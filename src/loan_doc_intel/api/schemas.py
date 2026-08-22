"""Pydantic v2 request/response models for the B5 Loan Document Intelligence API.

These schemas mirror the frozen domain dataclasses in
:mod:`loan_doc_intel.domain.models` one-for-one, so the HTTP boundary is a thin, typed
projection of the domain : the React/Next.js UI and the CLI consume exactly these shapes.
Each response model exposes a ``from_domain`` classmethod that builds itself from the
corresponding domain object, using :func:`domain.serialization.to_jsonable` where a nested
structure is easiest to project verbatim (enums become their ``.value`` strings).

Nothing here imports Google Cloud, ADK, or any adapter: the API layer depends only on the
domain models, the ports, and the orchestration service : never on a concrete adapter.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain import models as m
from ..domain.serialization import to_jsonable


# --------------------------------------------------------------------------- #
# Shared projections
# --------------------------------------------------------------------------- #
class CitationModel(BaseModel):
    """Field-level provenance attached to an extracted figure or check (mirror)."""

    source_id: str
    source_type: str = "document"
    title: str = ""
    page: int | None = None
    field: str = ""
    snippet: str = ""

    @classmethod
    def from_domain(cls, citation: m.Citation) -> CitationModel:
        return cls(**to_jsonable(citation))


class IncomeFigureModel(BaseModel):
    source_doc_id: str
    amount: float
    currency: str = "SGD"
    period: str = "monthly"
    kind: str = "salary"

    @classmethod
    def from_domain(cls, figure: m.IncomeFigure) -> IncomeFigureModel:
        return cls(**to_jsonable(figure))


class LineItemModel(BaseModel):
    label: str
    amount: float
    currency: str = "SGD"
    date: str | None = None

    @classmethod
    def from_domain(cls, item: m.LineItem) -> LineItemModel:
        return cls(**to_jsonable(item))


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class ApplicantModel(BaseModel):
    id: str
    name: str
    address: str = ""
    declared_income: IncomeFigureModel | None = None

    def to_domain(self) -> m.Applicant:
        declared = (
            m.IncomeFigure(
                source_doc_id=self.declared_income.source_doc_id,
                amount=self.declared_income.amount,
                currency=self.declared_income.currency,
                period=m.IncomePeriod(self.declared_income.period),
                kind=m.IncomeKind(self.declared_income.kind),
            )
            if self.declared_income
            else None
        )
        return m.Applicant(
            id=self.id, name=self.name, address=self.address, declared_income=declared
        )


class DocumentModel(BaseModel):
    id: str
    doc_type: str
    uri: str

    def to_domain(self) -> m.ApplicantDocument:
        return m.ApplicantDocument(id=self.id, doc_type=m.DocType(self.doc_type), uri=self.uri)


class ProcessRequest(BaseModel):
    """Inbound ``/v1/process`` request: an applicant + their documents.

    There is no ``actor`` field: the audit actor is the server-verified :class:`Principal`
    resolved from the request identity (an IAP assertion in secure mode, a seeded persona
    in local mode), never a value the client asserts.
    """

    application: ApplicantModel
    documents: list[DocumentModel] = Field(default_factory=list)


class ExtractRequest(BaseModel):
    """Inbound ``/v1/extract`` request: a single document (actor is server-verified)."""

    document: DocumentModel


class ExtractModel(BaseModel):
    document_id: str
    doc_type: str
    fields: dict[str, str] = Field(default_factory=dict)
    line_items: list[LineItemModel] = Field(default_factory=list)
    period: str = ""
    confidence: float = 0.0
    citations: list[CitationModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, extract: m.DocumentExtract) -> ExtractModel:
        return cls(
            document_id=extract.document_id,
            doc_type=extract.doc_type.value,
            fields=dict(extract.fields),
            line_items=[LineItemModel.from_domain(li) for li in extract.line_items],
            period=extract.period,
            confidence=extract.confidence,
            citations=[CitationModel.from_domain(c) for c in extract.citations],
        )


class ValidateRequest(BaseModel):
    """Inbound ``/v1/validate`` request: an application id + extracts to cross-check."""

    application_id: str
    applicant: ApplicantModel
    extracts: list[ExtractModel] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Cross-validation response
# --------------------------------------------------------------------------- #
class CrossValidationCheckModel(BaseModel):
    kind: str
    status: str
    expected: str
    observed: str
    detail: str = ""
    severity: str = "medium"
    citations: list[CitationModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, check: m.CrossValidationCheck) -> CrossValidationCheckModel:
        return cls(
            kind=check.kind.value,
            status=check.status.value,
            expected=check.expected,
            observed=check.observed,
            detail=check.detail,
            severity=check.severity.value,
            citations=[CitationModel.from_domain(c) for c in check.citations],
        )


class CrossValidationResponse(BaseModel):
    application_id: str
    checks: list[CrossValidationCheckModel] = Field(default_factory=list)
    passed: bool = False
    requires_human_review: bool = True

    @classmethod
    def from_domain(cls, result: m.CrossValidationResult) -> CrossValidationResponse:
        return cls(
            application_id=result.application_id,
            checks=[CrossValidationCheckModel.from_domain(c) for c in result.checks],
            passed=result.passed,
            requires_human_review=result.requires_human_review,
        )


class IncomeSummaryModel(BaseModel):
    application_id: str
    verified_income: IncomeFigureModel
    income_figures: list[IncomeFigureModel] = Field(default_factory=list)
    stability: str = ""
    red_flags: list[str] = Field(default_factory=list)
    verdict: str = "needs_review"
    citations: list[CitationModel] = Field(default_factory=list)
    requires_human_review: bool = True

    @classmethod
    def from_domain(cls, income: m.IncomeVerificationSummary) -> IncomeSummaryModel:
        return cls(
            application_id=income.application_id,
            verified_income=IncomeFigureModel.from_domain(income.verified_income),
            income_figures=[IncomeFigureModel.from_domain(f) for f in income.income_figures],
            stability=income.stability,
            red_flags=list(income.red_flags),
            verdict=income.verdict.value,
            citations=[CitationModel.from_domain(c) for c in income.citations],
            requires_human_review=income.requires_human_review,
        )


class LoanApplicationCaseResponse(BaseModel):
    """The B5 deliverable (mirror of LoanApplicationCase)."""

    id: str
    applicant: ApplicantModel
    extracts: list[ExtractModel] = Field(default_factory=list)
    validation: CrossValidationResponse | None = None
    income: IncomeSummaryModel | None = None
    requires_human_review: bool = True
    generated_at: str = ""

    @classmethod
    def from_domain(cls, case: m.LoanApplicationCase) -> LoanApplicationCaseResponse:
        declared = (
            IncomeFigureModel.from_domain(case.applicant.declared_income)
            if case.applicant.declared_income
            else None
        )
        return cls(
            id=case.id,
            applicant=ApplicantModel(
                id=case.applicant.id,
                name=case.applicant.name,
                address=case.applicant.address,
                declared_income=declared,
            ),
            extracts=[ExtractModel.from_domain(e) for e in case.extracts],
            validation=(
                CrossValidationResponse.from_domain(case.validation) if case.validation else None
            ),
            income=IncomeSummaryModel.from_domain(case.income) if case.income else None,
            requires_human_review=case.requires_human_review,
            generated_at=case.generated_at.isoformat(),
        )


# --------------------------------------------------------------------------- #
# Health & governance
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    status: str = "ok"
    profile: str = "local"
    region: str = "asia-southeast1"


class AgentSkillModel(BaseModel):
    id: str
    name: str
    description: str


class AgentCardModel(BaseModel):
    """A2A AgentCard served at /.well-known/agent-card.json (mirror of AgentCard)."""

    name: str
    description: str
    url: str
    version: str
    provider: str = "loan-document-intelligence"
    skills: list[AgentSkillModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, card: m.AgentCard) -> AgentCardModel:
        data: dict[str, Any] = to_jsonable(card)
        return cls(**data)
