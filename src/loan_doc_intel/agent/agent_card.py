"""A2A AgentCard for the B5 Loan Document Intelligence service (A3 Registry).

This builds the service's discovery card : the same minimal A2A shape the
``agent-registry`` service stores and serves. It is published at
``/.well-known/agent-card.json``; :func:`agent_card_document` returns the JSON-safe body
the API layer serves there.

The card advertises exactly the three skills B5 exposes (process_application,
extract_document, cross_validate), mirroring the ADK FunctionTools so a peer agent or the
registry sees one consistent capability surface.

This module is pure (domain models only) and imports without ADK or any Google Cloud SDK
installed (SPEC §4).
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..domain.models import AgentCard, AgentSkill

SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        id="process_application",
        name="Process loan application",
        description=(
            "Extract income and bank-statement data from an applicant's documents and "
            "cross-validate them deterministically into a cited income verification with a "
            "verdict (VERIFIED / NEEDS_REVIEW / INCONSISTENT). Flagged for human review."
        ),
    ),
    AgentSkill(
        id="extract_document",
        name="Extract document",
        description=(
            "Extract structured fields and dated line items from a single applicant "
            "document (payslip, bank statement, tax return, employment letter) via Document AI."
        ),
    ),
    AgentSkill(
        id="cross_validate",
        name="Cross-validate documents",
        description=(
            "Run the deterministic consistency checks across an applicant's extracts: "
            "salary-credit match, income consistency, name/address match, balance trend, "
            "affordability. Each check is PASS / WARN / FAIL with field-level evidence."
        ),
    ),
)

_DESCRIPTION = (
    "B5 Loan / Mortgage Document Intelligence : a document-intelligence agent for retail "
    "lending underwriting. It extracts income and bank-statement data (Document AI) and "
    "runs deterministic cross-validation across an applicant's documents, producing a "
    "cited, audited, maker-checker-gated income verification. Decision-support for "
    "underwriting, not a lending decision. Built ports-and-adapters on the Gemini "
    "Enterprise Agent Platform (region asia-southeast1)."
)


def build_agent_card(settings: Settings) -> AgentCard:
    """Construct the A2A :class:`AgentCard` for this service."""
    base_url = _resolve_url(settings)
    return AgentCard(
        name="loan-document-intelligence",
        description=_DESCRIPTION,
        url=base_url,
        version="0.1.0",
        skills=SKILLS,
        provider="loan-document-intelligence",
    )


def agent_card_document(settings: Settings) -> dict[str, Any]:
    """Return the JSON-safe body to serve at ``/.well-known/agent-card.json``."""
    from ..domain.serialization import to_jsonable

    return to_jsonable(build_agent_card(settings))


def _resolve_url(settings: Settings) -> str:
    """Best-effort public URL for the card, region-pinned to asia-southeast1."""
    resource = settings.agent_engine.resource_name
    if resource:
        return f"https://aiplatform.googleapis.com/v1/{resource}"
    return "https://loan-document-intelligence.internal/a2a"
