"""The ``live`` profile's model adapter, driven offline through a fake transport.

The laptop ``live`` lane reaches the fleet's shared local model through the kit client
(:mod:`hex_service_kit.localmodel`). These tests hand that client a fake transport, so they
exercise the real message assembly, schema retry and response mapping with no model server,
and they pin the other half of the lane: every port builds under ``live``.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from hex_service_kit.localmodel import (
    DEFAULT_LOCAL_MODEL,
    LocalModelClient,
    LocalModelOutputError,
    LocalModelSettings,
    LocalModelUnavailable,
)

from loan_doc_intel import config
from loan_doc_intel.adapters.live.llm import LocalModelLLMAdapter
from loan_doc_intel.config import LocalSettings, Settings, build_container, end_user_auth_kind
from loan_doc_intel.domain.models import LlmMessage, LlmRequest, TokenUsage
from loan_doc_intel.ports.identity import VERIFIED

CONFIG_PATH = "config/settings.yaml"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"narrative": {"type": "string"}},
    "required": ["narrative"],
}


class _FakeTransport:
    """Answers each POST with the next scripted reply and records every request body."""

    def __init__(self, *replies: str, model: str = "fake/answering-model", usage: Any = None):
        self._replies = list(replies)
        self._model = model
        self._usage = usage
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        self.bodies.append(json.loads(body))
        content = self._replies.pop(0)
        answer: dict[str, Any] = {
            "model": self._model,
            "choices": [{"message": {"role": "assistant", "content": content}}],
        }
        if self._usage is not None:
            answer["usage"] = self._usage
        return json.dumps(answer).encode()


def _adapter(transport: Any) -> LocalModelLLMAdapter:
    client = LocalModelClient(LocalModelSettings(), transport=transport)
    return LocalModelLLMAdapter(Settings(profile="live"), client=client)


def _request(**overrides: Any) -> LlmRequest:
    fields: dict[str, Any] = {
        "messages": (LlmMessage(role="user", content="Normalise the monthly income."),),
        "system_instruction": "You normalise declared income against the extracts.",
        "temperature": 0.0,
        "max_output_tokens": 512,
        "response_schema": _SCHEMA,
    }
    fields.update(overrides)
    return LlmRequest(**fields)


def test_a_fenced_invalid_first_answer_is_retried_and_the_valid_one_returned() -> None:
    transport = _FakeTransport(
        '```json\n{"summary": "wrong field"}\n```',
        '```json\n{"narrative": "Net pay SGD 6,200 per month."}\n```',
    )
    response = _adapter(transport).generate(_request())

    assert json.loads(response.text) == {"narrative": "Net pay SGD 6,200 per month."}
    assert response.model == "fake/answering-model"
    assert len(transport.bodies) == 2
    retry_turn = transport.bodies[1]["messages"][-1]["content"]
    assert "narrative" in retry_turn, "the schema problem must be fed back to the model"
    # The system instruction and the schema share the one system turn.
    system = transport.bodies[0]["messages"][0]
    assert system["role"] == "system"
    assert "normalise declared income" in system["content"]
    assert '"required": ["narrative"]' in system["content"]


def test_an_answer_that_never_validates_raises_the_kit_output_error() -> None:
    transport = _FakeTransport("not json", "still not json", "{}")
    with pytest.raises(LocalModelOutputError):
        _adapter(transport).generate(_request())
    assert len(transport.bodies) == 3


def test_the_request_temperature_and_token_budget_pass_through_unchanged() -> None:
    transport = _FakeTransport("A plain narrative.")
    response = _adapter(transport).generate(
        _request(response_schema=None, temperature=0.7, max_output_tokens=321)
    )

    assert response.text == "A plain narrative."
    body = transport.bodies[0]
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 321
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_model_turns_become_assistant_turns() -> None:
    transport = _FakeTransport("ok")
    _adapter(transport).generate(
        _request(
            response_schema=None,
            messages=(
                LlmMessage(role="user", content="first"),
                LlmMessage(role="model", content="reply"),
                LlmMessage(role="user", content="second"),
            ),
        )
    )
    roles = [m["role"] for m in transport.bodies[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_usage_is_carried_when_reported_and_zero_only_because_the_type_requires_one() -> None:
    reported = _FakeTransport("ok", usage={"input_tokens": 11, "output_tokens": 7})
    assert _adapter(reported).generate(_request(response_schema=None)).usage == TokenUsage(11, 7)

    silent = _FakeTransport("ok")
    assert _adapter(silent).generate(_request(response_schema=None)).usage == TokenUsage()


def test_classify_coerces_the_answer_onto_a_label() -> None:
    transport = _FakeTransport("The label is payslip.")
    assert _adapter(transport).classify("Payslip for March", ["bank_statement", "payslip"]) == (
        "payslip"
    )
    assert transport.bodies[0]["temperature"] == 0.0


def test_an_unreachable_server_raises_the_kit_unavailable_error() -> None:
    def refuse(url: str, body: bytes | None, timeout: float) -> bytes:
        raise OSError("connection refused")

    with pytest.raises(LocalModelUnavailable, match="Start a local model server"):
        _adapter(refuse).generate(_request())


def test_the_container_builds_every_port_under_live() -> None:
    base = Settings.load(CONFIG_PATH)
    settings = dataclasses.replace(
        base,
        profile="live",
        profile_explicit=True,
        local=LocalSettings(audit_path=":memory:"),
    )
    container = build_container(settings)

    for port_name in settings.adapters:
        assert getattr(container, port_name) is not None, port_name
    assert isinstance(container.llm, LocalModelLLMAdapter)
    assert "live" in config.RUNTIME_PROFILES


def test_live_takes_the_laptop_posture_seeded_personas_that_verify_nobody() -> None:
    """``live`` serves the seeded personas, so the exposure guard must treat it as unverified."""
    settings = dataclasses.replace(
        Settings.load(CONFIG_PATH),
        profile="live",
        profile_explicit=True,
        local=LocalSettings(audit_path=":memory:"),
    )
    assert build_container(settings).identity is not None
    assert end_user_auth_kind(settings) != VERIFIED


def test_the_model_pill_names_the_local_model_that_answers_under_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = dataclasses.replace(Settings.load(CONFIG_PATH), profile="live")
    assert live.runtime == "local"
    monkeypatch.delenv("LOCAL_MODEL", raising=False)
    assert live.generator_model == DEFAULT_LOCAL_MODEL
    monkeypatch.setenv("LOCAL_MODEL", "example-org/another-local-build")
    assert live.generator_model == "example-org/another-local-build"
