"""Pytest fixtures: the ``local`` adapters (seeded) + assembled domain services.

The unit suite is driven by the **real** ``local`` adapter family
(``src/loan_doc_intel/adapters/local``) rather than bespoke in-memory fakes, so the
offline implementation lives in exactly one place and the tests exercise the same code the
offline CLI runs. Every adapter constructs with a single ``Settings`` (the adapter
convention) pointed at ``:memory:`` for the append-only audit store, and the extraction
adapter is seeded with the synthetic ``tests/fixtures/sample_docs`` extracts for
determinism.

A few fixtures wrap the local adapter in a thin **recording** subclass that captures call
arguments for assertions (``.calls`` / ``.requests`` / ``.spans`` / ``.events``). These add
no behaviour: every method delegates to the real local adapter, so the in-memory
implementation is still the one under ``adapters/local``. The recorders carry the test
instrumentation and nothing else.

``BlockingGuardrail`` is the one deliberately-forcing test double kept here: a thin
``GuardrailPort`` that blocks a chosen direction so the blocked-path tests can drive the
pipeline with the benign sample documents (the real local heuristic would pass them). It
reproduces a fixed verdict; it is test instrumentation, not a registered adapter.

The local LLM is schema-driven (it reads ``request.response_schema`` and returns a JSON
object whose keys match it, parsing the document ids out of the rendered EXTRACTS block),
so it stays correct whatever field names the services declare.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from loan_doc_intel.adapters.local.audit import LocalAppendOnlyAuditAdapter
from loan_doc_intel.adapters.local.entitlements import LocalEntitlementsAdapter
from loan_doc_intel.adapters.local.evaluation import LocalOfflineEvalAdapter
from loan_doc_intel.adapters.local.extraction import LocalExtractionAdapter
from loan_doc_intel.adapters.local.guardrail import LocalHeuristicGuardrailAdapter
from loan_doc_intel.adapters.local.llm import LocalDeterministicLLMAdapter
from loan_doc_intel.adapters.local.memory import LocalMemoryAdapter
from loan_doc_intel.adapters.local.redaction import LocalRegexRedactionAdapter
from loan_doc_intel.adapters.local.registry import LocalRegistryAdapter
from loan_doc_intel.adapters.local.runtime import LocalAgentRuntimeAdapter
from loan_doc_intel.adapters.local.session import LocalSessionAdapter
from loan_doc_intel.adapters.local.tool_catalog import LocalToolCatalogAdapter
from loan_doc_intel.adapters.local.tracer import LocalNoopTracerAdapter
from loan_doc_intel.config import LocalSettings, Settings
from loan_doc_intel.domain.models import (
    ApplicantDocument,
    AuditEvent,
    Direction,
    DocumentExtract,
    GuardrailCategory,
    GuardrailFinding,
    GuardrailVerdict,
    LlmRequest,
    LlmResponse,
)
from tests.fixtures import sample_docs


def _settings() -> Settings:
    """Settings whose local audit store is an ephemeral in-memory SQLite (deterministic)."""
    return Settings(profile="local", local=LocalSettings(audit_path=":memory:"))


# --------------------------------------------------------------------------- #
# Recording wrappers : thin subclasses of the local adapters that capture call
# arguments for assertions. Every method delegates to the real local adapter.
# --------------------------------------------------------------------------- #
class RecordingExtraction(LocalExtractionAdapter):
    """Local extraction that records each document id it was asked to extract."""

    def __init__(self, settings: Settings, extracts: list[DocumentExtract] | None = None) -> None:
        super().__init__(settings)
        # Re-seed the canned store with the requested scenario (consistent by default).
        self.seed(sample_docs.consistent_extracts() if extracts is None else extracts)
        self.calls: list[str] = []

    def extract(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> DocumentExtract:
        self.calls.append(document.id)
        return super().extract(document, content, mime_type)


class RecordingLLM(LocalDeterministicLLMAdapter):
    """Local deterministic LLM that records the requests it received."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.requests: list[LlmRequest] = []
        self.classify_calls: list[tuple[str, list[str]]] = []

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return super().generate(request)

    def classify(self, text: str, labels: list[str]) -> str:
        self.classify_calls.append((text, labels))
        return super().classify(text, labels)


class RecordingRedaction(LocalRegexRedactionAdapter):
    """Local regex redaction that records the raw text it was asked to redact."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[str] = []

    def redact(self, text: str):  # type: ignore[no-untyped-def]
        self.calls.append(text)
        return super().redact(text)


class RecordingTracer(LocalNoopTracerAdapter):
    """Local no-op tracer that records the span names it opened."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.spans: list[str] = []

    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.spans.append(name)
        return super().span(name, **attributes)


