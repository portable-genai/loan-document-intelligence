"""LoanDocService : the B5 document-intelligence orchestration (SPEC §5).

Owns the full processing pipeline and calls only ports. The pipeline implements the
FULL R1 safety chain (B5 handles applicant PII), in order:

    tracer.span("loan_doc.process"):
      redact(inputs)                      [P-04: PII removed before model / audit]
      -> guardrail.screen(INPUT)          [blocked -> audit BLOCKED + blocked case]
      -> per document: extraction.extract -> redact the extract
      -> llm normalise extracts into IncomeFigure[]   [model normalises, never decides]
      -> CrossValidator.validate          [DETERMINISTIC : the verdict authority]
      -> IncomeVerificationService verdict
      -> assemble LoanApplicationCase (requires_human_review = True)
      -> guardrail.screen(OUTPUT)
      -> review policy (always true)
      -> audit.record(redacted prompt + response)

Defensive throughout: a guardrail block, missing documents and malformed model JSON all
degrade to a safe, human-review-flagged case rather than crashing the caller.

Pure domain code : no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

import contextlib
from contextlib import nullcontext
from typing import Any

from . import _grounded as g
from .cross_validator import CrossValidator
from .entitlements import require_object_access
from .hitl import LoanReviewPolicy
from .identity import Principal
from .income_service import IncomeVerificationService
from .models import (
    ApplicantDocument,
    AuditEvent,
    Citation,
    CrossValidationResult,
    Decision,
    Direction,
    DocumentExtract,
    GuardrailVerdict,
    IncomeFigure,
    IncomePeriod,
    IncomeVerificationSummary,
    LoanApplicationCase,
    RedactionResult,
    VerificationVerdict,
    utcnow,
)
from .prompts import _CITATION_RULES, NORMALISE_INCOME_SYSTEM, NORMALISE_INCOME_USER
from .serialization import to_jsonable

# JSON schema for the structured income-normalisation response.
_FIGURES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "figures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_doc_id": {"type": "string"},
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                    "period": {"type": "string", "enum": ["monthly", "annual"]},
                    "kind": {
                        "type": "string",
                        "enum": ["salary", "bonus", "rental", "other"],
                    },
                },
                "required": ["source_doc_id", "amount", "period", "kind"],
            },
        }
    },
    "required": ["figures"],
}


class LoanDocService:
    """Process an application end-to-end. Constructor takes explicit ports (SPEC §5)."""

    def __init__(
        self,
        extraction: Any,
        llm: Any,
        guardrail: Any,
        redaction: Any,
        tracer: Any,
        audit: Any,
        entitlements: Any,
        validator: CrossValidator | None = None,
        income_service: IncomeVerificationService | None = None,
        review_policy: LoanReviewPolicy | None = None,
        review_router: Any = None,
    ) -> None:
        self._extraction = extraction
        self._llm = llm
        self._guardrail = guardrail
        self._redaction = redaction
        self._tracer = tracer
        self._audit = audit
        self._entitlements = entitlements
        self._validator = validator or CrossValidator()
        self._income = income_service or IncomeVerificationService()
        self._review = review_policy or LoanReviewPolicy()
        # Optional ReviewRouterPort (rule R8): when bound, an escalated case is routed to the
        # Hrz7 maker-checker console. Optional so the pure domain and unit tests run without it.
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def process(
        self,
        application: Any,
        documents: list[ApplicantDocument],
        principal: Principal,
    ) -> LoanApplicationCase:
        """Process ``application`` + ``documents`` via the SPEC §5 R1 pipeline.

        Object-level authorization (C2) runs FIRST, fail-closed: the application's owner is
        resolved SERVER-SIDE via the EntitlementsPort (never from the client-submitted body)
        and checked against the VERIFIED ``principal``, so an authenticated caller cannot
        process another tenant's applicant. A denial raises
        :class:`~loan_doc_intel.domain.errors.AccessDenied` (HTTP 403) BEFORE any redaction,
        extraction, validation, or audit : the object is not touched. The audit actor is the
        verified subject, derived here from ``principal`` (never a client-asserted field).
        """
        applicant = getattr(application, "applicant", application)
        application_id = getattr(application, "id", getattr(applicant, "id", "application"))
        self._authorize(principal, application_id)
        actor = principal.actor
        span = self._tracer.span("loan_doc.process", action="process", actor=actor)
        with span if span is not None else nullcontext():
            case = self._process_inner(application, documents, actor)
        # Rule R8: route the escalation to the Hrz7 maker-checker console instead of leaving it a
        # per-repo boolean. After the audit (written inside the pipeline) and best-effort: a
        # routing failure must never crash the request or lose the assembled case.
        self._route_for_review(case, maker=actor, tenant=principal.tenant)
        return case

    def extract_only(
        self, document: ApplicantDocument, content: bytes, mime_type: str, principal: Principal
    ) -> DocumentExtract:
        """Extract one document (``/v1/extract``): authorized, redacted, and audited.

        The document is the protected object here: its owner is resolved SERVER-SIDE and
        checked against the VERIFIED ``principal`` BEFORE extraction or audit (fail-closed;
        a cross-tenant caller raises :class:`~loan_doc_intel.domain.errors.AccessDenied`).
        """
        self._authorize(principal, document.id)
        actor = principal.actor
        span = self._tracer.span("loan_doc.extract", action="extract", actor=actor)
        with span if span is not None else nullcontext():
            extract = self._extract_and_redact(document, content, mime_type)
            self._write_audit(
                actor,
                f"extract document {document.id} ({document.doc_type.value})",
                f"{len(extract.fields)} fields, confidence {extract.confidence:.2f}",
                Decision.ALLOWED,
                citations=extract.citations,
                action="extract",
            )
            return extract

    # ------------------------------------------------------------------ #
    # Object-level authorization (C2): fail-closed, SERVER-SIDE owner resolution.
    # ------------------------------------------------------------------ #
    def _authorize(self, principal: Principal, object_id: str) -> None:
        """Authorize ``principal`` for ``object_id`` or raise ``AccessDenied`` (fail-closed).

        The object's owner (owning tenant + permitted roles) is resolved from the
        EntitlementsPort : a SERVER-SIDE source of truth keyed by object id : so the
        client-submitted object cannot assert its own owner. An unknown object (no owner
        record) is denied.
        """
        owner = self._entitlements.owner(object_id)
        require_object_access(principal, object_id, owner)

    # ------------------------------------------------------------------ #
    # Review routing (rule R8): hand the escalation to Hrz7, not a boolean.
    # ------------------------------------------------------------------ #
    def _route_for_review(self, case: LoanApplicationCase, *, maker: str, tenant: str) -> None:
        """Route a ``requires_human_review`` case to the Hrz7 console via the bound router.

        Optional and best-effort: no router bound (pure domain / unit tests) is a no-op, and a
        routing failure is suppressed so it never crashes the request or drops the assembled case
        (the case is already persisted/audited; the router is the transactional-outbox seam).
        """
        if self._review_router is None or not case.requires_human_review:
            return
        with contextlib.suppress(Exception):
            self._review_router.route(case, maker=maker, tenant=tenant)

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #
    def _process_inner(
        self,
        application: Any,
        documents: list[ApplicantDocument],
        actor: str,
    ) -> LoanApplicationCase:
        applicant = getattr(application, "applicant", application)
        application_id = getattr(application, "id", getattr(applicant, "id", "application"))

        # 1) Redact the inputs (P-04) before anything touches the model or the audit log.
        prompt = self._input_description(applicant, documents)
        redaction: RedactionResult = self._redaction.redact(prompt)
        redacted_prompt = redaction.text

        # 2) Guardrail screen (INPUT). Blocked -> audit BLOCKED + blocked case.
        in_verdict: GuardrailVerdict = self._guardrail.screen(redacted_prompt, Direction.INPUT)
        if not in_verdict.allowed:
            return self._blocked_case(applicant, application_id, documents, redacted_prompt, actor)

        # 3) Per document: extract then redact the extract.
        extracts: list[DocumentExtract] = []
        for document in documents:
            content, mime_type = self._load_content(document)
            extracts.append(self._extract_and_redact(document, content, mime_type))

        # 4) LLM normalises the extracts into IncomeFigure[] (never decides a verdict).
        figures = self._normalise_income(applicant, extracts)

        # 5) Deterministic cross-validation : the verdict authority.
        validation: CrossValidationResult = self._validator.validate(
            extracts, applicant, application_id=application_id
        )

        # 6) Derive the income verification summary (deterministic verdict).
        income: IncomeVerificationSummary = self._income.summarise(
            application_id, extracts, figures, validation
        )

        # 7) Assemble the case (always requires human review : the underwriter decides).
        case = LoanApplicationCase(
            id=application_id,
            applicant=applicant,
            documents=tuple(documents),
            extracts=tuple(extracts),
            validation=validation,
            income=income,
            requires_human_review=self._review.requires_review(verdict=income.verdict),
            generated_at=utcnow(),
        )

        # 8) Guardrail screen (OUTPUT) on a redacted projection of the case.
        out_text = self._output_summary(case)
        out_verdict: GuardrailVerdict = self._guardrail.screen(out_text, Direction.OUTPUT)
        if not out_verdict.allowed:
            return self._blocked_case(
                applicant, application_id, documents, redacted_prompt, actor, Direction.OUTPUT
            )

        # 9) Audit (already-redacted prompt + response).
        decision = (
            Decision.ESCALATED
            if self._review.is_escalated(validation, income.verdict)
            else Decision.ALLOWED
        )
        self._write_audit(
            actor,
            redacted_prompt,
            self._output_summary(case),
            decision,
            citations=income.citations,
            metadata={
                "verdict": income.verdict.value,
                "passed": str(validation.passed).lower(),
                "n_documents": str(len(documents)),
                "requires_human_review": "true",
            },
        )
        return case

    # ------------------------------------------------------------------ #
    # Extraction + redaction of an extract (P-04 applies to extracted PII too)
    # ------------------------------------------------------------------ #
    def _extract_and_redact(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> DocumentExtract:
        extract: DocumentExtract = self._extraction.extract(document, content, mime_type)
        return self._redact_extract(extract)

    def _redact_extract(self, extract: DocumentExtract) -> DocumentExtract:
        """Redact PII-bearing free-text fields on the extract before it is used/audited.

        Numeric fields used by the deterministic checks (net_pay, balances) are left
        intact; name/address/employer free text is de-identified so raw PII never
        reaches the model prompt or the audit sink.
        """
        redactable = {"name", "address", "employer", "account_holder", "title"}
        new_fields = dict(extract.fields)
        for key in list(new_fields):
            if key in redactable and new_fields[key]:
                new_fields[key] = self._redaction.redact(new_fields[key]).text
        from dataclasses import replace

        return replace(extract, fields=new_fields)

    # ------------------------------------------------------------------ #
    # LLM income normalisation
    # ------------------------------------------------------------------ #
    def _normalise_income(
        self, applicant: Any, extracts: list[DocumentExtract]
    ) -> list[IncomeFigure]:
        if not extracts:
            return []
        declared = self._declared_text(applicant)
        system = NORMALISE_INCOME_SYSTEM.format(citation_rules=_CITATION_RULES)
        user = NORMALISE_INCOME_USER.format(declared=declared, extracts=g.render_extracts(extracts))
        request = g.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,  # adapter default => reasoning model gemini-3.5-flash
            response_schema=_FIGURES_SCHEMA,
        )
        try:
            response = self._llm.generate(request)
        except Exception:  # noqa: BLE001 - normalisation failure must not drop the case
            return []
        g.maybe_record_usage(self._tracer, response)
        parsed = g.parse_structured(response)
        return self._build_figures(parsed, extracts)

    @staticmethod
    def _build_figures(
        parsed: dict[str, Any], extracts: list[DocumentExtract]
    ) -> list[IncomeFigure]:
        known_ids = {e.document_id for e in extracts}
        raw_items = parsed.get("figures")
        if not isinstance(raw_items, list):
            return []
        out: list[IncomeFigure] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source_doc_id") or "").strip()
            if source not in known_ids:  # only cite documents we actually extracted
                continue
            amount = g.coerce_amount(raw.get("amount"))
            if amount is None:  # unreadable is not zero; skip rather than fabricate a figure
                continue
            out.append(
                IncomeFigure(
                    source_doc_id=source,
                    amount=amount,
                    currency=str(raw.get("currency") or "SGD"),
                    period=g.coerce_period(raw.get("period")),
                    kind=g.coerce_kind(raw.get("kind")),
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Loading + projections
    # ------------------------------------------------------------------ #
    def _load_content(self, document: ApplicantDocument) -> tuple[bytes, str]:
        """Load a document's bytes for extraction.

        The domain does not own a storage port: the API/CLI thread bytes in directly,
        and the extraction adapter fetches by URI when content is empty. Here we pass an
        empty payload so the adapter resolves ``document.uri`` itself.
        """
        return b"", "application/pdf"

    @staticmethod
    def _input_description(applicant: Any, documents: list[ApplicantDocument]) -> str:
        name = getattr(applicant, "name", "applicant")
        address = getattr(applicant, "address", "")
        doc_list = ", ".join(f"{d.id}:{d.doc_type.value}" for d in documents)
        return (
            f"Process loan application for applicant {name} (address {address}). "
            f"Documents: {doc_list}."
        )

    @staticmethod
    def _declared_text(applicant: Any) -> str:
        declared: IncomeFigure | None = getattr(applicant, "declared_income", None)
        if declared is None:
            return "(no declared income provided)"
        return (
            f"{declared.amount} {declared.currency} per {declared.period.value} "
            f"({declared.kind.value})"
        )

    @staticmethod
    def _output_summary(case: LoanApplicationCase) -> str:
        income = case.income
        validation = case.validation
        verdict = income.verdict.value if income else "unknown"
        n_checks = len(validation.checks) if validation else 0
        amount = income.verified_income.amount if income else 0.0
        return (
            f"Verdict {verdict}; verified income {amount}; {n_checks} cross-validation "
            f"checks; requires human review."
        )

    # ------------------------------------------------------------------ #
    # Blocked / degraded cases
    # ------------------------------------------------------------------ #
    def _blocked_case(
        self,
        applicant: Any,
        application_id: str,
        documents: list[ApplicantDocument],
        redacted_prompt: str,
        actor: str,
        direction: Direction = Direction.INPUT,
    ) -> LoanApplicationCase:
        validation = CrossValidationResult(
            application_id=application_id, checks=(), passed=False, requires_human_review=True
        )
        income = IncomeVerificationSummary(
            application_id=application_id,
            verified_income=IncomeFigure(
                source_doc_id="blocked", amount=0.0, period=IncomePeriod.MONTHLY
            ),
            verdict=VerificationVerdict.NEEDS_REVIEW,
            red_flags=("Request blocked by the safety guardrail; not processed.",),
            requires_human_review=True,
        )
        case = LoanApplicationCase(
            id=application_id,
            applicant=applicant,
            documents=tuple(documents),
            extracts=(),
            validation=validation,
            income=income,
            requires_human_review=True,
        )
        self._write_audit(
            actor,
            redacted_prompt,
            f"blocked by guardrail ({direction.value})",
            Decision.BLOCKED,
            metadata={"requires_human_review": "true"},
        )
        return case

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _write_audit(
        self,
        actor: str,
        redacted_prompt: str,
        redacted_response: str,
        decision: Decision,
        citations: tuple[Citation, ...] = (),
        metadata: dict[str, str] | None = None,
        action: str = "process",
    ) -> None:
        event = AuditEvent(
            action=action,
            actor=actor,
            decision=decision,
            redacted_prompt=redacted_prompt,
            redacted_response=redacted_response,
            citations=citations,
            metadata=metadata or {},
        )
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001 - audit failure must not crash the request
            to_jsonable(event)
