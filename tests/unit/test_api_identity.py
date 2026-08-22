"""API-level tests for server-verified identity on the FastAPI surface.

These prove the identity seam end to end through the HTTP boundary:

* an unknown ``X-Dev-Persona`` is a hard 401 (the IdentityPort raised ``IdentityError``),
* with no persona header the DEFAULT seeded persona becomes the audit actor, and
* a selected persona becomes the audit actor,

so the audit trail's actor is the SERVER-verified subject, never a client-asserted body
``actor`` (there is no such field on the request schema any more).

``deps.get_container`` is ``lru_cache``d, so we monkeypatch it to inject an in-memory
container (a ``:memory:`` local audit store) rather than mutating global env / cache state.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from loan_doc_intel.api import deps
from loan_doc_intel.api.app import app
from loan_doc_intel.config import Container, LocalSettings, Settings

_SAMPLE_BODY = {
    "application": {
        "id": "app-fictional-0001",
        "name": "Jordan Tester Fictional",
        "address": "123 Imaginary Road, Singapore 000000",
        "declared_income": {
            "source_doc_id": "declared",
            "amount": 6500.0,
            "currency": "SGD",
            "period": "monthly",
            "kind": "salary",
        },
    },
    "documents": [
        {"id": "doc-payslip-2026-04", "doc_type": "payslip", "uri": "local://app-0001/payslip.txt"},
        {
            "id": "doc-bank-2026-04",
            "doc_type": "bank_statement",
            "uri": "local://app-0001/bank.txt",
        },
    ],
}


def _local_settings() -> Settings:
    """Local settings with the full adapter map from settings.yaml, in-memory audit store."""
    base = Settings.load("config/settings.yaml")
    return Settings(
        project_id=base.project_id,
        region=base.region,
        profile="local",
        kms_key=base.kms_key,
        models=base.models,
        document_ai=base.document_ai,
        model_armor=base.model_armor,
        dlp=base.dlp,
        logging=base.logging,
        agent_engine=base.agent_engine,
        validation=base.validation,
        local=LocalSettings(audit_path=":memory:"),
        adapters=base.adapters,
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient backed by an in-memory local container (shared across the request)."""
    container = Container(_local_settings())
    monkeypatch.setattr(deps, "get_container", lambda: container)
    # The service factory reads the same (patched) container.
    monkeypatch.setattr(
        deps, "get_loan_doc_service", lambda: deps.build_loan_doc_service(container)
    )
    client = TestClient(app, client=("127.0.0.1", 50000))
    client._container = container  # type: ignore[attr-defined]  # for audit read-back
    return client


def _audit_actors(client: TestClient) -> list[str]:
    container: Container = client._container  # type: ignore[attr-defined]
    return [event["actor"] for event in container.audit.read_all()]


def test_unknown_dev_persona_is_401(client: TestClient) -> None:
    response = client.post(
        "/v1/process", json=_SAMPLE_BODY, headers={"X-Dev-Persona": "does-not-exist"}
    )
    assert response.status_code == 401


def test_default_persona_is_the_audit_actor(client: TestClient) -> None:
    response = client.post("/v1/process", json=_SAMPLE_BODY)
    assert response.status_code == 200
    actors = _audit_actors(client)
    assert actors, "the process pipeline must have written an audit event"
    # No persona header -> the first seeded persona is the verified subject / audit actor.
    assert all(actor == "demo.analyst@bank.example" for actor in actors)


def test_selected_persona_is_the_audit_actor(client: TestClient) -> None:
    response = client.post("/v1/process", json=_SAMPLE_BODY, headers={"X-Dev-Persona": "approver"})
    assert response.status_code == 200
    actors = _audit_actors(client)
    assert actors and all(actor == "demo.approver@bank.example" for actor in actors)


def test_personas_route_lists_seeded_personas(client: TestClient) -> None:
    response = client.get("/v1/personas")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert {"analyst", "approver", "auditor", "other-tenant"} <= ids


# --------------------------------------------------------------------------- #
# Object-level authorization (C2): the application in the body is owned server-side
# by tenant `demo-bank`; a cross-tenant caller is denied 403 and nothing is processed.
# --------------------------------------------------------------------------- #
def test_cross_tenant_persona_is_denied_403_with_no_side_effects(client: TestClient) -> None:
    """The seeded `other-bank` persona may authenticate but is NOT entitled to a
    `demo-bank`-owned application: the request is refused 403 before any extraction,
    validation or audit, so no case is produced and no audit event is written."""
    response = client.post(
        "/v1/process", json=_SAMPLE_BODY, headers={"X-Dev-Persona": "other-tenant"}
    )
    assert response.status_code == 403
    # Fail-closed: a denied request must not process the object at all.
    assert _audit_actors(client) == [], "a denied request must write no audit event"


def test_entitled_analyst_persona_is_allowed_200(client: TestClient) -> None:
    """The seeded `demo-bank` analyst IS entitled to the `demo-bank`-owned application."""
    response = client.post("/v1/process", json=_SAMPLE_BODY, headers={"X-Dev-Persona": "analyst"})
    assert response.status_code == 200
    actors = _audit_actors(client)
    assert actors and all(actor == "demo.analyst@bank.example" for actor in actors)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
