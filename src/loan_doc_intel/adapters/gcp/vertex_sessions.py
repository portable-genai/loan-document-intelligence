"""Agent Platform Sessions adapter (SessionPort) : per-application conversation state.

Backs the domain ``SessionPort`` with the **Agent Platform Sessions** service on the
Gemini Enterprise Agent Platform. A session scopes the per-application underwriting
context so the agent can be queried statefully.

The Vertex AI SDK import is lazy so the on-prem and test profiles import without it.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import LlmMessage, Session, utcnow


class VertexSessionsAdapter:
    """Create and read per-application sessions via Agent Platform Sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service: Any | None = None

    def _get_service(self) -> Any:
        if self._service is None:
            from vertexai import agent_engines  # lazy

            # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/sessions
            self._service = agent_engines.get(self._settings.agent_engine.resource_name)
        return self._service

    def create_session(self, user_id: str, application_id: str | None = None) -> Session:
        service = self._get_service()
        created = service.create_session(user_id=user_id)
        session_id = getattr(created, "id", None) or getattr(created, "name", "session")
        return Session(id=str(session_id), user_id=user_id, application_id=application_id)

    def get(self, session_id: str) -> Session | None:
        service = self._get_service()
        raw = service.get_session(session_id=session_id)
        if raw is None:
            return None
        return Session(
            id=session_id,
            user_id=str(getattr(raw, "user_id", "")),
            application_id=getattr(raw, "application_id", None),
            created_at=utcnow(),
        )

    def append(self, session_id: str, message: LlmMessage) -> None:
        service = self._get_service()
        service.append_event(
            session_id=session_id,
            event={"role": message.role, "content": message.content},
        )

    def history(self, session_id: str) -> list[LlmMessage]:
        service = self._get_service()
        events = service.list_events(session_id=session_id) or []
        return [
            LlmMessage(role=str(e.get("role", "user")), content=str(e.get("content", "")))
            for e in events
        ]
