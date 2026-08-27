"""Local LLM adapter (LLMPort) : a deterministic, schema-driven income normaliser.

The ``local`` profile's stand-in for **Gemini**: no model, no network, fully
reproducible. It reads ``request.response_schema`` (the JSON schema the calling service
asks for) and emits a deterministic JSON object whose keys match it:

* a ``figures`` array (income normalisation) : one salary figure per income-bearing
  document id parsed out of the rendered EXTRACTS block, so the service maps figures only
  to documents it actually extracted, or
* an ``explanations`` array (discrepancy explanation) keyed off the same document ids.

There is no Google emulator for Gemini, so this path is unconditional. The schema-driven
``FakeLLM`` is a real, registered adapter rather than a test fixture, so the in-memory
implementation lives once under ``adapters/local`` and drives both the offline tests and
the CLI.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...config import Settings
from ...domain.models import LlmRequest, LlmResponse, TokenUsage

# Each extract block in the prompt is keyed by ``[document_id]``; recover those ids so the
# normalised figures attribute only to documents that were actually extracted.
_DOC_ID_RE = re.compile(r"\[([a-zA-Z0-9][a-zA-Z0-9\-]*?)\]")
# Document ids that carry an income figure worth normalising.
_INCOME_DOC_HINTS = ("payslip", "bank", "tax", "employment")


def _schema_properties(schema: dict | None) -> dict[str, Any]:
    if not schema:
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


class LocalDeterministicLLMAdapter:
    """Deterministic LLM whose ``generate`` returns JSON matching the request schema."""

    REASONING_MODEL = "gemini-3.5-flash"
    TRIAGE_MODEL = "gemini-3.5-flash"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._reasoning_model = settings.models.reasoning or self.REASONING_MODEL
        self._triage_model = settings.models.triage or self.TRIAGE_MODEL

    # ------------------------------------------------------------------ #
    # LLMPort
    # ------------------------------------------------------------------ #
    def generate(self, request: LlmRequest) -> LlmResponse:
        body = self._body_for_schema(request)
        return LlmResponse(
            text=json.dumps(body),
            usage=TokenUsage(input_tokens=128, output_tokens=64, thinking_tokens=32),
            model=request.model or self._reasoning_model,
            raw=body,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        # Deterministic triage: first label (the services only use this for routing).
        return labels[0] if labels else ""

    # ------------------------------------------------------------------ #
    # Schema-driven body
    # ------------------------------------------------------------------ #
    def _doc_ids(self, request: LlmRequest) -> list[str]:
        text = request.messages[-1].content if request.messages else ""
        seen: list[str] = []
        for did in _DOC_ID_RE.findall(text):
            if did not in seen:
                seen.append(did)
        return seen

    def _body_for_schema(self, request: LlmRequest) -> dict[str, Any]:
        props = _schema_properties(request.response_schema)
        doc_ids = self._doc_ids(request)
        if "figures" in props:
            income_ids = [d for d in doc_ids if any(h in d for h in _INCOME_DOC_HINTS)]
            chosen = income_ids or doc_ids[:1]
            figures = [
                {
                    "source_doc_id": did,
                    "amount": 6500.0,
                    "currency": "SGD",
                    "period": "monthly",
                    "kind": "salary",
                }
                for did in chosen
            ]
            return {"figures": figures}
        if "explanations" in props:
            return {
                "explanations": [
                    {
                        "kind": "income_consistency",
                        "explanation": "The figures align within tolerance.",
                        "used_document_ids": doc_ids,
                    }
                ]
            }
        return {}
