"""Live LLM adapter (LLMPort): the fleet's shared local open-weight model.

The ``live`` profile's model port. Income normalisation is produced by a model running on
the operator's own machine, reached through the ONE client every laptop ``live`` lane uses,
:class:`hex_service_kit.localmodel.LocalModelClient`. Its endpoint, model and timeout are the
fleet-wide ``LOCAL_MODEL_URL``, ``LOCAL_MODEL`` and ``LOCAL_MODEL_TIMEOUT`` (three-state, read
by the kit), so this repository carries no model server settings of its own. The model never
overrides a deterministic cross-validation verdict.

Structured output: the kit states the request's JSON schema in the prompt, strips a fence,
validates the answer against the schema and feeds any problem back for a bounded number of
retries. The validated value is re-serialised as the response text, so the domain's parser
reads clean JSON.

Failures propagate as the kit raises them (:class:`LocalModelUnavailable`,
:class:`LocalModelOutputError`), exactly as the Gemini adapter lets its SDK errors propagate:
the service treats a failed normalisation as "no figures" rather than dropping the case, in
both profiles.
"""

from __future__ import annotations

import json
from typing import Any

from hex_service_kit.localmodel import LocalModelClient, LocalModelSettings

from ...config import Settings
from ...domain.models import LlmRequest, LlmResponse, TokenUsage


class LocalModelLLMAdapter:
    """Generate with the shared local model through the kit client."""

    def __init__(self, settings: Settings, *, client: LocalModelClient | None = None) -> None:
        self._settings = settings
        self._client = client or LocalModelClient(LocalModelSettings.from_env())

    # ------------------------------------------------------------------ #
    # LLMPort
    # ------------------------------------------------------------------ #
    def generate(self, request: LlmRequest) -> LlmResponse:
        """Generate a completion for ``request``.

        ``request.model`` names a managed model id and is not honoured here: the laptop
        runs one model server, and the response names the model that actually answered.
        """
        messages = self._build_messages(request)
        if request.response_schema is not None:
            completion = self._client.complete_json(
                messages,
                schema=request.response_schema,
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
            )
            text = json.dumps(completion.data)
        else:
            completion = self._client.complete(
                messages,
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
            )
            text = completion.text.strip()
        # LlmResponse.usage is a mandatory TokenUsage; the kit reports None when the server
        # counted nothing (MLX), which this type can only carry as its zero default.
        return LlmResponse(
            text=text,
            usage=completion.usage if completion.usage is not None else TokenUsage(),
            model=completion.model,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        """Single-label triage at temperature 0, coerced onto ``labels``."""
        if not labels:
            return ""
        prompt = (
            "Classify the text into exactly one of these labels: "
            f"{', '.join(labels)}.\n"
            "Reply with the single label only, no punctuation or explanation.\n\n"
            f"Text:\n{text}"
        )
        completion = self._client.complete(
            [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=16
        )
        return _match_label(completion.text, labels)

    # ------------------------------------------------------------------ #
    # Prompt assembly
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_messages(request: LlmRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        for message in request.messages:
            role = "assistant" if message.role == "model" else message.role
            messages.append({"role": role, "content": message.content})
        return messages


def _match_label(content: str, labels: list[str]) -> str:
    answer = content.strip().strip(".\"'").lower()
    for label in labels:
        if answer == label.lower():
            return label
    for label in labels:  # the model often answers in a short sentence
        if label.lower() in answer:
            return label
    return labels[0]
