"""Both origin allowlists refuse EVERY spelling of a wildcard, not only the ones with an asterisk.

``LOAN_DOC_CORS_ORIGINS`` decides who may call this service from a browser (with credentials,
because the middleware sets ``allow_credentials=True``) and ``LOAN_DOC_FRAME_ANCESTORS`` decides
who may frame its UI. A wildcard in either is the whole origin policy switched off.

The asterisk half of the rule already existed here. The exact-token half did not, so ``null``
was ACCEPTED by both lists and travelled verbatim into
``CORSMiddleware(allow_origins=["null"])`` and ``Content-Security-Policy: frame-ancestors null``.
That is not a typo case. A sandboxed iframe presents ``null`` as its origin, so the accepted
value hands the UI to any page that can sandbox one, which is the framing this policy exists to
refuse, and it does so through the one spelling the asterisk test structurally cannot see.

``ui/lib/csp.mjs`` has enforced the union (``WILDCARD_TOKENS.has(token) || token.includes("*")``)
for the document a browser actually frames since the UI half of this sweep landed, so until now
the two surfaces of the same product disagreed about what an origin policy may hold. They agree
again after this file.

The spellings that were already refused are pinned here too, and pinned against the WILDCARD
message specifically. Several of them were turned away incidentally, by an origin validator or
by a parse rule rather than by the rule that is supposed to own them, and incidental coverage
moves the day that other rule moves.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hex_service_kit.netdefaults import ConfiguredEmptyError

from loan_doc_intel.api import app as app_module
from loan_doc_intel.api.app import (
    _CORS_ORIGINS_ENV,
    _FRAME_ANCESTORS_ENV,
    _WILDCARD_TOKENS,
    _cors_origins,
    _frame_ancestors,
)

_ROOT = Path(__file__).resolve().parents[2]

#: Every spelling an operator could reach, asterisk-bearing and not. ``null`` and ``'*'`` are the
#: two that carry no bare asterisk of their own; the host-source forms are the ones a CSP
#: ``frame-ancestors`` directive honours as "every subdomain", including one obtained by takeover.
_WILDCARD_SPELLINGS = ["*", "'*'", "null", "*.*", "https://*.example", "*.example", "https://*"]


def _boot(**overrides: str) -> subprocess.CompletedProcess[str]:
    """Import the API module in a fresh interpreter, the way uvicorn does at start-up."""
    env = dict(os.environ)
    env.pop(_CORS_ORIGINS_ENV, None)
    env.pop(_FRAME_ANCESTORS_ENV, None)
    env["LOAN_DOC_PROFILE"] = "local"
    env.update(overrides)
    env["PYTHONPATH"] = os.pathsep.join([str(_ROOT / "src"), env.get("PYTHONPATH", "")])
    # S603: the argv is this interpreter and a literal written here, never caller input.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import loan_doc_intel.api.app"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


# --------------------------------------------------------------------------- #
# The refusal, on both lists, for every spelling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", _WILDCARD_SPELLINGS)
def test_a_wildcard_frame_ancestor_is_refused(
    spelling: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FRAME_ANCESTORS_ENV, spelling)
    with pytest.raises(ValueError, match="wildcard"):
        _frame_ancestors()


@pytest.mark.parametrize("spelling", _WILDCARD_SPELLINGS)
def test_a_wildcard_cors_origin_is_refused(spelling: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_CORS_ORIGINS_ENV, spelling)
    with pytest.raises(ValueError, match="wildcard"):
        _cors_origins()


@pytest.mark.parametrize("spelling", _WILDCARD_SPELLINGS)
def test_a_wildcard_hidden_among_real_origins_is_refused(
    spelling: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dangerous shape in practice: an allowlist that looks specific and is not.

    An operator reviewing a config template sees the named origins and stops reading. One
    permissive entry anywhere in the list widens the whole policy to everybody.
    """
    monkeypatch.setenv(_FRAME_ANCESTORS_ENV, f"'self' https://portal.demo-bank.example {spelling}")
    with pytest.raises(ValueError, match="wildcard"):
        _frame_ancestors()
    monkeypatch.setenv(_CORS_ORIGINS_ENV, f"https://portal.demo-bank.example,{spelling}")
    with pytest.raises(ValueError, match="wildcard"):
        _cors_origins()


