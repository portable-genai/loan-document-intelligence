"""A2A registry adapter : agent discovery and governance for system B5 (A3).

Backs the domain ``AgentRegistryPort`` with an in-process, **A2A v1.0**-style registry of
``AgentCard`` objects. In a standalone deployment B5 registers its own card here and can
serve it at the well-known A2A discovery path; inside the full platform the ``platform``
profile swaps this for a thin client to ``agent-registry``.

A2A discovery contract: an agent publishes its capabilities as an **AgentCard** served at
``/.well-known/agent-card.json``; peers fetch that card to learn the agent's skills,
endpoint URL and version before initiating an A2A task. No external call is required :
this adapter is pure, in-memory governance.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AgentCard, AgentSkill

AGENT_CARD_PATH = "/.well-known/agent-card.json"

_B5_SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        id="process_application",
        name="Process loan application",
        description=(
            "Extract income and bank-statement data from an applicant's documents and "
            "cross-validate them deterministically into a cited income verification."
        ),
    ),
    AgentSkill(
        id="extract_document",
        name="Extract document",
        description="Extract structured fields and line items from one applicant document.",
    ),
    AgentSkill(
        id="cross_validate",
        name="Describe the cross-validation checks",
        description=(
            "Name the deterministic consistency checks B5 applies (salary-credit match, "
            "name/address, balance trend, affordability). process_application runs them."
        ),
    ),
)


class A2ARegistryAdapter:
    """In-process A2A AgentCard registry: register / get / list, plus card export."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cards: dict[str, AgentCard] = {}
        self.register(self._self_card())

    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def list(self) -> list[AgentCard]:
        return list(self._cards.values())

    def agent_card_dict(self, name: str | None = None) -> dict:
        """Return the ``/.well-known/agent-card.json`` body for ``name``."""
        card = self.get(name) if name else self._cards.get(self._self_name())
        if card is None:
            raise KeyError(f"No AgentCard registered for '{name}'.")
        return {
            "name": card.name,
            "description": card.description,
            "url": card.url,
            "version": card.version,
            "provider": card.provider,
            "skills": [
                {"id": s.id, "name": s.name, "description": s.description} for s in card.skills
            ],
        }

    def _self_name(self) -> str:
        return self._settings.agent_engine.display_name or "loan-document-intelligence"

    def _self_card(self) -> AgentCard:
        return AgentCard(
            name=self._self_name(),
            description=(
                "B5 Loan / Mortgage Document Intelligence : Document AI extraction + "
                "deterministic cross-validation of applicant income and bank-statement "
                "data, with field-level citations. Decision-support for underwriting."
            ),
            url=f"https://loan-document-intelligence.{self._settings.region}.example/a2a",
            version="1.0.0",
            skills=_B5_SKILLS,
            provider="loan-document-intelligence",
        )
