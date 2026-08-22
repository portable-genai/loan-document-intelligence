"""Unit tests for CrossValidator : the deterministic heart of B5 (SPEC §5).

These assert the deterministic rules directly (no model in the path): consistent
documents PASS every critical check; planted inconsistencies (salary-credit mismatch,
name mismatch, balance decline) FAIL or WARN exactly the right checks; and the LLM never
enters this path so the verdicts are reproducible.
"""

from __future__ import annotations

import pytest
from tests.fixtures import sample_docs

from loan_doc_intel.domain.cross_validator import CrossValidator
from loan_doc_intel.domain.models import CheckKind, CheckStatus


def _by_kind(result):
    return {c.kind: c for c in result.checks}


# --------------------------------------------------------------------------- #
# Consistent documents pass the critical checks.
# --------------------------------------------------------------------------- #
def test_consistent_documents_pass():
    result = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    by_kind = _by_kind(result)
    assert by_kind[CheckKind.SALARY_CREDIT_MATCH].status is CheckStatus.PASS
    assert by_kind[CheckKind.INCOME_CONSISTENCY].status is CheckStatus.PASS
    assert by_kind[CheckKind.NAME_MATCH].status is CheckStatus.PASS
    assert result.passed is True
    assert result.requires_human_review is True


# --------------------------------------------------------------------------- #
# Each planted inconsistency is caught (validation recall).
# --------------------------------------------------------------------------- #
def test_salary_credit_mismatch_fails():
    result = CrossValidator().validate(
        sample_docs.inconsistent_extracts(), sample_docs.APPLICANT, application_id="app-2"
    )
    by_kind = _by_kind(result)
    assert by_kind[CheckKind.SALARY_CREDIT_MATCH].status is CheckStatus.FAIL
    assert result.passed is False


def test_name_mismatch_fails():
    result = CrossValidator().validate(
        sample_docs.inconsistent_extracts(), sample_docs.APPLICANT, application_id="app-2"
    )
    assert _by_kind(result)[CheckKind.NAME_MATCH].status is CheckStatus.FAIL


def test_balance_decline_is_flagged():
    result = CrossValidator().validate(
        sample_docs.inconsistent_extracts(), sample_docs.APPLICANT, application_id="app-2"
    )
    # Opening 20000 -> closing 9000 is a 55% decline => FAIL.
    assert _by_kind(result)[CheckKind.BALANCE_TREND].status is CheckStatus.FAIL


# --------------------------------------------------------------------------- #
# Consistent documents do not raise false flags (validation precision).
# --------------------------------------------------------------------------- #
def test_consistent_documents_have_no_failures():
    result = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    assert result.failed_checks() == ()


# --------------------------------------------------------------------------- #
# Every check carries field-level evidence.
# --------------------------------------------------------------------------- #
def test_every_check_carries_citations_or_is_a_warn():
    result = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    for check in result.checks:
        # A PASS/FAIL critical check must cite the documents it compared.
        if check.status is not CheckStatus.WARN:
            assert check.citations, f"{check.kind} produced no evidence"


def test_all_six_check_kinds_are_run():
    result = CrossValidator().validate(
        sample_docs.consistent_extracts(), sample_docs.APPLICANT, application_id="app-1"
    )
    kinds = {c.kind for c in result.checks}
    assert kinds == set(CheckKind)


def test_configured_amount_tolerance_changes_the_boundary_without_code_changes():
    extracts = sample_docs.inconsistent_extracts()
    strict = CrossValidator(amount_tolerance=0.05).validate(extracts, sample_docs.APPLICANT)
    permissive = CrossValidator(amount_tolerance=0.60).validate(extracts, sample_docs.APPLICANT)
    assert _by_kind(strict)[CheckKind.SALARY_CREDIT_MATCH].status is CheckStatus.FAIL
    assert _by_kind(permissive)[CheckKind.SALARY_CREDIT_MATCH].status is CheckStatus.PASS


def test_invalid_policy_threshold_order_is_refused():
    with pytest.raises(ValueError, match="warn ratio must be below fail ratio"):
        CrossValidator(balance_decline_warn_ratio=0.50, balance_decline_fail_ratio=0.40)


def test_validator_is_deterministic_for_identical_inputs_and_policy():
    validator = CrossValidator(amount_tolerance=0.07)
    first = validator.validate(sample_docs.consistent_extracts(), sample_docs.APPLICANT)
    second = validator.validate(sample_docs.consistent_extracts(), sample_docs.APPLICANT)
    assert first == second


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
