"""The local audit store is hash-chained and tamper-evident (C9)."""

from __future__ import annotations

from loan_doc_intel.adapters.local.audit import LocalAppendOnlyAuditAdapter
from loan_doc_intel.config import LocalSettings, Settings
from loan_doc_intel.domain.models import AuditEvent, Decision


def _event(action: str) -> AuditEvent:
    return AuditEvent(
        action=action,
        actor="eval-bot (FICTIONAL)",
        decision=Decision.ALLOWED,
        redacted_prompt="[REDACTED] example request",
        redacted_response="[REDACTED] example response",
    )


def _adapter() -> LocalAppendOnlyAuditAdapter:
    settings = Settings(profile="local", local=LocalSettings(audit_path=":memory:"))
    return LocalAppendOnlyAuditAdapter(settings)


def test_events_round_trip_and_chain_verifies() -> None:
    adapter = _adapter()
    adapter.record(_event("process"))
    adapter.record(_event("extract"))
    events = adapter.read_all()
    assert [e["action"] for e in events] == ["process", "extract"]
    report = adapter.verify_chain()
    assert report.ok and report.chained == 2


def test_tampering_is_detected() -> None:
    adapter = _adapter()
    adapter.record(_event("process"))
    adapter.record(_event("extract"))
    conn = adapter._log._conn  # noqa: SLF001 - deliberate tamper simulation
    conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    conn.execute(
        "UPDATE audit_log SET event_json = replace(event_json, 'process', 'x') WHERE seq = 1"
    )
    conn.commit()
    assert not adapter.verify_chain().ok
