"""Unit tests for LoanDocService : the SPEC §5 R1 processing pipeline.

Pipeline (SPEC §5):
    redact(inputs) -> guardrail.screen(INPUT)
      -> [if blocked: audit + return blocked case]
      -> per document extract -> redact extract
      -> llm normalise into IncomeFigure[]
      -> CrossValidator.validate (DETERMINISTIC) -> IncomeVerificationService verdict
      -> assemble LoanApplicationCase -> guardrail.screen(OUTPUT)
      -> review policy (always true) -> audit.record(redacted)

These tests use only in-memory fakes (no Google Cloud SDK).
"""

from __future__ import annotations

import pytest
from tests.conftest import BlockingGuardrail
from tests.fixtures import sample_docs

from loan_doc_intel.domain.identity import Principal
from loan_doc_intel.domain.models import (
    Decision,
    Direction,
    LoanApplicationCase,
    VerificationVerdict,
)

ACTOR = "underwriter@bank.test"
# A verified demo-bank underwriter entitled to the seeded fixture application
# (app-fictional-0001, owned by demo-bank in the local entitlements registry). The audit
# actor is the principal's subject, so `actor == ACTOR` still holds after the C2 change.
PRINCIPAL = Principal(
    subject=ACTOR,
    principals=("group:loan-analyst", "group:underwriting"),
    tenant="demo-bank",
    source="test",
)


# --------------------------------------------------------------------------- #
# Redaction runs BEFORE the model (P-04: minimise applicant PII to model & store).
# --------------------------------------------------------------------------- #
def test_redaction_runs_before_model(loan_doc_service, redaction, llm):
    loan_doc_service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)

    assert redaction.calls, "redaction.redact was never called"
    # The text the model saw (rendered extracts) must never carry the raw NRIC/email.
    assert llm.requests, "llm.generate was never called"
    model_text = llm.requests[0].messages[-1].content
    assert "S1234567A" not in model_text
    assert "jordan.fictional@example.com" not in model_text


# --------------------------------------------------------------------------- #
# Blocked input: blocked case + audit BLOCKED + requires_human_review, no model call.
# --------------------------------------------------------------------------- #
def test_blocked_input_returns_blocked_case_and_audits(
    extraction, llm, redaction, tracer, audit, entitlements
):
    from tests.conftest import load_service

    blocking = BlockingGuardrail(block_input=True)
    service = load_service("LoanDocService")(
        extraction, llm, blocking, redaction, tracer, audit, entitlements
    )
    case = service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)

    assert isinstance(case, LoanApplicationCase)
    assert case.requires_human_review is True
    assert case.extracts == (), "a blocked case must not carry document extracts"
    assert llm.requests == [], "the LLM must never run once the input is blocked"
    assert extraction.calls == [], "extraction must not run on a blocked input"

    blocked = [e for e in audit.events if e.decision is Decision.BLOCKED]
    assert blocked, "expected an audit event with decision=BLOCKED"
    assert blocked[0].action == "process"
    assert blocked[0].actor == ACTOR


def test_blocked_input_screens_input_direction_only(
    extraction, llm, redaction, tracer, audit, entitlements
):
    from tests.conftest import load_service

    blocking = BlockingGuardrail(block_input=True)
    service = load_service("LoanDocService")(
        extraction, llm, blocking, redaction, tracer, audit, entitlements
    )
    service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    directions = [d for _, d in blocking.calls]
    assert Direction.INPUT in directions
    assert Direction.OUTPUT not in directions


# --------------------------------------------------------------------------- #
# Consistent docs: VERIFIED verdict, all checks present, cited, audited ALLOWED.
# --------------------------------------------------------------------------- #
def test_consistent_documents_verified(loan_doc_service, audit):
    case = loan_doc_service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    assert isinstance(case, LoanApplicationCase)
    assert case.income is not None
    assert case.income.verdict is VerificationVerdict.VERIFIED
    assert case.validation is not None and case.validation.passed is True
    # Always maker-checker gated (P-06): the underwriter decides.
    assert case.requires_human_review is True
    assert case.income.citations, "a verified income must carry document citations"

    assert any(e.decision is Decision.ALLOWED for e in audit.events)


def test_consistent_extract_is_wrapped_in_tracer_span(loan_doc_service, tracer):
    loan_doc_service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    assert tracer.spans, "the process pipeline must open at least one trace span"


def test_audit_record_is_redacted(loan_doc_service, audit):
    loan_doc_service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    assert audit.events
    for event in audit.events:
        assert "S1234567A" not in event.redacted_prompt
        assert "jordan.fictional@example.com" not in event.redacted_prompt


# --------------------------------------------------------------------------- #
# Inconsistent docs: INCONSISTENT verdict + escalated audit + red flags.
# --------------------------------------------------------------------------- #
def test_inconsistent_documents_flagged(
    inconsistent_extraction, llm, guardrail, redaction, tracer, audit, entitlements
):
    from tests.conftest import load_service

    service = load_service("LoanDocService")(
        inconsistent_extraction, llm, guardrail, redaction, tracer, audit, entitlements
    )
    case = service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)

    assert case.income is not None
    assert case.income.verdict is VerificationVerdict.INCONSISTENT
    assert case.income.red_flags, "an inconsistent case must surface red flags"
    assert case.validation is not None and case.validation.passed is False
    # An inconsistency escalates the audit decision.
    assert any(e.decision is Decision.ESCALATED for e in audit.events)


# --------------------------------------------------------------------------- #
# extract_only entry point: redacts + audits a single document.
# --------------------------------------------------------------------------- #
def test_extract_only_audits_and_redacts(
    extraction, llm, guardrail, redaction, tracer, audit, entitlements
):
    from tests.conftest import load_service

    service = load_service("LoanDocService")(
        extraction, llm, guardrail, redaction, tracer, audit, entitlements
    )
    extract = service.extract_only(sample_docs.DOCUMENTS[0], b"", "application/pdf", PRINCIPAL)
    assert extract.document_id == sample_docs.DOCUMENTS[0].id
    assert any(e.action == "extract" for e in audit.events)
    # The redaction port was invoked on the extract's free-text name field (P-04):
    # the real DLP adapter masks PERSON_NAME there; the test fake records the call.
    assert any("Jordan" in call for call in redaction.calls), (
        "the name field must be passed through redaction before use"
    )


def test_only_extracted_documents_are_cited(loan_doc_service):
    """The LLM can only attribute figures to documents we actually extracted."""
    case = loan_doc_service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    extracted_ids = {e.document_id for e in case.extracts}
    for figure in case.income.income_figures:
        assert figure.source_doc_id in extracted_ids


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
