"""The loopback bound is a property of the APP OBJECT, not of ``main()``.

The defect this guards is invisible to a test that only calls ``main()`` or only reads
``resolve_bind_host``: the Dockerfile CMD is ``uvicorn loan_doc_intel.api.app:app --host
0.0.0.0 --port ${PORT}``, so a bound that lives only in ``main()`` never runs in a shipped
process. Until this file existed, ``LOAN_DOC_PROFILE=local`` served the seeded persona list,
groups and tenant included, to any peer that could reach the port, and a LAN peer could then
act as the loan approver by naming that persona in a header.

The second half is WHAT the guard is derived from. It must not be a service credential:
``S2S_TOKEN`` authenticates a calling SERVICE and no end user, so setting one is not
evidence that ``/v1/personas`` is protected; a guard derived from it switches OFF for exactly
the end-user routes it was protecting. The guard therefore reads the identity BINDING
(``ports/identity.py``), and the token cells below are the standing proof of that.

The control at the bottom keeps the other cells from being true for a boring reason: a
VERIFYING binding must stand the guard DOWN, or "everything refuses" would just mean the guard
is stuck on.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

#: A peer on the LAN. RFC 5737 documentation address: no real host, and obviously fictional.
LAN_PEER = "192.0.2.50"

#: What a genuine dev run looks like to the guard.
LOOPBACK_PEER = "127.0.0.1"

_ENV = (
    "LOAN_DOC_PROFILE",
    "S2S_TOKEN",
    "LOAN_DOC_ALLOW_INSECURE_DEMO",
    "LOAN_DOC_IAP_AUDIENCE",
)


def _app_under(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> Any:
    """Re-import the API module under a scrubbed environment and return its app object.

    The posture is resolved at IMPORT (the guard rides the app object), so an app built under
    the ambient environment would prove nothing about any other. Every variable this module
    reads is cleared first, so a cell that omits one is testing the absent state rather than
    inheriting the developer's shell.
    """
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        if value is not None:
            monkeypatch.setenv(name, value)

    from loan_doc_intel.api import deps

    deps.get_container.cache_clear()
    module = importlib.import_module("loan_doc_intel.api.app")
    return importlib.reload(module).app


def _status(app: Any, path: str, peer: str) -> int:
    with TestClient(app, client=(peer, 50000)) as client:
        return client.get(path, headers={"X-Dev-Persona": "approver"}).status_code


# Every route the app serves that answers without a credential, including the two that need no
# identity at all: a deployment that can authenticate nobody has no business answering a
# stranger even about its own health.
UNCREDENTIALED_ROUTES = ("/healthz", "/v1/personas", "/.well-known/agent-card.json")


@pytest.mark.parametrize("path", UNCREDENTIALED_ROUTES)
@pytest.mark.parametrize(
    ("label", "env"),
    [
        # The cell this file was written for: an ordinary deployment shape, not a
        # misconfiguration. Setting a service credential must not unbound the end-user routes.
        ("local chosen, S2S token SET", {"LOAN_DOC_PROFILE": "local", "S2S_TOKEN": "s3cret"}),
        ("local chosen, no token", {"LOAN_DOC_PROFILE": "local"}),
        # Unset is not consent: no profile means no identity scheme was chosen at all.
        ("profile UNSET, token SET", {"S2S_TOKEN": "s3cret"}),
        # The on-premises placeholder resolves nobody until a client binds their own IdP.
        ("onprem placeholder binding", {"LOAN_DOC_PROFILE": "onprem"}),
    ],
)
def test_a_posture_that_authenticates_no_end_user_refuses_a_lan_peer(
    monkeypatch: pytest.MonkeyPatch, label: str, env: dict[str, str], path: str
) -> None:
    app = _app_under(monkeypatch, **env)
    assert _status(app, path, LAN_PEER) == 503, f"{label}: {path} answered a LAN peer"


@pytest.mark.parametrize("path", UNCREDENTIALED_ROUTES)
def test_the_same_posture_still_serves_a_loopback_peer(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """The offline demo is the whole point of the local profile and must not regress."""
    app = _app_under(monkeypatch, LOAN_DOC_PROFILE="local")
    assert _status(app, path, LOOPBACK_PEER) == 200


def test_the_local_profile_still_serves_its_seeded_personas_on_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal must be about the PEER, not about the personas having gone away."""
    app = _app_under(monkeypatch, LOAN_DOC_PROFILE="local")
    with TestClient(app, client=(LOOPBACK_PEER, 50000)) as client:
        response = client.get("/v1/personas")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == [
        "analyst",
        "approver",
        "auditor",
        "other-tenant",
    ]


def test_a_verifying_binding_stands_the_guard_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: without this, "everything refuses" would just mean the guard is stuck on.

    A fronted deployment (IAP verifies the assertion before the request arrives) must stay
    reachable and health-checkable off loopback. That it does NOT leak a seeded identity is
    the separate assertion below: ``/v1/personas`` is empty outside the persona binding.
    """
    app = _app_under(
        monkeypatch,
        LOAN_DOC_PROFILE="gcp",
        LOAN_DOC_IAP_AUDIENCE="/projects/000/global/backendServices/000",
        S2S_TOKEN="s3cret",
    )
    assert _status(app, "/healthz", LAN_PEER) == 200

    with TestClient(app, client=(LAN_PEER, 50000)) as client:
        response = client.get("/v1/personas", headers={"X-Dev-Persona": "approver"})
    assert response.status_code == 200
    assert response.json() == [], "a verifying binding must publish no seeded identities"


def test_the_opt_out_is_the_documented_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOAN_DOC_ALLOW_INSECURE_DEMO=1 is the single acknowledged exposure, as at bind time.

    An operator who deliberately demos off loopback keeps the escape hatch the bind guard has
    always honoured, so the guard adds no new way to get stuck.
    """
    app = _app_under(
        monkeypatch,
        LOAN_DOC_PROFILE="local",
        LOAN_DOC_ALLOW_INSECURE_DEMO="1",
    )
    assert _status(app, "/v1/personas", LAN_PEER) == 200


def test_the_guard_is_not_derived_from_the_service_credential() -> None:
    """A drift guard: no service-credential variable may appear in the posture derivation.

    Asserting the BEHAVIOUR above is the real test; this asserts the SHAPE, so a future
    refactor that reintroduces a token into this expression fails here with the reason spelled
    out rather than silently widening the exposure again.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "loan_doc_intel" / "api" / "app.py"
    text = source.read_text(encoding="utf-8")
    # Parsed rather than line-scanned: `ruff format` collapses or explodes this expression
    # depending on how long the names are, and a guard anchored to a closing bracket in
    # column 0 silently starts reading the wrong statement when that happens.
    tree = ast.parse(text)
    derivation = next(
        ast.get_source_segment(text, node.value) or ""
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_END_USER_AUTHENTICATED" for t in node.targets)
    )
    assert "S2S_TOKEN" not in derivation, (
        "the exposure guard is being derived from the service-to-service credential again; "
        "that secret authenticates a calling SERVICE and no end user, so it cannot speak for "
        "the end-user routes. Derive it from the identity binding (ports/identity.py)."
    )
