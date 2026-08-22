"""Vertical domain models for the Loan / Mortgage Document Intelligence service (system B5).

This module is the **vertical** half of the hexagon's heart: the loan artifacts a fork is
expected to rewrite (applicant documents and extracts, income figures, the deterministic
cross-validation checks, the income-verification summary and the application case). The
vertical-neutral machinery it builds on (citations and provenance, the LLM envelope,
guardrail and redaction verdicts, the audit event, the eval report, agent cards and tool
specs, the severity scale, ``utcnow``) lives in :mod:`loan_doc_intel.domain.kernel` and is
imported from there. The dependency direction is one way and enforced by
``tests/unit/test_kernel_boundary.py``: ``kernel`` never imports ``models``.

It has **no dependency on Google Cloud, ADK, FastAPI, or any framework** (only the Python
standard library plus the shared commons). Every adapter (GCP, remote-platform, or on-prem
placeholder) speaks in terms of these types, which is what lets the managed-service stack be
swapped for an on-premise one without touching domain logic (General Principle P-02, "no
vendor lock-in / ports & adapters").

B5 extracts income and bank-statement data from an applicant's documents (Document AI)
and runs DETERMINISTIC cross-validation across them (declared income vs payslip vs bank
statement, name/address consistency, affordability), producing a cited, audited, maker
checker-gated income verification. It is decision-support for underwriting, not a
lending decision. It handles applicant PII, so the full R1 redaction + guardrail
pipeline applies.

The kernel names below are re-exported unchanged, so every existing
``from loan_doc_intel.domain.models import ...`` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .kernel import (
    AgentCard as AgentCard,
)
from .kernel import (
    AgentSkill as AgentSkill,
)
from .kernel import (
    AuditEvent as AuditEvent,
)
from .kernel import (
    Citation as Citation,
)
from .kernel import (
    Decision as Decision,
)
from .kernel import (
    Direction as Direction,
)
from .kernel import (
    EvalMetricResult as EvalMetricResult,
)
from .kernel import (
    EvalReport as EvalReport,
)
from .kernel import (
    GuardrailCategory as GuardrailCategory,
)
from .kernel import (
    GuardrailFinding as GuardrailFinding,
)
from .kernel import (
    GuardrailVerdict as GuardrailVerdict,
)
from .kernel import (
    LlmMessage as LlmMessage,
)
from .kernel import (
    LlmRequest as LlmRequest,
)
from .kernel import (
    LlmResponse as LlmResponse,
)
from .kernel import (
    RedactionFinding as RedactionFinding,
)
from .kernel import (
    RedactionResult as RedactionResult,
)
from .kernel import (
    Severity as Severity,
)
from .kernel import (
    SourceType as SourceType,
)
from .kernel import (
    StrEnum as StrEnum,
)
from .kernel import (
    ThinkingLevel as ThinkingLevel,
)
from .kernel import (
    TokenUsage as TokenUsage,
)
from .kernel import (
    ToolSpec as ToolSpec,
)
from .kernel import (
    utcnow as utcnow,
)


# --------------------------------------------------------------------------- #
# Documents & extraction
# --------------------------------------------------------------------------- #
class DocType(StrEnum):
    """The kinds of applicant document B5 ingests and cross-validates."""

    PAYSLIP = "payslip"
    BANK_STATEMENT = "bank_statement"
    TAX_RETURN = "tax_return"
    EMPLOYMENT_LETTER = "employment_letter"
    ID = "id"


@dataclass(frozen=True, slots=True)
class ApplicantDocument:
    """A single document submitted with a loan application (a pointer, not bytes)."""

    id: str  # stable id within the application, e.g. "doc-payslip-2026-04"
    doc_type: DocType
    uri: str  # storage location (e.g. gs://... or a tenant blob ref); content fetched out of band


@dataclass(frozen=True, slots=True)
class LineItem:
    """One labelled monetary line on a document (e.g. a bank credit or a payslip row)."""

    label: str  # e.g. "Net pay", "Salary credit", "Basic salary"
    amount: float
    currency: str = "SGD"
    date: str | None = None  # ISO date when the line is dated (bank credits)


@dataclass(frozen=True, slots=True)
class DocumentExtract:
    """The structured fields Document AI extracted from one applicant document.

    ``fields`` holds flat key/value pairs (employer, period, gross/net amounts as
    strings exactly as parsed); ``line_items`` holds dated monetary rows used by the
    deterministic cross-validation (salary-credit matching, balance trend).
    """

    document_id: str
    doc_type: DocType
    fields: dict[str, str] = field(default_factory=dict)
    line_items: tuple[LineItem, ...] = ()
    period: str = ""  # the statement / pay period this document covers, e.g. "2026-04"
    confidence: float = 0.0  # Document AI extraction confidence in [0.0, 1.0]
    citations: tuple[Citation, ...] = ()


# --------------------------------------------------------------------------- #
# Income figures (normalised by the LLM, never decided by it)
# --------------------------------------------------------------------------- #
class IncomePeriod(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class IncomeKind(StrEnum):
    SALARY = "salary"
    BONUS = "bonus"
    RENTAL = "rental"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class IncomeFigure:
    """A single normalised income figure attributed to a source document."""

    source_doc_id: str
    amount: float
    currency: str = "SGD"
    period: IncomePeriod = IncomePeriod.MONTHLY
    kind: IncomeKind = IncomeKind.SALARY

    def monthly_amount(self) -> float:
        """Normalise the figure to a monthly amount for comparison."""
        if self.period is IncomePeriod.ANNUAL:
            return self.amount / 12.0
        return self.amount


# --------------------------------------------------------------------------- #
# Runtime, session & memory
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Session:
    id: str
    user_id: str
    application_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    content: str
    scope: str = "user"  # "user" | "application" | "global"
    created_at: datetime = field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Cross-validation : the deterministic heart of B5
# --------------------------------------------------------------------------- #
class CheckKind(StrEnum):
    """The deterministic consistency checks run across an applicant's documents."""

    INCOME_CONSISTENCY = "income_consistency"  # declared vs payslip vs tax return, within tolerance
    SALARY_CREDIT_MATCH = "salary_credit_match"  # bank credits match payslip net pay
    NAME_MATCH = "name_match"  # applicant name consistent across documents
    ADDRESS_MATCH = "address_match"  # applicant address consistent across documents
    BALANCE_TREND = "balance_trend"  # bank balance not on a sustained decline
    AFFORDABILITY = "affordability"  # simple income-to-obligation ratio sanity


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CrossValidationCheck:
    """One deterministic check: an expected vs observed comparison with evidence."""

    kind: CheckKind
    status: CheckStatus
    expected: str  # the rule's expectation, e.g. "net pay 6500 +/- 5%"
    observed: str  # what the documents actually showed, e.g. "salary credit 6500"
    detail: str = ""  # plain-English explanation (may be LLM-authored prose)
    severity: Severity = Severity.MEDIUM
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class CrossValidationResult:
    """The full set of deterministic checks for an application."""

    application_id: str
    checks: tuple[CrossValidationCheck, ...] = ()
    passed: bool = False  # all CRITICAL/HIGH checks PASS and none FAIL
    requires_human_review: bool = True  # always reviewed by the underwriter (P-06)

    def failed_checks(self) -> tuple[CrossValidationCheck, ...]:
        return tuple(c for c in self.checks if c.status is CheckStatus.FAIL)

    def warned_checks(self) -> tuple[CrossValidationCheck, ...]:
        return tuple(c for c in self.checks if c.status is CheckStatus.WARN)


