"""Fail-closed network defaults (C5).

Binding 0.0.0.0 unconditionally, and a CORS fallback that trusts the dev origins in every
profile, are the two defects. Both reads go through the shared ``hex-service-kit`` rules;
these tests prove THIS repo's wiring.

The CSP ``frame-ancestors`` allowlist is the third read of the same shape and lives in
``test_frame_ancestors_three_state.py``, which proves it against the SHIPPED headers.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loan_doc_intel.api import app as app_module

REPO_ROOT = Path(__file__).resolve().parents[2]


def _origins_for_profile(
    monkeypatch: pytest.MonkeyPatch, profile: str, *, explicit: bool = True
) -> list[str]:
    import dataclasses

    monkeypatch.delenv("LOAN_DOC_CORS_ORIGINS", raising=False)
    settings = dataclasses.replace(
        app_module.deps.get_settings(), profile=profile, profile_explicit=explicit
    )
    monkeypatch.setattr(app_module.deps, "get_settings", lambda: settings)
    return app_module._cors_origins()


def test_cors_fallback_only_under_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _origins_for_profile(monkeypatch, "local") == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    # A secure deploy that forgets the allowlist gets NO cross-origin trust (was: dev
    # origins with credentials in every profile).
    assert _origins_for_profile(monkeypatch, "gcp") == []
    assert _origins_for_profile(monkeypatch, "platform") == []


def test_cors_fallback_needs_a_deliberate_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """An INHERITED local profile is not consent to trust localhost with credentials.

    Red before the three-state resolution: the dev-origin fallback keyed off the raw profile
    string, which an unset LOAN_DOC_PROFILE silently made ``local``.
    """
    assert _origins_for_profile(monkeypatch, "local", explicit=False) == []


def test_explicit_allowlist_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAN_DOC_CORS_ORIGINS", "https://tenant.example")
    assert app_module._cors_origins() == ["https://tenant.example"]


def test_local_profile_refuses_non_loopback_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    from hex_service_kit import InsecureBindError, resolve_bind_host

    monkeypatch.setenv("LOAN_DOC_API_HOST", "0.0.0.0")
    monkeypatch.delenv("LOAN_DOC_ALLOW_INSECURE_DEMO", raising=False)
    with pytest.raises(InsecureBindError):
        resolve_bind_host(
            "local",
            host_env="LOAN_DOC_API_HOST",
            insecure_demo_env="LOAN_DOC_ALLOW_INSECURE_DEMO",
        )


def test_api_still_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app_module.app, client=("127.0.0.1", 50000))
    response = client.get("/healthz")
    assert response.status_code == 200


# The wildcard refusal. `_cors_origins` delegates to `hex_service_kit.cors_allowlist`, whose
# docstring promises it "never returns *" while its set-and-valid branch returns exactly what
# the operator wrote. So `LOAN_DOC_CORS_ORIGINS=*` produced an allowlist of every origin, and
# the middleware is configured with allow_credentials, which is the combination that turns a
# cross-origin page into a session-riding client.


def test_a_wildcard_cors_allowlist_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAN_DOC_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="wildcard"):
        app_module._cors_origins()


def test_a_wildcard_hiding_inside_an_origin_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``https://*.example`` is an allowlist of every subdomain, including one an attacker took."""
    monkeypatch.setenv("LOAN_DOC_CORS_ORIGINS", "https://tenant.example,https://*.example")
    with pytest.raises(ValueError, match="wildcard"):
        app_module._cors_origins()


def test_a_legitimate_allowlist_and_the_emptied_state_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal adds ONE case; the states the three-state read already resolved stand."""
    monkeypatch.setenv("LOAN_DOC_CORS_ORIGINS", "")
    assert app_module._cors_origins() == []
    monkeypatch.setenv("LOAN_DOC_CORS_ORIGINS", "https://tenant.example, https://other.example")
    assert app_module._cors_origins() == ["https://tenant.example", "https://other.example"]


def test_a_wildcard_cors_allowlist_refuses_at_boot() -> None:
    """Importing the app must fail, so a wildcard cannot reach a running service at all.

    A refusal raised on the first cross-origin request would leave the misconfiguration live
    until traffic found it, and a health check would have called the deployment good.
    """
    completed = subprocess.run(
        [sys.executable, "-c", "import loan_doc_intel.api.app"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": "src",
            "LOAN_DOC_PROFILE": "local",
            "LOAN_DOC_CORS_ORIGINS": "*",
        },
        check=False,
        timeout=300,
    )
    assert completed.returncode != 0, "the app imported with LOAN_DOC_CORS_ORIGINS=*"
    assert "wildcard" in completed.stderr, completed.stderr
