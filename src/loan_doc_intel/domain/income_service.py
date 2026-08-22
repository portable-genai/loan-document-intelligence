"""IncomeVerificationService : derive the verified income + verdict (SPEC §5).

Given the document extracts, the normalised income figures and the deterministic
cross-validation result, this service produces the ``IncomeVerificationSummary``: the
single verified income figure, an income-stability note, any red flags, and a verdict.

The verdict is derived DETERMINISTICALLY from the check statuses (the LLM never decides
it): VERIFIED only when every critical check PASSed and none FAILED; INCONSISTENT when
any check FAILED; NEEDS_REVIEW otherwise. The service is pure-ish : it takes the already
computed inputs and assembles the summary, so it has no ports of its own.

Pure domain code : no Google Cloud / ADK imports.
"""

from __future__ import annotations

from .models import (
    CheckStatus,
    Citation,
    CrossValidationResult,
    DocumentExtract,
    IncomeFigure,
    IncomeKind,
    IncomePeriod,
    IncomeVerificationSummary,
    Severity,
    SourceType,
    VerificationVerdict,
)


class IncomeVerificationService:
    """Assemble the verified-income summary from extracts, figures and checks."""

    def summarise(
        self,
        application_id: str,
        extracts: list[DocumentExtract],
        figures: list[IncomeFigure],
        validation: CrossValidationResult,
    ) -> IncomeVerificationSummary:
        """Derive the income verification summary (verdict is deterministic)."""
        verified = self._verified_income(figures, extracts)
        verdict = self._verdict(validation)
        red_flags = self._red_flags(validation)
        stability = self._stability(figures, validation)
        citations = self._citations(figures, extracts, verified)

        return IncomeVerificationSummary(
            application_id=application_id,
            verified_income=verified,
            income_figures=tuple(figures),
            stability=stability,
            red_flags=tuple(red_flags),
            verdict=verdict,
            citations=citations,
            requires_human_review=True,
        )

    # ------------------------------------------------------------------ #
    # Verdict (deterministic : never overridden by the model)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _verdict(validation: CrossValidationResult) -> VerificationVerdict:
        if any(c.status is CheckStatus.FAIL for c in validation.checks):
            return VerificationVerdict.INCONSISTENT
        if validation.passed:
            return VerificationVerdict.VERIFIED
        return VerificationVerdict.NEEDS_REVIEW

    # ------------------------------------------------------------------ #
    # Verified income selection
    # ------------------------------------------------------------------ #
    @staticmethod
    def _verified_income(
        figures: list[IncomeFigure], extracts: list[DocumentExtract]
    ) -> IncomeFigure:
        """Pick the most-evidenced salary figure, normalised to monthly.

        Preference: a salary figure over a non-salary one; otherwise the largest
        monthly amount. Falls back to a zero figure when nothing was normalised so the
        summary always carries a (clearly empty) verified income.
        """
        if not figures:
            source = extracts[0].document_id if extracts else "unknown"
            return IncomeFigure(
                source_doc_id=source,
                amount=0.0,
                period=IncomePeriod.MONTHLY,
                kind=IncomeKind.SALARY,
            )
        salary = [f for f in figures if f.kind is IncomeKind.SALARY]
        pool = salary or list(figures)
        return max(pool, key=lambda f: f.monthly_amount())

    # ------------------------------------------------------------------ #
    # Red flags + stability narrative
    # ------------------------------------------------------------------ #
    @staticmethod
    def _red_flags(validation: CrossValidationResult) -> list[str]:
        flags: list[str] = []
        for c in validation.checks:
            if c.status is CheckStatus.FAIL:
                flags.append(f"{c.kind.value}: {c.expected} but observed {c.observed}")
            elif c.status is CheckStatus.WARN and c.severity in (
                Severity.HIGH,
                Severity.CRITICAL,
            ):
                flags.append(f"{c.kind.value} (warning): {c.observed}")
        return flags

    @staticmethod
    def _stability(figures: list[IncomeFigure], validation: CrossValidationResult) -> str:
        if not figures:
            return "No income figures could be normalised from the documents."
        if validation.passed:
            return "Income is consistent across the supplied documents within tolerance."
        if any(c.status is CheckStatus.FAIL for c in validation.checks):
            return "Income could not be verified: one or more deterministic checks failed."
        return "Income shows warnings that an underwriter should review before relying on it."

    @staticmethod
    def _citations(
        figures: list[IncomeFigure],
        extracts: list[DocumentExtract],
        verified: IncomeFigure,
    ) -> tuple[Citation, ...]:
        by_id = {e.document_id: e for e in extracts}
        doc_ids = {f.source_doc_id for f in figures} or {verified.source_doc_id}
        out: list[Citation] = []
        for doc_id in doc_ids:
            extract = by_id.get(doc_id)
            page = extract.citations[0].page if extract and extract.citations else None
            title = extract.fields.get("title") or extract.doc_type.value if extract else doc_id
            out.append(
                Citation(
                    source_id=doc_id,
                    source_type=SourceType.DOCUMENT,
                    title=title,
                    page=page,
                    field="income",
                )
            )
        return tuple(out)