@pytest.mark.parametrize("variable", [_FRAME_ANCESTORS_ENV, _CORS_ORIGINS_ENV])
def test_the_null_origin_refuses_at_boot_and_not_on_a_later_request(variable: str) -> None:
    """``null`` is the spelling this change closes, so prove it stops a real start-up.

    uvicorn imports this module at start-up, which is the moment an operator whose config
    template rendered ``null`` can still act on it. A refusal that only fired once a browser
    somewhere presented a sandboxed origin would be discovered by the attacker first.
    """
    result = _boot(**{variable: "null"})
    assert result.returncode != 0, f"{variable}=null must refuse to boot"
    assert variable in result.stderr
    assert "wildcard" in result.stderr


def test_the_exact_token_half_names_the_behavioural_wildcards() -> None:
    """The asterisk test cannot see these, which is exactly why the set has to exist."""
    assert sorted(_WILDCARD_TOKENS) == ["'*'", "*", "*.*", "null"]
    assert "null" in _WILDCARD_TOKENS and "*" not in "null"


def test_the_console_document_policy_refuses_the_same_spellings() -> None:
    """The CSP a browser enforces for the UI page comes from the UI, not from here.

    Closing only the API would leave the more directly exploitable surface open: the UI document
    is served by Next.js and never passes through this middleware. The behavioural half lives in
    the UI's own tests; this is the drift guard the Python gate can run, since the gate does not
    shell out to node.
    """
    module = (_ROOT / "ui" / "lib" / "csp.mjs").read_text(encoding="utf-8")
    for spelling in sorted(_WILDCARD_TOKENS):
        assert f'"{spelling}"' in module, f"the UI policy does not name {spelling}"
    assert 'includes("*")' in module


# --------------------------------------------------------------------------- #
# What must NOT change
# --------------------------------------------------------------------------- #
def test_a_legitimate_allowlist_still_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal that also turns away valid configuration is an outage, not a control.

    The two shapes most likely to trip a careless rule are covered deliberately: an explicit
    PORT (the colon and digits a naive "looks odd" check might reject) and a HYPHENATED host
    label, which is legal in DNS and common in tenant-specific origins.
    """
    named = "https://portal.demo-bank.example:8443 https://a-b-c.demo.example"
    monkeypatch.setenv(_FRAME_ANCESTORS_ENV, named)
    assert _frame_ancestors() == named

    monkeypatch.setenv(
        _CORS_ORIGINS_ENV, "https://console.demo-bank.example:8443,https://a-b-c.demo.example"
    )
    assert _cors_origins() == [
        "https://console.demo-bank.example:8443",
        "https://a-b-c.demo.example",
    ]

    result = _boot(**{_FRAME_ANCESTORS_ENV: named})
    assert result.returncode == 0, result.stderr


def test_a_host_containing_the_word_null_is_not_a_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact matching, not substring: ``null`` is a token, and hosts may legitimately spell it."""
    monkeypatch.setenv(_FRAME_ANCESTORS_ENV, "https://nullify.demo-bank.example")
    assert _frame_ancestors() == "https://nullify.demo-bank.example"


def test_the_unset_and_emptied_states_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the wildcard case is new; the states this repo already resolved must hold exactly.

    Unset frame-ancestors keeps the shipped ``'self'``, and an EMPTIED one still REFUSES the
    boot with its own ConfiguredEmptyError. That refusal is a different one from this change's,
    and it must not be swallowed by it; it is also this repo's own answer, different from the
    fleet siblings that map an emptied value to ``'none'``, so it is pinned rather than assumed.
    Unset CORS keeps the local dev fallback and emptied still denies every origin.
    """
    monkeypatch.delenv(_FRAME_ANCESTORS_ENV, raising=False)
    assert _frame_ancestors() == "'self'"
    for blank in ("", "   ", "\t "):
        monkeypatch.setenv(_FRAME_ANCESTORS_ENV, blank)
        with pytest.raises(ConfiguredEmptyError, match=_FRAME_ANCESTORS_ENV):
            _frame_ancestors()

    # The unset branch keys off exposure_profile, which is process-wide state, so the profile
    # is named here rather than inherited from whatever built the container first.
    settings = dataclasses.replace(
        app_module.deps.get_settings(), profile="local", profile_explicit=True
    )
    monkeypatch.setattr(app_module.deps, "get_settings", lambda: settings)
    monkeypatch.delenv(_CORS_ORIGINS_ENV, raising=False)
    assert _cors_origins() == ["http://localhost:3000", "http://127.0.0.1:3000"]
    monkeypatch.setenv(_CORS_ORIGINS_ENV, "")
    assert _cors_origins() == []


def test_a_total_lockdown_is_still_expressible(monkeypatch: pytest.MonkeyPatch) -> None:
    """``'none'`` forbids all framing, and refusing a wildcard must not take it away."""
    monkeypatch.setenv(_FRAME_ANCESTORS_ENV, "'none'")
    assert _frame_ancestors() == "'none'"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
