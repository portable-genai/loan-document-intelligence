"""The CSP ``frame-ancestors`` allowlist resolved in THREE states, never two.

Red before the fix: ``_frame_ancestors`` read the variable as
``os.environ.get(name, "").strip() or _DEFAULT_FRAME_ANCESTORS``. The ``or`` collapses
"absent" and "present but empty" into one branch, so a deployment whose template rendered
``LOAN_DOC_FRAME_ANCESTORS`` empty (a Terraform variable resolving to nothing, a Cloud Run
env var declared with no value, a ``.env`` line left as ``VAR=``) booted happily and answered
``Content-Security-Policy: frame-ancestors 'self'`` plus ``X-Frame-Options: SAMEORIGIN`` :
byte-for-byte INDISTINGUISHABLE from never having set the variable at all. An operator who
deliberately empties the allowlist expressed an intent that names no parent; handing them the
unset default instead is reading an absence as consent, and it grants same-origin framing they
did not ask for with no signal that it happened.

Green after: unset keeps the documented restrictive default, set-and-empty is REFUSED at boot
(the resolver runs at import, so the process never starts and serves nothing at all), and
set-and-valid is used verbatim.

These tests assert the SHIPPED response headers from an app booted in a CHILD process, not the
helper's return value. ``_FRAME_ANCESTORS`` is resolved once at import into a module-level
constant, so a test that monkeypatches the environment and calls the helper proves only what
the helper computes, never what a browser receives. The child process is where the
module-level read really runs against the environment under test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

import pytest

from loan_doc_intel.api import app as app_module

_ENV = "LOAN_DOC_FRAME_ANCESTORS"

_HEADER_PROBE = """
import json
from fastapi.testclient import TestClient
from loan_doc_intel.api import app as app_module

response = TestClient(app_module.app, client=("127.0.0.1", 50000)).get("/healthz")
print(json.dumps({
    "csp": response.headers.get("Content-Security-Policy", ""),
    "xfo": response.headers.get("X-Frame-Options", ""),
    "nosniff": response.headers.get("X-Content-Type-Options", ""),
    "referrer": response.headers.get("Referrer-Policy", ""),
    "hsts": response.headers.get("Strict-Transport-Security", ""),
}))
"""


@dataclass(frozen=True)
class _Boot:
    """What a real boot of the service produced: either headers, or a refusal."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def booted(self) -> bool:
        return self.returncode == 0

    @property
    def headers(self) -> dict[str, str]:
        assert self.booted, f"the service refused to boot: {self.stderr}"
        return dict(json.loads(self.stdout.strip().splitlines()[-1]))


