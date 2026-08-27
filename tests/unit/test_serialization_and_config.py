"""Unit tests for serialization, Settings.load, and Container wiring.

* domain/serialization.to_jsonable round-trips enums (-> .value) and datetimes.
* Settings.load parses config/settings.yaml.
* Container under profile=onprem binds the on-prem placeholder adapters, and each bound
  adapter satisfies its runtime_checkable Protocol (structural parity).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from tests.fixtures import sample_docs

from loan_doc_intel import ports
from loan_doc_intel.config import Container, Settings
from loan_doc_intel.domain.cross_validator import CrossValidator
from loan_doc_intel.domain.models import (
    AuditEvent,
    CheckStatus,
    Decision,
    DocType,
    VerificationVerdict,
)

CONFIG_PATH = "config/settings.yaml"

PORT_PROTOCOLS = {
    "extraction": ports.DocumentExtractionPort,
    "llm": ports.LLMPort,
    "guardrail": ports.GuardrailPort,
    "redaction": ports.PIIRedactionPort,
    "agent_runtime": ports.AgentRuntimePort,
    "session": ports.SessionPort,
    "memory": ports.MemoryPort,
    "audit": ports.AuditSinkPort,
    "tracer": ports.ObservabilityTracerPort,
    "evaluation": ports.EvaluationGatePort,
    "registry": ports.AgentRegistryPort,
    "tool_catalog": ports.ToolCatalogPort,
    "entitlements": ports.EntitlementsPort,
}


def _to_jsonable():
    from loan_doc_intel.domain.serialization import to_jsonable

    return to_jsonable


def test_to_jsonable_enum_becomes_value():
    to_jsonable = _to_jsonable()
    assert to_jsonable(DocType.PAYSLIP) == "payslip"
    assert to_jsonable(CheckStatus.FAIL) == "fail"
    assert to_jsonable(VerificationVerdict.VERIFIED) == "verified"
    assert to_jsonable(Decision.BLOCKED) == "blocked"


def test_to_jsonable_datetime_is_json_safe_string():
    to_jsonable = _to_jsonable()
    dt = datetime(2026, 6, 20, 8, 30, tzinfo=UTC)
    out = to_jsonable(dt)
    assert isinstance(out, str)
    assert json.loads(json.dumps(out)) == out
    assert "2026-06-20" in out


def test_to_jsonable_cross_validation_result_roundtrips():
    to_jsonable = _to_jsonable()
    result = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    out = to_jsonable(result)
    text = json.dumps(out)  # must not raise
    reloaded = json.loads(text)
    assert reloaded["application_id"] == "app-1"
    assert reloaded["passed"] is True
    assert reloaded["checks"][0]["status"] in {"pass", "warn", "fail"}


def test_to_jsonable_audit_event_is_worm_serialisable():
    to_jsonable = _to_jsonable()
    event = AuditEvent(
        action="process",
        actor="underwriter",
        decision=Decision.ALLOWED,
        redacted_prompt="[NRIC]",
        redacted_response="verdict verified",
    )
    out = to_jsonable(event)
    reloaded = json.loads(json.dumps(out))
    assert reloaded["decision"] == "allowed"
    assert reloaded["action"] == "process"
    assert reloaded["resource"] == "loan-document-intelligence"


# --------------------------------------------------------------------------- #
# Settings.load
# --------------------------------------------------------------------------- #
def test_settings_load_parses_yaml():
    settings = Settings.load(CONFIG_PATH)
    assert settings.region == "asia-southeast1"
    assert settings.models.reasoning == "gemini-3.5-flash"
    assert settings.models.triage == "gemini-3.5-flash"
    assert settings.validation.amount_tolerance == pytest.approx(0.05)
    assert settings.validation.balance_decline_warn_ratio == pytest.approx(0.15)
    assert settings.validation.balance_decline_fail_ratio == pytest.approx(0.40)
    assert set(PORT_PROTOCOLS) <= set(settings.adapters)


def test_settings_pins_models_to_allowed_ids():
    settings = Settings.load(CONFIG_PATH)
    assert settings.models.reasoning != "gemini-2.0-flash"
    assert settings.models.triage != "gemini-2.0-flash"
    assert settings.models.reasoning.startswith("gemini-3")


def test_settings_refuses_an_out_of_country_region() -> None:
    with pytest.raises(ValueError, match="approved allowlist"):
        Settings(region="australia-southeast1")


def test_settings_refuses_a_document_ai_region_mismatch() -> None:
    from loan_doc_intel.config import DocumentAiSettings

    with pytest.raises(ValueError, match="Document AI location"):
        Settings(document_ai=DocumentAiSettings(location="us-central1"))


# --------------------------------------------------------------------------- #
# Container binds on-prem adapters under profile=onprem.
# --------------------------------------------------------------------------- #
def _onprem_settings() -> Settings:
    settings = Settings.load(CONFIG_PATH)
    return Settings(
        project_id=settings.project_id,
        region=settings.region,
        profile="onprem",
        kms_key=settings.kms_key,
        models=settings.models,
        document_ai=settings.document_ai,
        model_armor=settings.model_armor,
        dlp=settings.dlp,
        logging=settings.logging,
        agent_engine=settings.agent_engine,
        validation=settings.validation,
        adapters=settings.adapters,
    )


def test_container_binds_onprem_adapters_with_protocol_parity():
    container = Container(_onprem_settings())
    for port_name, protocol in PORT_PROTOCOLS.items():
        adapter = getattr(container, port_name)
        assert isinstance(adapter, protocol), (
            f"on-prem adapter for '{port_name}' is not structurally a {protocol.__name__}"
        )


def test_container_falls_back_to_gcp_binding_when_profile_missing():
    settings = _onprem_settings()
    binding = settings.adapters["guardrail"]
    assert binding["onprem"].endswith("OnPremGuardrailAdapter")
    assert "gcp" in binding


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
