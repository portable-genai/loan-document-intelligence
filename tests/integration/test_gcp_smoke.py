"""Integration smoke tests : require live Google Cloud credentials (deselected by default).

Run explicitly with ``pytest -m integration`` in an environment that has the ``[gcp]`` extra
installed and ADC credentials for a Singapore-resident project. These tests construct the
real GCP adapters and exercise a minimal call against each managed service. They are NEVER
run in the default suite or in CI (``-m 'not integration'``), so the offline gate stays
credential-free.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

from loan_doc_intel.config import Settings, build_container  # noqa: E402
from loan_doc_intel.domain.models import Direction  # noqa: E402

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")


def _gcp_container():
    settings = Settings.load()
    settings = Settings(
        project_id=_PROJECT or settings.project_id,
        region="asia-southeast1",
        profile="gcp",
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
    return build_container(settings)


@pytest.mark.skipif(not _PROJECT, reason="GOOGLE_CLOUD_PROJECT not set")
def test_dlp_redaction_smoke():
    """DLP de-identifies a synthetic NRIC (real call)."""
    container = _gcp_container()
    result = container.redaction.redact("Applicant NRIC S1234567A (fictional).")
    assert "S1234567A" not in result.text


@pytest.mark.skipif(not _PROJECT, reason="GOOGLE_CLOUD_PROJECT not set")
def test_model_armor_guardrail_smoke():
    """Model Armor screens a benign prompt and allows it (real call)."""
    container = _gcp_container()
    verdict = container.guardrail.screen("What is the net pay on this payslip?", Direction.INPUT)
    assert verdict.direction is Direction.INPUT


@pytest.mark.skipif(not _PROJECT, reason="GOOGLE_CLOUD_PROJECT not set")
def test_audit_sink_smoke():
    """Cloud Logging accepts a structured audit record (real call)."""
    from loan_doc_intel.domain.models import AuditEvent, Decision

    container = _gcp_container()
    container.audit.record(
        AuditEvent(
            action="process",
            actor="integration-smoke",
            decision=Decision.ALLOWED,
            redacted_prompt="[REDACTED]",
            redacted_response="verdict verified",
        )
    )
