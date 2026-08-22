"""Human-in-the-loop (maker-checker) policy : General Principle P-06.

B5 is a *decision-support* assistant for retail-lending underwriting, never an
autonomous approver. P-06 (maker-checker) requires that a human (the underwriter)
reviews any consequential output before it is relied upon. This module centralises that
gate so every service applies identical rules and the threshold is auditable in one
place.

Policy (SPEC §5):
* A ``LoanApplicationCase`` is *always* consequential : it bundles an income
  verification used in an underwriting decision, so it is always flagged for human
  review. The agent verifies; the underwriter decides.
* Any deterministic check that FAILED, or an ``INCONSISTENT`` verdict, escalates : the
  case is routed for review with the inconsistency surfaced.
* A ``VERIFIED`` verdict with only PASS checks is still reviewed (the case is
  consequential) but is not *escalated*.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import CheckStatus, CrossValidationResult, VerificationVerdict


@dataclass(frozen=True, slots=True)
class LoanReviewPolicy:
    """Maker-checker gate (P-06). Pure decision logic; no side effects."""

    def requires_review(self, *, verdict: VerificationVerdict | None = None) -> bool:
        """A loan application case is consequential : it always requires human review.

        ``verdict`` is accepted for symmetry/auditability but does not change the
        outcome : the underwriter signs off on every case.
        """
        return True

    def is_escalated(
        self,
        validation: CrossValidationResult | None,
        verdict: VerificationVerdict | None,
    ) -> bool:
        """Whether the case must be *escalated* (a failure/inconsistency was found).

        Escalation raises the audit decision from ESCALATED-by-default to a surfaced
        inconsistency: any FAILED check or an INCONSISTENT verdict escalates.
        """
        if verdict is VerificationVerdict.INCONSISTENT:
            return True
        if validation is None:
            return False
        return any(c.status is CheckStatus.FAIL for c in validation.checks)
