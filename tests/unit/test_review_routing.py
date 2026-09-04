"""R8 routing: an escalated loan case is routed to human-review-console via the shared review-kit.

Every ``LoanApplicationCase`` requires human review (the underwriter is the checker, P-06), so rule
R8 says it MUST be handed to the human-review-console maker-checker console rather than left as a
per-repo boolean. These tests prove the producer half of that loop end-to-end against the offline
local router (an in-memory outbox), and prove the redact-before-wire boundary so no raw applicant
identifier reaches the console. All data here is synthetic and clearly fictional.
"""

from __future__ import annotations

import pytest
from tests.conftest import load_service
from tests.fixtures import sample_docs

from loan_doc_intel.adapters._review_payload import case_to_review
from loan_doc_intel.adapters.local.review_router import LocalReviewRouter
from loan_doc_intel.config import Settings
from loan_doc_intel.domain.identity import Principal
from loan_doc_intel.domain.models import (
    Applicant,
    CheckKind,
    CheckStatus,
    Citation,
    CrossValidationCheck,
    CrossValidationResult,
    IncomeFigure,
    IncomePeriod,
    IncomeVerificationSummary,
    LoanApplicationCase,
    Severity,
    SourceType,
    VerificationVerdict,
)

ACTOR = "underwriter@bank.test"
TENANT = "demo-bank"
PRINCIPAL = Principal(
    subject=ACTOR,
    principals=("group:loan-analyst", "group:underwriting"),
    tenant=TENANT,
    source="test",
)


def _service_with_router(
    extraction, llm, guardrail, redaction, tracer, audit, entitlements, router
):
    return load_service("LoanDocService")(
        extraction, llm, guardrail, redaction, tracer, audit, entitlements, review_router=router
    )


def test_process_routes_case_to_outbox_once(
    extraction, llm, guardrail, redaction, tracer, audit, entitlements
):
    """A processed application enqueues exactly one review to the router's outbox (R8)."""
    router = LocalReviewRouter(Settings())
    service = _service_with_router(
        extraction, llm, guardrail, redaction, tracer, audit, entitlements, router
    )
    assert not router.outbox.pending()

    case = service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    assert case.requires_human_review

    pending = router.outbox.pending()
    assert len(pending) == 1, (
        "the escalated case must be routed to human-review-console exactly once"
    )
    review = pending[0].review
    assert review.case_ref == case.id
    assert review.maker == ACTOR
    assert review.tenant == TENANT
    assert review.action.startswith("loan_income_verification:")


def _inconsistent_case_with_pii() -> LoanApplicationCase:
    """A clearly-fictional case whose verdict is INCONSISTENT and whose evidence quotes a NRIC."""
    applicant = Applicant(id="app-fictional-9999", name="Jordan Tester Fictional")
    # A citation snippet carrying a synthetic SG NRIC: it must be masked before the wire.
    cite = Citation(
        source_id="doc-payslip-1",
        source_type=SourceType.DOCUMENT,
        title="Payslip 2026-04",
        field="net_pay",
        snippet="Net pay for NRIC S1234567A credited to the account.",
    )
    failed = CrossValidationCheck(
        kind=CheckKind.SALARY_CREDIT_MATCH,
        status=CheckStatus.FAIL,
        expected="bank salary credit 6500.00 +/- 5%",
        observed="bank salary credit 4200.00",
        severity=Severity.CRITICAL,
        citations=(cite,),
    )
    validation = CrossValidationResult(application_id=applicant.id, checks=(failed,), passed=False)
    income = IncomeVerificationSummary(
        application_id=applicant.id,
        verified_income=IncomeFigure(
            source_doc_id="doc-payslip-1", amount=6500.0, period=IncomePeriod.MONTHLY
        ),
        verdict=VerificationVerdict.INCONSISTENT,
        red_flags=("salary_credit_match: mismatch",),
        citations=(cite,),
    )
    return LoanApplicationCase(
        id=applicant.id,
        applicant=applicant,
        validation=validation,
        income=income,
    )


def test_payload_is_redacted_and_carries_tenant_and_dual_control():
    """The wire payload masks identifiers, carries the tenant, and escalates to four-eyes."""
    review = case_to_review(_inconsistent_case_with_pii(), maker=ACTOR, tenant=TENANT)

    assert review.tenant == TENANT
    assert review.severity == "high"
    assert review.required_approvals == 2, "an INCONSISTENT case warrants dual control"
    # No raw NRIC survives into the payload the console receives.
    assert "S1234567A" not in review.subject
    assert "S1234567A" not in review.summary
    for citation in review.citations:
        assert "S1234567A" not in citation.snippet
    assert any(c.title == "Payslip 2026-04" for c in review.citations)


def test_verified_case_is_single_control():
    """A VERIFIED case is still reviewed (P-06) but is not escalated to dual control."""
    applicant = Applicant(id="app-fictional-0002", name="Alex Fictional")
    validation = CrossValidationResult(application_id=applicant.id, checks=(), passed=True)
    income = IncomeVerificationSummary(
        application_id=applicant.id,
        verified_income=IncomeFigure(
            source_doc_id="doc-1", amount=5000.0, period=IncomePeriod.MONTHLY
        ),
        verdict=VerificationVerdict.VERIFIED,
    )
    case = LoanApplicationCase(
        id=applicant.id, applicant=applicant, validation=validation, income=income
    )

    review = case_to_review(case, maker=ACTOR, tenant=TENANT)
    assert review.severity == "low"
    assert review.required_approvals == 1


def test_no_router_still_processes_case(
    extraction, llm, guardrail, redaction, tracer, audit, entitlements
):
    """Routing is optional: with no router bound, processing still returns an escalated case."""
    service = _service_with_router(
        extraction, llm, guardrail, redaction, tracer, audit, entitlements, None
    )
    case = service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    assert case.requires_human_review


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
