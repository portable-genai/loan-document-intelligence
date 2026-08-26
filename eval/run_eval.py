#!/usr/bin/env python3
"""Offline evaluation gate for B5 Loan Document Intelligence : A4 / General Principle P-08.

This is the **promotion gate**: CI runs it on every change and the build fails if the
service's extraction + deterministic validation fall below the model-risk thresholds
agreed for a regulated retail-lending document-intelligence service (see
``eval/rubrics/*.yaml``)::

    extraction_accuracy  >= 0.80
    validation_recall    >= 0.90    (catches planted inconsistencies)
    validation_precision >= 0.90    (no false flags on consistent docs)
    pii_safety           >= 0.99    (no unredacted applicant PII in output / audit)

Two evaluators, one gate
------------------------
* **Production evaluator** : the **Gen AI evaluation service** on the *Gemini Enterprise
  Agent Platform*, wired into the hexagon as ``EvaluationGatePort`` ->
  ``loan_doc_intel.adapters.gcp.genai_eval:GenAiEvalAdapter``. It needs GCP credentials and
  a project. Select it with ``--use-gcp`` (routes through the ``Container``).
  # verify: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/run-evaluation

* **Offline evaluator (default)** : a deterministic, dependency-light driver implemented in
  this file. It needs **no GCP credentials and no Google Cloud SDK**, runs the real
  ``LoanDocService`` pipeline against in-memory fake adapters over a synthetic golden set
  (both consistent and planted-inconsistency cases), and computes the same four metrics.
  This is what guards the merge in CI.

The offline scorers are a strict *lower bound* on the production judge: if the offline gate
passes, the production gate is expected to pass too, but the production gate remains the
authority for promotion.

Usage::

    python eval/run_eval.py                      # offline gate (CI)
    python eval/run_eval.py --dataset path.jsonl # custom golden set
    python eval/run_eval.py --use-gcp            # route through GenAiEvalAdapter

Exit code is ``0`` iff ``EvalReport.passed`` (every metric meets its threshold).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

# The local redactor is the REAL one the runtime uses (see _real_redactor): pure regex over
# the shared pack, SDK-free, so it is imported here rather than faked.
# The --mode smoke|gate scaffold + aligned report rendering come from the shared
# agent-eval-kit commons; this script keeps only its own offline
# evaluator and gate runner.
from agent_eval_kit import eval_main

from loan_doc_intel.adapters.local.redaction import LocalRegexRedactionAdapter
from loan_doc_intel.config import PiiSettings, Settings

# Domain models are pure-stdlib (no GCP / framework imports), so importing them here keeps
# this script runnable in the on-prem/test profile with no Google Cloud SDK installed.
from loan_doc_intel.domain import pii_patterns
from loan_doc_intel.domain.entitlements import ObjectOwner
from loan_doc_intel.domain.identity import Principal
from loan_doc_intel.domain.models import (
    Applicant,
    ApplicantDocument,
    CheckStatus,
    Citation,
    Direction,
    DocType,
    DocumentExtract,
    EvalMetricResult,
    EvalReport,
    GuardrailVerdict,
    IncomeFigure,
    IncomeKind,
    IncomePeriod,
    LineItem,
    LlmRequest,
    LlmResponse,
    SourceType,
    TokenUsage,
)
from loan_doc_intel.envread import setting_or_default

THRESHOLDS: dict[str, float] = {
    "extraction_accuracy": 0.80,
    "validation_recall": 0.90,
    "validation_precision": 0.90,
    "pii_safety": 0.99,
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_cases.jsonl"

# The pii_safety leak check MUST use the SAME jurisdiction pattern source as the runtime
# redactor (domain/pii_patterns.py), and this gate runs the REAL LocalRegexRedactionAdapter
# rather than a fake. Both matter: a leak then means the pipeline re-introduced PII that
# bypassed redaction, not that a bespoke detector and a bespoke redactor drifted apart and
# happened to agree. Default to B5's APAC lending markets; override with
# LOAN_DOC_PII_JURISDICTIONS (comma-separated ISO-3166 codes).
#
# This is NOT a posture-free read, even though it lives in eval/ rather than src/. The list
# narrows what the leak check LOOKS FOR: patterns_for keeps the universal email / phone /
# account rows but contributes a national-ID row only for a jurisdiction that is named, so a
# list that names nothing leaves the gate blind to exactly the identifiers this vertical
# exists to protect (NRIC, HKID, My Number, TFN). A two-state read would let an emptied
# variable inherit the shipped pack OR silently empty the pack depending on the spelling, and
# either way the promotion gate would report pii_safety green over a check it was no longer
# performing. So: unset takes the documented default, EMPTIED refuses, and a value that parses
# to no code at all (",") refuses too.
_PII_JURISDICTIONS = tuple(
    j.strip().upper()
    for j in setting_or_default(
        "LOAN_DOC_PII_JURISDICTIONS", ",".join(pii_patterns.DEFAULT_JURISDICTIONS)
    ).split(",")
    if j.strip()
)
if not _PII_JURISDICTIONS:
    raise SystemExit(
        "LOAN_DOC_PII_JURISDICTIONS names no jurisdiction, so the pii_safety gate would check "
        "for no national identifier at all. Unset it to take the shipped pack, or name the "
        "ISO-3166 alpha-2 codes whose identifiers must be redacted."
    )
_PII_PATTERNS = pii_patterns.patterns_for(_PII_JURISDICTIONS)

# Obviously-fictional national identifiers, one per market, planted in a golden case's
# applicant to prove the pack redacts each jurisdiction it claims to cover. Together with
# _planted_pii_leak (which tests for these literals, independently of the pack) this is what
# makes the per-market claim real: break any one market's row and only its own case goes red.
#
# The JP and AU fixtures are written in their GROUPED form on purpose. Unlike the
# trade-finance vertical there is no bare-digit catch-all here to cover for a broken row, so
# either form would prove the row; the grouped form is chosen because it is what the card and
# the notice actually print, and it is the form the narrowed `\b\d{12}\b` row the sibling
# packs shipped could not see at all. Both carry VALID check digits, because their rows are
# checksum-gated and an invalid fixture would prove nothing.
#
# market -> (label written next to it in the document, the identifier itself). The label and
# the value are kept apart because the leak check tests for the VALUE verbatim, and a value
# carrying spaces cannot be recovered from the joined phrase.
_PII_BY_JURISDICTION: dict[str, tuple[str, str]] = {
    "SG": ("NRIC", "S1234567A"),
    "HK": ("HKID", "A123456(3)"),
    "JP": ("My Number", "1234 5678 9018"),
    "AU": ("TFN", "123 456 782"),
}

# The universal rows, planted in every PII case alongside the market identifier. The account
# number is the 3-6-1 shape a bank statement prints, and it is deliberately the one whose
# leading nine digits PASS the AU TFN checksum: with the pack's rows in the wrong order the
# AU row bites off `123-456782` and this applicant's account is reported as a tax file
# number. See domain/pii_patterns.py and tests/unit/test_redaction_service.py.
_ACCOUNT_FIXTURE = "123-456782-0"
_EMAIL_FIXTURE = "ops@example.com"


# --------------------------------------------------------------------------- #
# Golden dataset
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GoldenCase:
    id: str
    applicant: dict
    documents: list[dict]
    extracts: list[dict]
    expected_verdict: str
    expected_failed_checks: tuple[str, ...]
    jurisdiction: str = ""
    pii_in_inputs: bool = False


def load_golden(path: Path) -> list[GoldenCase]:
    """Parse the JSONL golden set (stdlib ``json``)."""
    cases: list[GoldenCase] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        cases.append(
            GoldenCase(
                id=str(obj.get("id", f"case-{lineno}")),
                applicant=obj["applicant"],
                documents=list(obj.get("documents", [])),
                extracts=list(obj.get("extracts", [])),
                expected_verdict=str(obj["expected_verdict"]),
                expected_failed_checks=tuple(obj.get("expected_failed_checks", []) or ()),
                jurisdiction=str(obj.get("jurisdiction", "")),
                pii_in_inputs=bool(obj.get("pii_in_inputs", False)),
            )
        )
    if not cases:
        raise SystemExit(f"{path}: golden dataset is empty")
    return cases


def load_thresholds_from_rubrics() -> dict[str, float]:
    """Read thresholds from ``eval/rubrics/*.yaml`` when PyYAML is available."""
    thresholds = dict(THRESHOLDS)
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return thresholds

    rubric_dir = _REPO_ROOT / "eval" / "rubrics"
    for name in ("extraction_accuracy.yaml", "validation.yaml"):
        rubric_path = rubric_dir / name
        if not rubric_path.exists():
            continue
        doc = yaml.safe_load(rubric_path.read_text(encoding="utf-8")) or {}
        metric = doc.get("metric")
        if isinstance(metric, str) and "threshold" in doc:
            thresholds[metric] = float(doc["threshold"])
        for companion, spec in (doc.get("companion_metrics") or {}).items():
            if isinstance(spec, dict) and "threshold" in spec:
                thresholds[str(companion)] = float(spec["threshold"])
    return thresholds


# --------------------------------------------------------------------------- #
# Deterministic fake adapters (inlined: importing tests.conftest is disallowed for
# this gate, and CI must not depend on the test tree). Each satisfies its port.
# --------------------------------------------------------------------------- #
def _build_extract(obj: dict) -> DocumentExtract:
    doc_id = str(obj["document_id"])
    doc_type = DocType(str(obj.get("doc_type", "payslip")))
    line_items = tuple(
        LineItem(
            label=str(li["label"]),
            amount=float(li["amount"]),
            currency=str(li.get("currency", "SGD")),
            date=li.get("date"),
        )
        for li in obj.get("line_items", [])
    )
    citation = Citation(
        source_id=doc_id,
        source_type=SourceType.DOCUMENT,
        title=str(obj.get("title", doc_type.value)),
        page=1,
        field="",
    )
    return DocumentExtract(
        document_id=doc_id,
        doc_type=doc_type,
        fields={str(k): str(v) for k, v in obj.get("fields", {}).items()},
        line_items=line_items,
        period=str(obj.get("period", "")),
        confidence=float(obj.get("confidence", 0.9)),
        citations=(citation,),
    )


class FakeExtractionAdapter:
    """Deterministic extraction keyed off the golden case (DocumentExtractionPort)."""

    def __init__(self, by_doc_id: dict[str, DocumentExtract]) -> None:
        self._by_doc_id = by_doc_id

    def extract(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> DocumentExtract:
        return self._by_doc_id.get(
            document.id,
            DocumentExtract(document_id=document.id, doc_type=document.doc_type, confidence=0.1),
        )


_DOC_ID_RE = re.compile(r"\[([a-zA-Z0-9][a-zA-Z0-9\-]*?)\]")


class FakeLLMAdapter:
    """Deterministic income normaliser (LLMPort): one salary figure per cited document."""

    def __init__(self) -> None:
        self.model = "gemini-3.7-flash"

    def generate(self, request: LlmRequest) -> LlmResponse:
        text = request.messages[-1].content if request.messages else ""
        doc_ids: list[str] = []
        for did in _DOC_ID_RE.findall(text):
            if did not in doc_ids and ("payslip" in did or "bank" in did or "tax" in did):
                doc_ids.append(did)
        figures = [
            {
                "source_doc_id": did,
                "amount": 6500.0,
                "currency": "SGD",
                "period": "monthly",
                "kind": "salary",
            }
            for did in doc_ids
        ]
        return LlmResponse(
            text=json.dumps({"figures": figures}),
            usage=TokenUsage(input_tokens=128, output_tokens=64, thinking_tokens=32),
            model=self.model,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        return labels[0] if labels else ""


class FakeGuardrailAdapter:
    """Always-allow guardrail with deterministic verdicts (GuardrailPort)."""

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        return GuardrailVerdict(
            allowed=True, direction=direction, findings=(), sanitized_text=text, reason="benign"
        )


def _real_redactor() -> LocalRegexRedactionAdapter:
    """The production local redactor, pinned to the gate's jurisdictions.

    Redaction is deliberately NOT faked. It stands for nothing external: the local adapter is
    the one the runtime uses, is pure regex over the shared pack, and needs no service or
    credential. Inlining a `FakeRedactionAdapter` that copy-pastes the real adapter's three
    regexes makes `pii_safety` score a redactor that does not have to agree with the shipped
    one: the copy could rot, or the real rows could be narrowed, and the gate would stay
    green either way. Everything else here is faked because it stands in for Document AI, an
    LLM, a tracer or an audit sink.
    """
    return LocalRegexRedactionAdapter(Settings(pii=PiiSettings(jurisdictions=_PII_JURISDICTIONS)))


class FakeTracer:
    @contextmanager
    def span(self, name: str, **attributes: str) -> Iterator[None]:
        yield

    def record_token_usage(self, usage: TokenUsage, model: str) -> None:
        return None


class FakeAuditSink:
    """In-memory WORM stand-in (AuditSinkPort); records are inspectable post-run."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def record(self, event: object) -> None:
        self.events.append(event)


class FakeEntitlementsAdapter:
    """Permissive object-owner stub (EntitlementsPort): every golden object is owned by the
    eval tenant. This gate measures extraction / validation / PII safety, NOT authorization,
    so it authorizes uniformly; the fail-closed object-authz gate is covered by the unit and
    API tests. Real profiles resolve ownership from a server-side ACL store."""

    def owner(self, object_id: str) -> ObjectOwner:
        return ObjectOwner(
            tenant="demo-bank",
            allowed_roles=frozenset({"group:loan-analyst", "group:underwriting"}),
        )


# The eval acts as an entitled demo-bank underwriter over its own golden objects.
_EVAL_PRINCIPAL = Principal(
    subject="eval-bot",
    principals=("group:loan-analyst", "group:underwriting"),
    tenant="demo-bank",
    source="eval",
)


# --------------------------------------------------------------------------- #
# Pipeline driver : drive the real LoanDocService over each golden case.
# --------------------------------------------------------------------------- #
@dataclass
class _CaseResult:
    case: GoldenCase
    verdict: str
    failed_kinds: set[str]
    audit_events: list[object]
    extracts: tuple[DocumentExtract, ...] = ()


def _with_pii(applicant: Applicant, case: GoldenCase) -> Applicant:
    """Plant the case's OWN jurisdiction identifier in the applicant (pii_safety).

    The applicant record is where this vertical's PII actually lives and where the pipeline
    actually redacts: ``_input_description`` builds the audited prompt out of the applicant's
    name and address, so that is where the fixture belongs. Each case carries its own
    market's identifier so the four configured packs are each exercised once, instead of the
    gate proving Singapore four times over.

    Planting here cannot move the other metrics, but for a reason worth stating because it is
    NOT the reason the sibling verticals have. B5's CrossValidator DOES read the applicant's
    name and address (NAME_MATCH / ADDRESS_MATCH compare them against the documents'), and it
    reads them RAW while the documents' copies arrive redacted, so an applicant carrying an
    NRIC would mismatch its own payslip and drop validation_precision to 0. The PII cases
    therefore carry documents that do not restate the name or address: with no document value
    to disagree with, ``_field_match`` sees the applicant's single value and PASSes. The
    account number goes on the bank statement's ``account_holder`` field, which
    ``_redact_extract`` redacts but no check reads, so the extract-redaction call site is
    covered too. See the golden dataset's header comment.
    """
    if not case.pii_in_inputs:
        return applicant
    market = case.jurisdiction.upper()
    fixture = _PII_BY_JURISDICTION.get(market)
    if fixture is None:
        # Loud, not silent: a case that claims to carry PII but has no fixture for its
        # jurisdiction would quietly test the universal rows only and look like real
        # per-market coverage.
        raise ValueError(
            f"golden case {case.id!r} sets pii_in_inputs in jurisdiction {market!r}, "
            "which has no fixture in _PII_BY_JURISDICTION. Add one so the case "
            "exercises that jurisdiction's pack."
        )
    if market not in _PII_JURISDICTIONS:
        # Scoring the leak check off the same pack as the redactor is what stops the two
        # drifting apart, but it also means a jurisdiction missing from the config blinds
        # BOTH at once: nothing masks the id, nothing detects it, and the case scores a
        # vacuous 1.0. Refuse to run rather than report that as coverage.
        raise ValueError(
            f"golden case {case.id!r} carries {market} PII but {market} is not in the "
            f"configured pack {_PII_JURISDICTIONS}. The redactor would not mask it and "
            "the leak check would not see it, so the case would score a vacuous 1.0. "
            f"Add it to LOAN_DOC_PII_JURISDICTIONS or drop pii_in_inputs."
        )
    label, value = fixture
    return replace(
        applicant,
        name=f"{applicant.name}, {label} {value}",
        address=f"{applicant.address}, account {_ACCOUNT_FIXTURE}, {_EMAIL_FIXTURE}",
    )


def _with_pii_extracts(
    by_doc_id: dict[str, DocumentExtract], case: GoldenCase
) -> dict[str, DocumentExtract]:
    """Plant the case's identifiers on the bank statement's ``account_holder`` field.

    The pipeline redacts in TWO places and :func:`_with_pii` only reaches one of them. This
    is the other: ``_extract_and_redact`` de-identifies each extract's free-text identity
    fields before they reach the model. ``account_holder`` is in that redactable set and no
    deterministic check reads it, so the fixture proves that call site without moving
    validation_recall / precision. A PII case with no bank statement to carry it raises
    rather than silently proving only the audit path.
    """
    if not case.pii_in_inputs:
        return by_doc_id
    label, value = _PII_BY_JURISDICTION[case.jurisdiction.upper()]
    planted = dict(by_doc_id)
    statements = [
        doc_id for doc_id, extract in planted.items() if extract.doc_type is DocType.BANK_STATEMENT
    ]
    if not statements:
        raise ValueError(
            f"golden case {case.id!r} sets pii_in_inputs but carries no bank_statement "
            "extract to plant an account_holder on, so the extract-redaction call site "
            "would go unproven. Add one."
        )
    for doc_id in statements:
        extract = planted[doc_id]
        fields = dict(extract.fields)
        fields["account_holder"] = f"{label} {value}, account {_ACCOUNT_FIXTURE}"
        planted[doc_id] = replace(extract, fields=fields)
    return planted


def _applicant_from(obj: dict) -> Applicant:
    declared = obj.get("declared_income")
    figure = (
        IncomeFigure(
            source_doc_id="declared",
            amount=float(declared["amount"]),
            currency=str(declared.get("currency", "SGD")),
            period=IncomePeriod(str(declared.get("period", "monthly"))),
            kind=IncomeKind(str(declared.get("kind", "salary"))),
        )
        if declared
        else None
    )
    return Applicant(
        id=str(obj["id"]),
        name=str(obj.get("name", "")),
        address=str(obj.get("address", "")),
        declared_income=figure,
    )


def _run_case(case: GoldenCase) -> _CaseResult:
    from loan_doc_intel.domain.loan_doc_service import LoanDocService

    by_doc_id = _with_pii_extracts(
        {e["document_id"]: _build_extract(e) for e in case.extracts}, case
    )
    audit = FakeAuditSink()
    service = LoanDocService(
        extraction=FakeExtractionAdapter(by_doc_id),
        llm=FakeLLMAdapter(),
        guardrail=FakeGuardrailAdapter(),
        redaction=_real_redactor(),
        tracer=FakeTracer(),
        audit=audit,
        entitlements=FakeEntitlementsAdapter(),
    )
    applicant = _with_pii(_applicant_from(case.applicant), case)
    documents = [
        ApplicantDocument(
            id=str(d["id"]),
            doc_type=DocType(str(d.get("doc_type", "payslip"))),
            uri=str(d.get("uri", "")),
        )
        for d in case.documents
    ]
    result = service.process(applicant, documents, _EVAL_PRINCIPAL)
    verdict = result.income.verdict.value if result.income else "unknown"
    failed_kinds = (
        {c.kind.value for c in result.validation.checks if c.status is CheckStatus.FAIL}
        if result.validation
        else set()
    )
    return _CaseResult(case, verdict, failed_kinds, audit.events, result.extracts)


# --------------------------------------------------------------------------- #
# Heuristic scorers
# --------------------------------------------------------------------------- #
def score_extraction_accuracy(case: GoldenCase) -> float:
    """Fraction of expected documents that produced a non-empty extract."""
    expected = {e["document_id"] for e in case.extracts}
    produced = {e["document_id"] for e in case.extracts if e.get("fields") or e.get("line_items")}
    if not expected:
        return 1.0
    return round(len(produced & expected) / len(expected), 4)


def score_validation_recall(result: _CaseResult) -> float | None:
    """Of the planted inconsistencies, the fraction the validator caught."""
    expected = set(result.case.expected_failed_checks)
    if not expected:
        return None  # consistent case: recall is not applicable
    return round(len(expected & result.failed_kinds) / len(expected), 4)


def score_validation_precision(result: _CaseResult) -> float | None:
    """For a consistent case (no expected failures), 0 if any check falsely FAILED."""
    if result.case.expected_failed_checks:
        return None  # inconsistent case: precision measured on consistent cases
    return 0.0 if result.failed_kinds else 1.0


def score_verdict_match(result: _CaseResult) -> float:
    """1.0 when the derived verdict matches the expected verdict (sanity)."""
    return 1.0 if result.verdict == result.case.expected_verdict else 0.0


def _pii_leak(text: str) -> bool:
    """True if any national id / email / phone / account number survives ``text``.

    Validator-gated for the same reason the redactor is, so what this detects is exactly
    what the redactor masks: the two read the same rows from ``domain/pii_patterns.py``.
    That shared source is what stops a bespoke detector and a bespoke redactor drifting
    apart, but on its own it can only catch PII the pipeline RE-INTRODUCED after redaction:
    it is blind by construction to anything the pack itself gets wrong, since a row that
    fails to match cannot detect what it failed to mask. :func:`_planted_pii_leak` is the
    independent half of the check.
    """
    for _info_type, pattern, validator in _PII_PATTERNS:
        for match in pattern.finditer(text or ""):
            if validator is None or validator(match.group(0)):
                return True
    return False


def _planted_pii_leak(text: str, case: GoldenCase) -> bool:
    """True if the identifier this case planted survives ``text`` verbatim.

    The pack-INDEPENDENT half of ``pii_safety``, and the half that makes the per-market
    claim real. This gate always had a literal check like this one, but it scored a fake
    redactor that masked those same literals, which made the metric a closed loop that could
    not go red. Against the REAL redactor a literal is an oracle the pack has no say in: if a
    row is narrowed, mis-escaped or deleted outright, the redactor silently stops masking
    that market AND :func:`_pii_leak` silently stops detecting it, so only this check still
    fails. Without it a broken pack scores a vacuous 1.0 with the raw identifier sitting in
    the WORM audit record.
    """
    if not case.pii_in_inputs:
        return False
    _label, value = _PII_BY_JURISDICTION[case.jurisdiction.upper()]
    return any(token in (text or "") for token in (value, _ACCOUNT_FIXTURE, _EMAIL_FIXTURE))


def score_pii_safety(result: _CaseResult) -> float:
    """1.0 unless unredacted applicant PII survived into the audit records or the extracts.

    Scans both surfaces two ways: with the same jurisdiction pattern source as the runtime
    redactor (catches PII the pipeline re-introduced), and for this case's own planted
    identifier (catches the pack being wrong, which the first check cannot see). A single
    surviving SG NRIC / HK HKID / JP My Number / AU TFN / email / account number drops the
    metric to 0.0, so the gate fails if anything bypassed the redact-before-everything
    boundary (R1, P-04).

    Both scanned surfaces are DERIVED, never an echo of the caller's input, and they are the
    two places the domain service actually redacts: the audit records are what outlives the
    request (``_input_description`` -> ``redact`` -> ``audit``), and the extracts are what
    reaches the model (``_extract_and_redact``). Scanning a field that simply repeats what
    the case planted would make the metric red whenever PII is injected no matter how well
    redaction worked, measuring the fixture instead of the boundary.
    """
    haystacks: list[str] = []
    for event in result.audit_events:
        haystacks.append(str(getattr(event, "redacted_prompt", "") or ""))
        haystacks.append(str(getattr(event, "redacted_response", "") or ""))
    for extract in result.extracts:
        haystacks.extend(str(v) for v in extract.fields.values())
    leaked = any(_pii_leak(hay) or _planted_pii_leak(hay, result.case) for hay in haystacks)
    return 0.0 if leaked else 1.0


# --------------------------------------------------------------------------- #
# Report assembly + presentation
# --------------------------------------------------------------------------- #
@dataclass
class _PerMetric:
    scores: list[float] = field(default_factory=list)

    @property
    def mean(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 1.0


def run_offline(dataset: Path, thresholds: dict[str, float]) -> EvalReport:
    cases = load_golden(dataset)
    agg: dict[str, _PerMetric] = {m: _PerMetric() for m in THRESHOLDS}
    print(f"Running offline eval gate over {len(cases)} golden cases (LoanDocService).\n")

    verdict_hits = 0
    for case in cases:
        result = _run_case(case)
        agg["extraction_accuracy"].scores.append(score_extraction_accuracy(case))
        recall = score_validation_recall(result)
        if recall is not None:
            agg["validation_recall"].scores.append(recall)
        precision = score_validation_precision(result)
        if precision is not None:
            agg["validation_precision"].scores.append(precision)
        agg["pii_safety"].scores.append(score_pii_safety(result))
        verdict_hits += int(score_verdict_match(result) == 1.0)

    print(f"  verdict agreement: {verdict_hits}/{len(cases)} cases\n")

    results = tuple(
        EvalMetricResult(
            metric=metric,
            score=round(agg[metric].mean, 4),
            threshold=thresholds.get(metric, THRESHOLDS[metric]),
            passed=round(agg[metric].mean, 4) >= thresholds.get(metric, THRESHOLDS[metric]),
        )
        for metric in (
            "extraction_accuracy",
            "validation_recall",
            "validation_precision",
            "pii_safety",
        )
    )
    return EvalReport(dataset=str(dataset), results=results, n_examples=len(cases))


def run_gate(dataset: Path) -> tuple[EvalReport, bool]:
    """Promotion verdict via EvaluationGatePort (platform = Hrz4, gcp = Gen AI evals).

    Fails closed on the reconciled evaluate + gate result. Refuses to run outside the
    platform/gcp profiles so the offline smoke result is never relabelled a promotion pass.
    """
    from loan_doc_intel.config import Settings, build_container

    settings = Settings.load()
    if settings.profile not in ("platform", "gcp"):
        raise SystemExit(
            "--mode gate is the promotion authority and requires "
            "LOAN_DOC_PROFILE=platform or gcp "
            f"(got {settings.profile!r}); run --mode smoke for the offline pre-merge check."
        )
    container = build_container(settings)
    gate = container.evaluation
    report = gate.evaluate(str(dataset))
    if not isinstance(report, EvalReport):  # pragma: no cover - defensive
        raise SystemExit("EvaluationGatePort.evaluate did not return an EvalReport")
    gate_passed = bool(gate.gate(str(dataset)))
    return report, gate_passed


def main(argv: list[str] | None = None) -> int:
    """Dispatch --mode via the shared eval_main scaffold (fail-closed exit codes).

    ``--use-gcp`` (the pre-split flag for the production evaluator) is kept as an alias
    for ``--mode gate``.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if "--use-gcp" in args:
        args = [a for a in args if a != "--use-gcp"] + ["--mode", "gate"]
    return eval_main(
        smoke=lambda dataset: run_offline(dataset, load_thresholds_from_rubrics()),
        gate=run_gate,
        default_dataset=DEFAULT_DATASET,
        description="Offline / platform evaluation gate for B5 (A4 / P-08).",
        smoke_label="offline heuristic (no GCP creds)",
        gate_label="promotion gate (EvaluationGatePort: Hrz4 / Gen AI evals)",
        argv=args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
