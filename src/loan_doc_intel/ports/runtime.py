"""Runtime ports : hosting, conversation state and long-term memory.

Primary GCP adapters: **Agent Runtime** (formerly Vertex AI Agent Engine) for the
deployed agent, **Agent Platform Sessions** for per-application state, and **Agent
Platform Memory Bank** for durable underwriter preferences/facts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import LlmMessage, LoanApplicationCase, MemoryItem, Session


@runtime_checkable
class AgentRuntimePort(Protocol):
    def query(self, session: Session, message: str) -> LoanApplicationCase:
        """Send a message to the hosted agent within a session and get a case reply."""
        ...

    def health(self) -> bool:
        """Liveness/readiness of the hosted agent runtime."""
        ...


@runtime_checkable
class SessionPort(Protocol):
    def create_session(self, user_id: str, application_id: str | None = None) -> Session: ...

    def get(self, session_id: str) -> Session | None: ...

    def append(self, session_id: str, message: LlmMessage) -> None: ...

    def history(self, session_id: str) -> list[LlmMessage]: ...


@runtime_checkable
class MemoryPort(Protocol):
    def store(self, item: MemoryItem) -> None: ...

    def search(self, query: str, scope: str = "user", top_k: int = 5) -> list[MemoryItem]: ...
