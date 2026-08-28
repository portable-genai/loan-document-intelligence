"""Vertical-neutral domain kernel: the contracts a fork never edits.

This module is the **physical** kernel seam, not a re-export shim. It owns the
vertical-neutral machinery : provenance and citations, the LLM envelope, safety
verdicts, the audit record, the evaluation report, agent-discovery cards, tool specs
and the shared severity scale : and it depends on **nothing** inside this package. In
particular it does not import :mod:`loan_doc_intel.domain.models`; the dependency runs
the other way, so a fork can lift this module (and the ports typed against it) and
rewrite the loan / applicant / income / document artifacts in ``models`` without
touching a line here.

Like ``models`` it is standard-library only (plus the shared commons), with no
dependency on Google Cloud, ADK or FastAPI.

The dependency direction is proved by execution in
``tests/unit/test_kernel_boundary.py``: a fresh interpreter imports this module and
asserts ``loan_doc_intel.domain.models`` never enters ``sys.modules``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Shared value types, IMPORTED rather than redeclared. Sixteen repositories had each
# hand-copied these, and by the time anyone compared them they had drifted: that is the whole
# defect class, and re-exporting the one definition retires it. The redundant `as` aliases are
# deliberate; without them ruff F401 flags the names this module re-exports but does not itself
# use. The submodule import path (`agent_eval_kit.report`, not the package root) is deliberate
# too: the package root pulls in the httpx-backed gate client, and this module promises the
# domain kernel stays stdlib-only.
from agent_eval_kit.report import EvalMetricResult as EvalMetricResult
from agent_eval_kit.report import EvalReport as EvalReport
from hex_service_kit import StrEnum as StrEnum
from hex_service_kit.observability import TokenUsage as TokenUsage


def utcnow() -> datetime:
    """Timezone-aware UTC now : the single clock the domain uses."""
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Citation (evidence points at a source + field)
# --------------------------------------------------------------------------- #
class SourceType(StrEnum):
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class Citation:
    """Evidence-grade provenance attached to every extracted figure and check.

    Field-level citation is a hard requirement: a verified income figure must point at
    the exact source document, page and field so an underwriter can verify it.
    """

    source_id: str  # the document_id the evidence came from
    source_type: SourceType = SourceType.DOCUMENT
    title: str = ""  # human label for the document, e.g. "Payslip 2026-04"
    page: int | None = None
    field: str = ""  # the document field / line label the evidence is drawn from
    snippet: str = ""


# --------------------------------------------------------------------------- #
# Generation (LLM) : normalises / explains, never overrides a deterministic check
# --------------------------------------------------------------------------- #
class ThinkingLevel(StrEnum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: str  # "user" | "model" | "system"
    content: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    messages: tuple[LlmMessage, ...]
    system_instruction: str | None = None
    model: str | None = None  # None => adapter default from config
    thinking: ThinkingLevel = ThinkingLevel.MEDIUM
    temperature: float = 0.0  # omitted at a call site means this value; it must not sample
    max_output_tokens: int = 4096
    response_schema: dict | None = None  # JSON schema for structured output


# ``TokenUsage`` is not declared in ``models``: three ``int`` fields defaulting to zero is a
# shared value type, byte-identical wherever it is spelled out, so it comes from
# ``hex_service_kit.observability`` (imported at the top of this module) and this repo has
# nothing left to drift.


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    raw: dict | None = None


# --------------------------------------------------------------------------- #
# Safety (guardrail + PII redaction) : A1 Guardrail Gateway concerns (R1)
# --------------------------------------------------------------------------- #
class GuardrailCategory(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    SENSITIVE_DATA = "sensitive_data"
    MALICIOUS_URL = "malicious_url"
    HATE = "hate"
    HARASSMENT = "harassment"
    SEXUAL = "sexual"
    DANGEROUS = "dangerous"
    OTHER = "other"


class Direction(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    category: GuardrailCategory
    confidence: str  # e.g. "low" | "medium" | "high"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    allowed: bool
    direction: Direction
    findings: tuple[GuardrailFinding, ...] = ()
    # Text after any inline sanitisation the guardrail applied (may equal input).
    sanitized_text: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RedactionFinding:
    info_type: str  # e.g. "PERSON_NAME", "SG_NRIC_FIN", "BANK_ACCOUNT_NUMBER"
    count: int = 1


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str  # de-identified text safe to send to the model / audit log
    findings: tuple[RedactionFinding, ...] = ()

    @property
    def redacted(self) -> bool:
        return bool(self.findings)


# --------------------------------------------------------------------------- #
# Audit & observability : A5 Observability, Audit & FinOps concerns (R2)
# --------------------------------------------------------------------------- #
class Decision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"  # routed to a human (maker-checker)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, WORM-stored record of one document-intelligence interaction.

    Prompt and response are stored **already redacted** (P-04): applicant PII (names,
    NRIC, bank accounts, income) is removed at the boundary before it is ever written
    to the audit sink or a trace span.
    """

    action: str  # "process" | "extract" | "validate"
    actor: str  # authenticated underwriter / service identity
    decision: Decision
    redacted_prompt: str
    redacted_response: str
    citations: tuple[Citation, ...] = ()
    resource: str = "loan-document-intelligence"
    trace_id: str | None = None
    timestamp: datetime = field(default_factory=utcnow)
    metadata: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Evaluation gate : A4 AI Quality & Model-Risk concerns (R5)
# --------------------------------------------------------------------------- #
# ``EvalMetricResult`` and ``EvalReport`` are not declared in ``models``. They come from
# ``agent_eval_kit.report`` (imported at the top of this module), which carries the SAME four
# metric fields and the same fail-closed ``passed`` rule -- ``n_examples > 0 and bool(results)
# and all(...)``, because ``all(())`` is vacuously True and a report that scored nothing must
# never certify a promotion -- plus the durable evidence fields a hand-copied report drops on
# the floor: ``run_id``, ``dataset_version``, ``dataset_digest``, ``evaluator``,
# ``schema_version``, ``trace_id``, ``correlation_id``, ``artifact_refs`` and ``attested``. All
# of those default, so a constructor naming only the four metric fields still compiles, and the
# remote adapter never has to throw the evidence away to fit a narrower local type.


# --------------------------------------------------------------------------- #
# Governance : A3 Agent Registry & Governance concerns (A2A AgentCard) (R4)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AgentSkill:
    id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AgentCard:
    """Minimal A2A-style agent card published at /.well-known/agent-card.json."""

    name: str
    description: str
    url: str
    version: str
    skills: tuple[AgentSkill, ...] = ()
    provider: str = "loan-document-intelligence"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A governed, least-privilege tool exposed to the agent (typically via MCP)."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Shared severity scale
# --------------------------------------------------------------------------- #
class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


__all__ = [
    "AgentCard",
    "AgentSkill",
    "AuditEvent",
    "Citation",
    "Decision",
    "Direction",
    "EvalMetricResult",
    "EvalReport",
    "GuardrailCategory",
    "GuardrailFinding",
    "GuardrailVerdict",
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "RedactionFinding",
    "RedactionResult",
    "Severity",
    "SourceType",
    "StrEnum",
    "ThinkingLevel",
    "TokenUsage",
    "ToolSpec",
    "utcnow",
]
