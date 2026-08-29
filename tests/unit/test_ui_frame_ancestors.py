"""The console's document-layer CSP: one policy module, three-state framing, no second emitter.

The backend middleware only covers API responses. The document a browser actually parses and
executes is served by Next.js, so the console emits its own policy and that is the header that
governs framing of the page.

Two defects are pinned here, and they are different in kind.

1. **Three states, never two.** ``process.env.X || "'self'"`` collapses "absent" and "present but
   empty" into one branch, so a build whose template rendered ``NEXT_PUBLIC_FRAME_ANCESTORS``
   empty emitted ``frame-ancestors 'self'``, indistinguishable from never having configured it.
   An operator who empties the allowlist expressed an intent that names no parent, and reading
   that absence as consent is the bug. ``ui/lib/csp.mjs`` mirrors ``_frame_ancestors`` in
   ``src/loan_doc_intel/api/app.py`` so the two halves of the embedding posture cannot disagree.

2. **One emitter, not two.** The policy lives in ``ui/lib/csp.mjs`` and is emitted only by
   ``ui/proxy.ts``, because it carries a per-request script nonce a static ``headers()`` table
   cannot express. Building it inside ``ui/next.config.mjs`` as well is the defect. If
   ``next.config.mjs`` also emitted a ``Content-Security-Policy``, the browser would INTERSECT the
   two policies and the stricter one would win per directive, silently reinstating a nonce-less
   ``script-src`` under which the console renders as dead markup.

These assertions evaluate the SHIPPED modules in a real node process rather than a
re-implementation of their logic. What they cannot see is whether the served HTML actually carries
the nonce; only ``ui/scripts/assert-hydratable.mjs`` (wired into ``make ui-check``) can, because
the response header is byte-identical in the working and the broken case.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_ENV = "NEXT_PUBLIC_FRAME_ANCESTORS"
_UI = Path(__file__).resolve().parents[2] / "ui"
_CSP = _UI / "lib" / "csp.mjs"
_CONFIG = _UI / "next.config.mjs"

# csp.mjs imports nothing at all, so this needs no node_modules.
_PROBE = """
const mod = await import(process.argv[1]);
const env = process.env;
const ancestors = mod.frameAncestors(env);
console.log(JSON.stringify({
  frameAncestors: ancestors,
  frameOptions: mod.frameOptions(ancestors),
  csp: mod.contentSecurityPolicy(env, "test-nonce"),
}));
"""

# next.config.mjs reads app/layout.tsx from disk, so it is imported by URL from its own directory.
_CONFIG_PROBE = """
const mod = await import(process.argv[1]);
const rules = await mod.default.headers();
console.log(JSON.stringify(Object.fromEntries(rules[0].headers.map((h) => [h.key, h.value]))));
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@dataclass(frozen=True)
class _Load:
    """What loading the shipped module produced: either a resolved policy, or a refusal."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def loaded(self) -> bool:
        return self.returncode == 0

    @property
    def payload(self) -> dict[str, str]:
        assert self.loaded, f"the UI policy module refused to load: {self.stderr}"
        return dict(json.loads(self.stdout.strip().splitlines()[-1]))


def _run(probe: str, module: Path, value: str | None, node_env: str = "production") -> _Load:
    """Evaluate the shipped module under ``node``.

    ``node_env`` defaults to ``production`` because that is what a deployment runs and what
    every assertion about the exact policy string below is about. The module keys one
    deliberate relaxation off it (see :func:`test_the_dev_relaxations_exist_only_off_production`),
    and a probe that let the ambient environment decide would silently test whichever branch
    the runner happened to be on.
    """
    env = dict(os.environ)
    env["NODE_ENV"] = node_env
    if value is None:
        env.pop(_ENV, None)
    else:
        env[_ENV] = value
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", probe, module.as_uri()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return _Load(completed.returncode, completed.stdout, completed.stderr)


def _load(value: str | None, node_env: str = "production") -> _Load:
    """Resolve ``ui/lib/csp.mjs`` with ``NEXT_PUBLIC_FRAME_ANCESTORS`` at ``value``."""
    return _run(_PROBE, _CSP, value, node_env)


def _directives(csp: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for piece in csp.split(";"):
        name, _, value = piece.strip().partition(" ")
        if name:
            out[name.lower()] = value.strip()
    return out


def test_unset_ships_the_documented_default() -> None:
    payload = _load(None).payload
    assert payload["frameAncestors"] == "'self'"
    assert payload["frameOptions"] == "SAMEORIGIN"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_set_and_empty_refuses(blank: str) -> None:
    """An emptied allowlist must not be indistinguishable from an unset one.

    Red before: this resolved cleanly to the same ``'self'`` as
    :func:`test_unset_ships_the_documented_default`. ``next.config.mjs`` calls
    ``assertEmbedPolicyConfigured`` at module scope, which ``next build`` and ``next start`` both
    evaluate, so this refusal is a build/boot refusal rather than a surprise on a later request.
    """
    load = _load(blank)
    assert not load.loaded, (
        f"the policy resolved with an emptied allowlist to {load.stdout.strip()!r}: "
        "an emptied allowlist is being read as consent to the default"
    )
    assert _ENV in load.stderr


def test_set_and_valid_reaches_the_emitted_policy() -> None:
    payload = _load("https://portal.client.example").payload
    assert _directives(payload["csp"])["frame-ancestors"] == "https://portal.client.example"
    # A named allowlist has no X-Frame-Options spelling: emit none rather than a contradiction.
    assert payload["frameOptions"] == ""


def test_an_explicit_refusal_ships_both_halves_of_the_control() -> None:
    payload = _load("'none'").payload
    assert _directives(payload["csp"])["frame-ancestors"] == "'none'"
    assert payload["frameOptions"] == "DENY"


def test_the_document_policy_is_complete_and_nonce_bearing() -> None:
    """frame-ancestors alone is not a policy: nothing else constrained scripts, base or objects."""
    directives = _directives(_load(None).payload["csp"])
    for name in ("default-src", "script-src", "object-src", "base-uri", "frame-ancestors"):
        assert name in directives, f"the console CSP has no `{name}` directive at all"
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'self'"
    assert directives["script-src"] == "'self' 'nonce-test-nonce' 'strict-dynamic'"
    # An empty directive is a CSP parse error: browsers discard it and the restriction vanishes.
    assert all(value for value in directives.values()), f"an empty directive in {directives}"


def test_the_dev_relaxations_exist_only_off_production() -> None:
    """The third defect, and the one that made `npm run dev` serve a dead console.

    The policy above is correct and unservable by a development server: `next dev` compiles
    with `eval` and its HMR client opens a websocket back to itself, so the page rendered and
    React never attached. The module now adds `'unsafe-eval'` and `ws: wss:` when NODE_ENV is
    anything other than the exact literal ``production``, which is a branch a `next build`
    artefact cannot take. Both halves are asserted from the SHIPPED module in a real node
    process, because the whole point is what the deployed bytes emit.
    """
    production = _directives(_load(None, node_env="production").payload["csp"])
    assert "unsafe-eval" not in production["script-src"]
    assert "ws:" not in production["connect-src"]

    for value in ("development", "test"):
        development = _directives(_load(None, node_env=value).payload["csp"])
        assert "'unsafe-eval'" in development["script-src"], value
        assert "ws: wss:" in development["connect-src"], value
        # The relaxation is additive. The nonce still governs which scripts may run, and
        # 'unsafe-inline' never comes back on any branch.
        assert "'nonce-test-nonce' 'strict-dynamic'" in development["script-src"], value
        assert "unsafe-inline" not in development["script-src"], value


def test_next_config_emits_no_second_policy() -> None:
    """Two layers emitting a CSP means the browser intersects them and the stricter one wins."""
    headers = _run(_CONFIG_PROBE, _CONFIG, None).payload
    assert "Content-Security-Policy" not in headers, (
        "next.config.mjs emits a CSP as well as proxy.ts; the browser intersects the two and the "
        "stricter directive wins, which reinstates a nonce-less script-src"
    )
    assert "X-Frame-Options" not in headers
    assert headers["X-Content-Type-Options"] == "nosniff"


# The FOURTH state: a wildcard. The backend refuses one; without the same refusal here the two
# halves of one embedding posture would answer differently, and the console's header is the one
# a browser honours for the DOCUMENT, so the permissive half would be the one that governs.


@pytest.mark.parametrize("value", ["*", "'self' https://*.client.example"])
def test_a_wildcard_allowlist_refuses(value: str) -> None:
    """Red before: both resolved and were emitted verbatim into the document policy.

    ``frame-ancestors *`` lets any page on the internet frame the console and drive it as the
    signed-in user, and the partial form is no better: ``https://*.client.example`` trusts every
    subdomain including one an attacker managed to take.
    """
    load = _load(value)
    assert not load.loaded, f"the policy resolved {value!r} to {load.stdout.strip()!r}"
    assert "wildcard" in load.stderr


def test_the_wildcard_refusal_leaves_the_other_three_states_alone() -> None:
    """The refusal adds ONE state. Unset, emptied and a named allowlist are unchanged."""
    assert _load(None).payload["frameAncestors"] == "'self'"
    assert not _load("").loaded
    assert _load("'none'").payload["frameAncestors"] == "'none'"
    assert _load("https://portal.client.example").payload["frameAncestors"] == (
        "https://portal.client.example"
    )