def _boot(value: str | None) -> _Boot:
    """Boot the app in a child process with ``LOAN_DOC_FRAME_ANCESTORS`` at ``value``."""
    env = dict(os.environ) | {"LOAN_DOC_PROFILE": "local"}
    if value is None:
        env.pop(_ENV, None)
    else:
        env[_ENV] = value
    completed = subprocess.run(
        [sys.executable, "-c", _HEADER_PROBE],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return _Boot(completed.returncode, completed.stdout, completed.stderr)


def test_unset_ships_the_documented_default() -> None:
    """No intent was expressed, so the documented restrictive default stands."""
    headers = _boot(None).headers
    assert headers["csp"] == "frame-ancestors 'self'"
    assert headers["xfo"] == "SAMEORIGIN"
    assert headers["nosniff"] == "nosniff"
    assert headers["referrer"] == "no-referrer"
    assert headers["hsts"] == ""


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_set_and_empty_refuses_at_boot_instead_of_serving_the_default(blank: str) -> None:
    """The load-bearing assertion: an emptied allowlist must NOT look like an unset one.

    Red before the three-state read: this boot succeeded and the response carried
    ``frame-ancestors 'self'`` + ``X-Frame-Options: SAMEORIGIN``, identical to
    :func:`test_unset_ships_the_documented_default`, so an operator who emptied the variable
    silently got same-origin framing. Green after: the process exits non-zero at import and
    serves NO response, which is the only outcome a two-state read cannot produce.
    """
    boot = _boot(blank)
    assert not boot.booted, (
        "the service booted with an emptied allowlist and served "
        f"{boot.stdout.strip()!r}: an emptied allowlist is being read as consent to the default"
    )
    assert _ENV in boot.stderr
    assert "ConfiguredEmptyError" in boot.stderr
    assert not boot.stdout.strip(), "a refused boot must not serve a response at all"


def test_set_and_valid_reaches_the_shipped_header() -> None:
    headers = _boot("https://portal.client.example https://admin.client.example").headers
    assert headers["csp"] == (
        "frame-ancestors https://portal.client.example https://admin.client.example"
    )
    # A named allowlist has no X-Frame-Options spelling: send none rather than one that
    # contradicts the CSP.
    assert headers["xfo"] == ""


def test_an_explicit_refusal_ships_both_halves_of_the_control() -> None:
    """``'none'`` is how an operator spells "nobody may frame this", and it is honoured."""
    headers = _boot("'none'").headers
    assert headers["csp"] == "frame-ancestors 'none'"
    assert headers["xfo"] == "DENY"


def test_the_shipped_directive_is_never_empty() -> None:
    """Whatever survives the resolver, a browser never receives a valueless directive.

    A valueless ``frame-ancestors`` is a CSP parse error, so the directive is discarded and no
    framing policy applies at all.
    """
    csp = _boot(None).headers["csp"]
    directive = csp.split("frame-ancestors", 1)[1].strip()
    assert directive, "an empty frame-ancestors directive is discarded by browsers"


def test_the_resolver_agrees_with_the_shipped_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-process cover for the three states, so a regression names the state it broke."""
    from hex_service_kit import ConfiguredEmptyError

    monkeypatch.delenv(_ENV, raising=False)
    assert app_module._frame_ancestors() == "'self'"

    monkeypatch.setenv(_ENV, "  https://portal.client.example  ")
    assert app_module._frame_ancestors() == "https://portal.client.example"

    monkeypatch.setenv(_ENV, "   ")
    with pytest.raises(ConfiguredEmptyError) as excinfo:
        app_module._frame_ancestors()
    assert _ENV in str(excinfo.value)


# The FOURTH state: a wildcard. The comment beside the variable said the allowlist is
# "never *" and nothing enforced it, so `LOAN_DOC_FRAME_ANCESTORS=*` shipped
# `frame-ancestors *`: any page on the internet could frame the UI and drive it as the
# signed-in user, and no X-Frame-Options backstop applies to a named allowlist either.


@pytest.mark.parametrize("value", ["*", "'self' https://*.client.example"])
def test_a_wildcard_allowlist_refuses_at_boot(value: str) -> None:
    """A wildcard is not an allowlist, and it must never reach a shipped header.

    Red before the refusal: both of these booted and served the wildcard verbatim. The
    partial form matters as much as the bare one, because ``https://*.client.example``
    trusts every subdomain including one an attacker managed to take.
    """
    boot = _boot(value)
    assert not boot.booted, f"the service booted with {value!r} and served {boot.stdout.strip()!r}"
    assert "wildcard" in boot.stderr
    assert not boot.stdout.strip(), "a refused boot must not serve a response at all"


def test_the_wildcard_refusal_leaves_the_other_three_states_alone() -> None:
    """The refusal adds ONE state. Unset, emptied and a named allowlist are unchanged."""
    assert _boot(None).headers["csp"] == "frame-ancestors 'self'"
    assert not _boot("").booted
    assert _boot("'none'").headers["csp"] == "frame-ancestors 'none'"
    assert _boot("https://portal.client.example").headers["csp"] == (
        "frame-ancestors https://portal.client.example"
    )