# --------------------------------------------------------------------------- #
# Income verification summary
# --------------------------------------------------------------------------- #
class VerificationVerdict(StrEnum):
    VERIFIED = "verified"  # all critical checks PASS
    NEEDS_REVIEW = "needs_review"  # warnings present, no hard failure
    INCONSISTENT = "inconsistent"  # one or more deterministic checks FAILED


@dataclass(frozen=True, slots=True)
class IncomeVerificationSummary:
    """The verified income figure(s), stability assessment and verdict for an applicant."""

    application_id: str
    verified_income: IncomeFigure
    income_figures: tuple[IncomeFigure, ...] = ()
    stability: str = ""  # plain-English income-stability assessment
    red_flags: tuple[str, ...] = ()
    verdict: VerificationVerdict = VerificationVerdict.NEEDS_REVIEW
    citations: tuple[Citation, ...] = ()
    requires_human_review: bool = True


# --------------------------------------------------------------------------- #
# Applicant + the top-level deliverable
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Applicant:
    """The loan applicant (synthetic, clearly fictional in all fixtures)."""

    id: str
    name: str
    address: str = ""
    declared_income: IncomeFigure | None = None


@dataclass(frozen=True, slots=True)
class LoanApplicationCase:
    """The B5 deliverable: extracts + cross-validation + income summary, cited and audited.

    Always ``requires_human_review = True`` : the underwriter is the human checker
    (P-06). B5 verifies, it does not approve.
    """

    id: str
    applicant: Applicant
    documents: tuple[ApplicantDocument, ...] = ()
    extracts: tuple[DocumentExtract, ...] = ()
    validation: CrossValidationResult | None = None
    income: IncomeVerificationSummary | None = None
    requires_human_review: bool = True
    generated_at: datetime = field(default_factory=utcnow)
