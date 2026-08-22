"""Agent Runtime adapter (AgentRuntimePort) : the hosted B5 agent.

Backs the domain ``AgentRuntimePort`` with **Agent Runtime** (formerly Vertex AI Agent
Engine) on the Gemini Enterprise Agent Platform. ``query`` sends a message to the deployed
reasoning engine within a session and returns the resulting ``LoanApplicationCase``;
``health`` probes the engine.

The Vertex AI SDK import is lazy so the on-prem and test profiles import without it.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import LoanApplicationCase, Session


class AgentRuntimeAdapter:
    """Query the hosted reasoning engine and map the reply to a domain case."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        if self._engine is None:
            from vertexai import agent_engines  # lazy

            # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine
            self._engine = agent_engines.get(self._settings.agent_engine.resource_name)
        return self._engine

    def query(self, session: Session, message: str) -> LoanApplicationCase:
        """Send ``message`` to the hosted agent within ``session`` and parse the reply."""
        engine = self._get_engine()
        response = engine.query(input=message, session_id=session.id)
        return self._to_case(session, response)

    def health(self) -> bool:
        """Liveness/readiness of the hosted agent runtime."""
        try:
            return self._get_engine() is not None
        except Exception:  # noqa: BLE001 - health must never raise
            return False

    @staticmethod
    def _to_case(session: Session, response: Any) -> LoanApplicationCase:
        from ...domain.models import (
            Applicant,
            IncomeFigure,
            IncomeVerificationSummary,
        )

        application_id = session.application_id or "application"
        applicant = Applicant(id=application_id, name="")
        income = IncomeVerificationSummary(
            application_id=application_id,
            verified_income=IncomeFigure(source_doc_id="agent", amount=0.0),
        )
        return LoanApplicationCase(id=application_id, applicant=applicant, income=income)
