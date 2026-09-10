"""Unit tests for CrossValidator : the deterministic heart of B5 (SPEC §5).

These assert the deterministic rules directly (no model in the path): consistent
documents PASS every critical check; planted inconsistencies (salary-credit mismatch,
name mismatch, balance decline) FAIL or WARN exactly the right checks; and the LLM never
enters this path so the verdicts are reproducible.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.fixtures import sample_docs

from loan_doc_intel.adapters.local.redaction import LocalRegexRedactionAdapter
from loan_doc_intel.config import PiiSettings, Settings
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


# --------------------------------------------------------------------------- #
# NAME_MATCH / ADDRESS_MATCH compare like with like, and never the applicant with itself.
# --------------------------------------------------------------------------- #
_NRIC = "S1234567A"  # FICTIONAL; a valid checksum, so the SG row masks it


def _sg_redactor() -> LocalRegexRedactionAdapter:
    return LocalRegexRedactionAdapter(Settings(pii=PiiSettings(jurisdictions=("SG",))))


def test_a_masked_document_name_is_compared_with_the_masked_applicant_name():
    # The service's shape: every document extract masked before validation (P-04), the
    # applicant record raw, and both carrying the same identifier.
    redactor = _sg_redactor()
    applicant = replace(sample_docs.APPLICANT, name=f"{sample_docs.APPLICANT.name}, NRIC {_NRIC}")
    extracts = [
        replace(e, fields={**e.fields, "name": redactor.redact(applicant.name).text})
        for e in sample_docs.consistent_extracts()
    ]
    assert all(_NRIC not in e.fields["name"] for e in extracts)

    masked_vs_raw = CrossValidator().validate(extracts, applicant)
    masked_vs_masked = CrossValidator(redaction=redactor).validate(extracts, applicant)

    # Masked against raw is a false FAIL on a consistent application.
    assert _by_kind(masked_vs_raw)[CheckKind.NAME_MATCH].status is CheckStatus.FAIL
    # Masked against masked compares real document values, and they agree. The documents'
    # values are masked twice here, so this also pins that masking is idempotent.
    name = _by_kind(masked_vs_masked)[CheckKind.NAME_MATCH]
    assert name.status is CheckStatus.PASS
    assert len(name.citations) == len(extracts)


def test_binding_the_redactor_changes_nothing_when_neither_side_was_redacted():
    # Both sides go through the redactor, so a caller that redacted neither (the /validate
    # endpoint, the CLI) gets the same verdicts with it bound or not, planted mismatches included.
    for extracts in (sample_docs.consistent_extracts(), sample_docs.inconsistent_extracts()):
        bound = CrossValidator(redaction=_sg_redactor()).validate(extracts, sample_docs.APPLICANT)
        assert bound == CrossValidator().validate(extracts, sample_docs.APPLICANT)


def test_a_field_no_document_carries_is_a_warn_not_a_pass_on_the_applicant_alone():
    extracts = [
        replace(e, fields={k: v for k, v in e.fields.items() if k != "name"})
        for e in sample_docs.consistent_extracts()
    ]
    result = CrossValidator().validate(extracts, sample_docs.APPLICANT)
    name = _by_kind(result)[CheckKind.NAME_MATCH]
    # The applicant declared a name and no document carried one. A set of one used to PASS,
    # having compared the applicant with itself and cited nothing.
    assert name.status is CheckStatus.WARN
    assert name.citations == ()
    # LOW severity: the check says it compared nothing without failing the application.
    assert result.passed is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
