"""Cloud Logging WORM audit adapter : immutable audit trail for system B5.

Backs the domain ``AuditSinkPort`` with **Cloud Logging**. Each ``AuditEvent`` is written
as a structured log entry; a Cloud Logging **sink** (provisioned in Terraform) routes the
``loan-document-intelligence-audit`` log to a **locked log bucket** (WORM, ~7-year retention), so
records are write-once and immutable : the regulator-grade audit guarantee (A5, P-07).

Important: ``redacted_prompt`` / ``redacted_response`` arrive **already de-identified** by
the DLP redaction adapter upstream in the pipeline. This sink does not redact; it only
serialises and writes. No applicant PII should ever reach this code path.

The Cloud Logging SDK import is lazy so the on-prem and test profiles import without it.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import AuditEvent, Decision

_SEVERITY_BY_DECISION: dict[Decision, str] = {
    Decision.ALLOWED: "INFO",
    Decision.ESCALATED: "WARNING",
    Decision.BLOCKED: "WARNING",
}


class CloudLoggingAuditAdapter:
    """Write already-redacted ``AuditEvent`` records to the locked WORM log bucket."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log_name = settings.logging.log_name
        self._client: Any | None = None
        self._logger: Any | None = None

    def _get_logger(self) -> Any:
        """Return (and cache) the Cloud Logging logger for the audit log name."""
        if self._logger is not None:
            return self._logger
        from google.cloud import logging_v2  # lazy: Cloud Logging SDK only on gcp

        # verify: https://cloud.google.com/logging/docs/reference/libraries
        self._client = logging_v2.Client(project=self._settings.project_id)
        self._logger = self._client.logger(self._log_name)
        return self._logger

    def record(self, event: AuditEvent) -> None:
        """Serialise and write one immutable audit record (routed to WORM by a sink)."""
        from ...domain.serialization import to_jsonable  # reuse domain serializer

        logger = self._get_logger()
        payload = to_jsonable(event)
        severity = _SEVERITY_BY_DECISION.get(event.decision, "INFO")
        labels = {
            "action": event.action,
            "actor": event.actor,
            "decision": event.decision.value,
            "resource": event.resource,
        }
        if event.trace_id:
            labels["trace_id"] = event.trace_id
        logger.log_struct(payload, severity=severity, labels=labels)
