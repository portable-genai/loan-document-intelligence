"""Unit tests for IncomeVerificationService verdict derivation + LoanReviewPolicy.

The verdict is DETERMINISTIC (derived from the check statuses, never the model):
VERIFIED only when all critical checks PASS, INCONSISTENT when any check FAILED,
NEEDS_REVIEW otherwise. A LoanApplicationCase always requires human review (P-06).
"""

from __future__ import annotations

import pytest
from tests.fixtures import sample_docs

from loan_doc_intel.domain.cross_validator import CrossValidator
from loan_doc_intel.domain.hitl import LoanReviewPolicy
from loan_doc_intel.domain.income_service import IncomeVerificationService
from loan_doc_intel.domain.models import (
    IncomeFigure,
    IncomeKind,
    IncomePeriod,
    VerificationVerdict,
)


def _figures():
    return [
        IncomeFigure(source_doc_id="doc-payslip-2026-04", amount=6500.0, kind=IncomeKind.SALARY),
        IncomeFigure(
            source_doc_id="doc-bank-2026-04",
            amount=78000.0,
            period=IncomePeriod.ANNUAL,
            kind=IncomeKind.SALARY,
        ),
    ]


def test_verified_when_all_checks_pass():
    validation = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    summary = IncomeVerificationService().summarise(
        "app-1", sample_docs.consistent_extracts(), _figures(), validation
    )
    assert summary.verdict is VerificationVerdict.VERIFIED
    assert summary.requires_human_review is True
    assert summary.red_flags == ()


def test_inconsistent_when_a_check_fails():
    validation = CrossValidator().validate(
        sample_docs.inconsistent_extracts(), sample_docs.APPLICANT, application_id="app-2"
    )
    summary = IncomeVerificationService().summarise(
        "app-2", sample_docs.inconsistent_extracts(), _figures(), validation
    )
    assert summary.verdict is VerificationVerdict.INCONSISTENT
    assert summary.red_flags, "an inconsistent verdict must surface red flags"


def test_verified_income_normalises_to_monthly():
    validation = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    summary = IncomeVerificationService().summarise(
        "app-1", sample_docs.consistent_extracts(), _figures(), validation
    )
    # The annual 78000 figure normalises to 6500/month, tying with the monthly 6500.
    assert summary.verified_income.monthly_amount() == pytest.approx(6500.0)


def test_empty_figures_yield_zero_verified_income():
    validation = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    summary = IncomeVerificationService().summarise(
        "app-1", sample_docs.consistent_extracts(), [], validation
    )
    assert summary.verified_income.amount == 0.0


# --------------------------------------------------------------------------- #
# LoanReviewPolicy
# --------------------------------------------------------------------------- #
def test_review_always_required():
    policy = LoanReviewPolicy()
    assert policy.requires_review(verdict=VerificationVerdict.VERIFIED) is True
    assert policy.requires_review(verdict=VerificationVerdict.INCONSISTENT) is True


def test_inconsistent_verdict_escalates():
    policy = LoanReviewPolicy()
    validation = CrossValidator().validate(
        sample_docs.inconsistent_extracts(), sample_docs.APPLICANT, application_id="app-2"
    )
    assert policy.is_escalated(validation, VerificationVerdict.INCONSISTENT) is True


def test_clean_verified_case_not_escalated():
    policy = LoanReviewPolicy()
    validation = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    assert policy.is_escalated(validation, VerificationVerdict.VERIFIED) is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
