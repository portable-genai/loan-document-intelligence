"""Generation port : LLM normalisation / reconciliation / explanation.

Primary GCP adapter: Gemini models on the Gemini Enterprise Agent Platform
(``gemini-3.5-flash`` for reasoning, ``gemini-3.1-flash-lite`` for triage). The LLM
normalises extracted fields into ``IncomeFigure`` records, summarises and explains
discrepancies in prose. It NEVER overrides the deterministic cross-validation verdicts :
that authority belongs to the ``CrossValidator``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import LlmRequest, LlmResponse


@runtime_checkable
class LLMPort(Protocol):
    def generate(self, request: LlmRequest) -> LlmResponse:
        """Generate a completion for ``request`` using the configured model."""
        ...

    def classify(self, text: str, labels: list[str]) -> str:
        """Cheap single-label classification (triage/routing tier model)."""
        ...
