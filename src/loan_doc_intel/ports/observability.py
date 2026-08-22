"""Observability ports : the A5 (audit/trace) and A4 (eval gate) concerns (R2, R5).

Primary GCP adapters: **Cloud Logging locked WORM bucket** for immutable audit, **Cloud
Trace via OpenTelemetry** for pipeline traces (message content capture OFF so applicant
PII never reaches a span), and the **Gen AI evaluation service** for the promotion gate
(extraction accuracy, validation recall/precision, PII safety).

Two of the three ports below are RE-EXPORTS, not declarations, and that is the point of this
module. ``ObservabilityTracerPort`` and ``EvaluationGatePort`` were hand-copied into sixteen
repositories, and by the time anyone compared them they disagreed: one had dropped the eval port
entirely, two had dropped its ``gate`` method (the half that can actually refuse a promotion),
one returned ``str`` from an audit ``record`` that returns ``None`` everywhere else. A Protocol
copied into N repos is N Protocols, and only one of them gets fixed when a defect is found. So
they are imported from the commons that own them and this module adds nothing to them.

``AuditSinkPort`` stays declared here on purpose: it is typed in this repo's own vocabulary
(:class:`~loan_doc_intel.domain.models.AuditEvent`), so it is not a shared shape at all.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_eval_kit import EvaluationGatePort
from hex_service_kit.observability import ObservabilityTracerPort, TokenUsage

from ..domain.models import AuditEvent


@runtime_checkable
class AuditSinkPort(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Write an immutable, already-redacted audit record (WORM)."""
        ...


__all__ = [
    "AuditSinkPort",
    "EvaluationGatePort",
    "ObservabilityTracerPort",
    "TokenUsage",
]