class RecordingGuardrail(LocalHeuristicGuardrailAdapter):
    """Local heuristic guardrail that records the (text, direction) screen calls.

    Behaviour is the real heuristic: benign text passes, malicious text (e.g. a
    prompt-injection string) is blocked. Only the recording is added.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[tuple[str, Direction]] = []

    def screen(self, text: str, direction: Direction):  # type: ignore[no-untyped-def]
        self.calls.append((text, direction))
        return super().screen(text, direction)


class RecordingAudit(LocalAppendOnlyAuditAdapter):
    """Local append-only audit that also keeps the AuditEvent objects for assertions."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)
        super().record(event)


class BlockingGuardrail:
    """Forcing test double: blocks a chosen direction so blocked-path tests use benign docs.

    The real local heuristic would pass the sample documents, so the blocked-input /
    blocked-output tests drive this thin ``GuardrailPort`` instead. It reproduces a fixed
    deterministic verdict; it is test instrumentation, not a registered adapter.
    """

    def __init__(self, block_input: bool = True, block_output: bool = False) -> None:
        self.block_input = block_input
        self.block_output = block_output
        self.calls: list[tuple[str, Direction]] = []

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        self.calls.append((text, direction))
        blocked = (direction is Direction.INPUT and self.block_input) or (
            direction is Direction.OUTPUT and self.block_output
        )
        return GuardrailVerdict(
            allowed=not blocked,
            direction=direction,
            findings=(
                (
                    GuardrailFinding(
                        category=GuardrailCategory.PROMPT_INJECTION,
                        confidence="high",
                        detail="prompt injection detected",
                    ),
                )
                if blocked
                else ()
            ),
            sanitized_text=None if blocked else text,
            reason="blocked by guardrail" if blocked else "ok",
        )


# --------------------------------------------------------------------------- #
# Service / policy resolvers : locate the domain classes wherever they live.
# --------------------------------------------------------------------------- #
_SERVICE_MODULE_CANDIDATES = (
    "loan_doc_intel.domain.loan_doc_service",
    "loan_doc_intel.domain.cross_validator",
    "loan_doc_intel.domain.income_service",
    "loan_doc_intel.domain.services",
)
_POLICY_MODULE_CANDIDATES = (
    "loan_doc_intel.domain.hitl",
    "loan_doc_intel.domain.services",
)


def _resolve(symbol: str, candidates: tuple[str, ...]) -> Any:
    last: Exception | None = None
    for mod_name in candidates:
        try:
            module = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:  # pragma: no cover - layout fallback
            last = exc
            continue
        obj = getattr(module, symbol, None)
        if obj is not None:
            return obj
    raise ImportError(f"Could not locate domain symbol {symbol!r} in any of {candidates}") from last


def load_service(name: str) -> Any:
    return _resolve(name, _SERVICE_MODULE_CANDIDATES)


def load_policy(name: str) -> Any:
    return _resolve(name, _POLICY_MODULE_CANDIDATES)


# --------------------------------------------------------------------------- #
# Pytest fixtures : construct the (seeded) local adapters.
# --------------------------------------------------------------------------- #
@pytest.fixture
def extraction() -> RecordingExtraction:
    return RecordingExtraction(_settings())


@pytest.fixture
def inconsistent_extraction() -> RecordingExtraction:
    return RecordingExtraction(_settings(), extracts=sample_docs.inconsistent_extracts())


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM(_settings())


@pytest.fixture
def guardrail() -> RecordingGuardrail:
    return RecordingGuardrail(_settings())


@pytest.fixture
def blocking_guardrail() -> BlockingGuardrail:
    return BlockingGuardrail()


@pytest.fixture
def redaction() -> RecordingRedaction:
    return RecordingRedaction(_settings())


@pytest.fixture
def tracer() -> RecordingTracer:
    return RecordingTracer(_settings())


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit(_settings())


@pytest.fixture
def session() -> LocalSessionAdapter:
    return LocalSessionAdapter(_settings())


@pytest.fixture
def memory() -> LocalMemoryAdapter:
    return LocalMemoryAdapter(_settings())


@pytest.fixture
def agent_runtime() -> LocalAgentRuntimeAdapter:
    return LocalAgentRuntimeAdapter(_settings())


@pytest.fixture
def evaluation() -> LocalOfflineEvalAdapter:
    return LocalOfflineEvalAdapter(_settings())


@pytest.fixture
def registry() -> LocalRegistryAdapter:
    return LocalRegistryAdapter(_settings())


@pytest.fixture
def tool_catalog() -> LocalToolCatalogAdapter:
    return LocalToolCatalogAdapter(_settings())


@pytest.fixture
def entitlements() -> LocalEntitlementsAdapter:
    """Seeded local object-owner registry (fixture applications owned by demo-bank)."""
    return LocalEntitlementsAdapter(_settings())


@pytest.fixture
def loan_doc_service(extraction, llm, guardrail, redaction, tracer, audit, entitlements):
    """LoanDocService(extraction, llm, guardrail, redaction, tracer, audit, entitlements)."""
    cls = load_service("LoanDocService")
    return cls(extraction, llm, guardrail, redaction, tracer, audit, entitlements)
