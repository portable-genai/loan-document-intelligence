"""Shared conversion from an escalated loan case to an ``review-kit`` Review payload.

Lives in the adapter layer (not the pure domain) because it depends on the kit. Redacts the
subject descriptor, the summary and every citation snippet before they leave the process (R1 /
P-04 boundary), using the SAME jurisdiction pattern set the redaction adapter uses
(``domain/pii_patterns.patterns_for``), so no raw applicant identifier reaches Hrz7 over the wire;
Hrz7 redacts again before its own audit write (defense in depth). The maker (the underwriter /
service identity that originated the case) and the tenant are asserted here and trusted by Hrz7
because this is an authenticated S2S caller (per-hop OBO is the deferred next layer).

``LoanApplicationCase`` carries no tenant field, so the tenant is passed in by the service from the
verified principal rather than read off the artifact.
"""

from __future__ import annotations

import re

from review_kit import Citation as KitCitation
from review_kit import Review

from ..domain.hitl import LoanReviewPolicy
from ..domain.models import Citation, LoanApplicationCase, VerificationVerdict
from ..domain.pii_patterns import NATIONAL_ID_PATTERNS, Pattern, patterns_for

# Cap the citations carried on the wire: enough to let an underwriter trace the case without
# copying the entire evidence set into the review console.
_MAX_CITATIONS = 8

# The whole-catalogue pattern set (every jurisdiction, not just the deployment's configured
# ones) because the review console is a shared sink: a case processed under an SG book may still
# quote an HK id, and the payload must never carry a raw identifier regardless of which market
# configured this producer. Built once at import.
_ALL_PATTERNS: list[Pattern] = patterns_for(tuple(NATIONAL_ID_PATTERNS))

# Map the deterministic verdict to a maker-checker severity band for the console.
_SEVERITY_BY_VERDICT: dict[VerificationVerdict, str] = {
    VerificationVerdict.VERIFIED: "low",
    VerificationVerdict.NEEDS_REVIEW: "medium",
    VerificationVerdict.INCONSISTENT: "high",
}

_POLICY = LoanReviewPolicy()


def _redact(text: str) -> str:
    """Mask every jurisdiction's national identifiers plus email/phone/bank-account.

    Mirrors ``adapters/local/redaction.LocalRegexRedactionAdapter``: checksum-gated rows
    (AU TFN, HK HKID, JP My Number) mask only genuine identifiers so an ordinary payslip
    figure is left intact, while the un-gated rows mask every match. The mask token is the
    info type, so a reviewer reads the same label the redaction findings name.
    """
    redacted = text
    for info_type, pattern, validator in _ALL_PATTERNS:
        if validator is None:
            redacted = pattern.sub(f"[{info_type}]", redacted)
        else:

            def _repl(match: re.Match[str], _it: str = info_type, _v=validator) -> str:
                return f"[{_it}]" if _v(match.group(0)) else match.group(0)

            redacted = pattern.sub(_repl, redacted)
    return re.sub(r"\s+", " ", redacted).strip()


def _case_citations(case: LoanApplicationCase) -> list[Citation]:
    """Gather the case's evidence: income figures, each deterministic check, and extracts."""
    out: list[Citation] = []
    if case.income is not None:
        out.extend(case.income.citations)
    if case.validation is not None:
        for check in case.validation.checks:
            out.extend(check.citations)
    for extract in case.extracts:
        out.extend(extract.citations)
    return out


def _kit_citations(case: LoanApplicationCase) -> tuple[KitCitation, ...]:
    seen: set[str] = set()
    out: list[KitCitation] = []
    for c in _case_citations(case):
        if c.source_id in seen:
            continue
        seen.add(c.source_id)
        out.append(
            KitCitation(source_id=c.source_id, title=_redact(c.title), snippet=_redact(c.snippet))
        )
        if len(out) >= _MAX_CITATIONS:
            break
    return tuple(out)


def case_to_review(case: LoanApplicationCase, *, maker: str, tenant: str = "") -> Review:
    """Build the review a producer submits to Hrz7 when a loan case escalates (rule R8)."""
    income = case.income
    validation = case.validation
    verdict = income.verdict if income is not None else VerificationVerdict.NEEDS_REVIEW
    applicant = case.applicant

    descriptor = f"Loan application {case.id} for applicant {applicant.name} (id={applicant.id})"

    verified = income.verified_income if income is not None else None
    n_checks = len(validation.checks) if validation is not None else 0
    n_failed = len(validation.failed_checks()) if validation is not None else 0
    n_flags = len(income.red_flags) if income is not None else 0
    verified_txt = (
        f"{verified.monthly_amount():.2f} {verified.currency}/month"
        if verified is not None
        else "n/a"
    )
    summary = (
        f"verdict={verdict.value}; checks={n_checks}; failed={n_failed}; "
        f"verified_income={verified_txt}; red_flags={n_flags}"
    )

    # Dual control (four-eyes) when the case escalates: an INCONSISTENT verdict or any FAILED
    # deterministic check. Otherwise a single underwriter sign-off is the conservative default.
    escalated = _POLICY.is_escalated(validation, verdict)
    required_approvals = 2 if escalated else 1

    return Review(
        action=f"loan_income_verification:{verdict.value}",
        subject=_redact(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_redact(summary),
        severity=_SEVERITY_BY_VERDICT.get(verdict, "medium"),
        required_approvals=required_approvals,
        sod_group="loan-underwriting-maker-checker",
        case_ref=case.id,
        citations=_kit_citations(case),
    )
