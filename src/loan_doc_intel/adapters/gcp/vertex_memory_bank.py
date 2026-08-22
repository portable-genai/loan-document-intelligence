"""Agent Platform Memory Bank adapter (MemoryPort) : durable underwriter facts.

Backs the domain ``MemoryPort`` with the **Agent Platform Memory Bank** on the Gemini
Enterprise Agent Platform : durable storage of underwriter preferences and recurring
facts (never applicant PII, which is redacted at the boundary).

The Vertex AI SDK import is lazy so the on-prem and test profiles import without it.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import MemoryItem


class VertexMemoryBankAdapter:
    """Store and search durable memory items via Agent Platform Memory Bank."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service: Any | None = None

    def _get_service(self) -> Any:
        if self._service is None:
            from vertexai import agent_engines  # lazy

            # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/memory
            self._service = agent_engines.get(self._settings.agent_engine.resource_name)
        return self._service

    def store(self, item: MemoryItem) -> None:
        service = self._get_service()
        service.create_memory(
            fact=item.content,
            scope={"scope": item.scope, "id": item.id},
        )

    def search(self, query: str, scope: str = "user", top_k: int = 5) -> list[MemoryItem]:
        service = self._get_service()
        results = service.retrieve_memories(query=query, scope={"scope": scope}) or []
        items: list[MemoryItem] = []
        for r in results[:top_k]:
            items.append(
                MemoryItem(
                    id=str(getattr(r, "id", "")),
                    content=str(getattr(r, "fact", "") or getattr(r, "content", "")),
                    scope=scope,
                )
            )
        return items
