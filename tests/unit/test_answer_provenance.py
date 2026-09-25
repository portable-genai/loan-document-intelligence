"""The service half of the model pills: which model ANSWERED, and whether it searched.

The console shows two pills at the top right: the model that answered the last request, and
``Search`` when that answer used an online search tool. Both come from response headers the kit
emits (``install_answer_provenance`` in ``api/app.py``) for whatever the model adapters NOTED as
they called. Before a request is answered the pill shows ``generator_model`` from ``/healthz``,
so that value must be the model the bound adapter calls, never one a configuration flag names
while the adapter calls another.

The Gemini adapter is driven here through a FAKE ``google.genai`` module, so what is proved is
this repository's half: the model id each call notes, and the sampling each call sends (a free
request sends no temperature at all; a pinned one sends its 0.0).
"""

from __future__ import annotations

import dataclasses
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit import provenance
from hex_service_kit.localmodel import LocalModelClient, LocalModelSettings

from loan_doc_intel.adapters.gcp.gemini_llm import GeminiLLMAdapter
from loan_doc_intel.adapters.live.llm import LocalModelLLMAdapter
from loan_doc_intel.adapters.local.llm import LocalDeterministicLLMAdapter
from loan_doc_intel.api import deps
from loan_doc_intel.api.app import app
from loan_doc_intel.config import (
    STUB_GENERATOR_MODEL,
    Container,
    LocalSettings,
    ModelSettings,
    Settings,
)
from loan_doc_intel.domain.models import LlmMessage, LlmRequest, LlmResponse

CONFIG_PATH = "config/settings.yaml"
ANSWERED_BY = "x-answered-by"
SEARCH_USED = "x-search-used"

_BODY = {
    "application": {
        "id": "app-fictional-0001",
        "name": "Jordan Tester Fictional",
        "address": "123 Imaginary Road, Singapore 000000",
        "declared_income": {
            "source_doc_id": "declared",
            "amount": 6500.0,
            "currency": "SGD",
            "period": "monthly",
            "kind": "salary",
        },
    },
    "documents": [
        {"id": "doc-payslip-2026-04", "doc_type": "payslip", "uri": "local://app-0001/payslip.txt"},
        {
            "id": "doc-bank-2026-04",
            "doc_type": "bank_statement",
            "uri": "local://app-0001/bank.txt",
        },
    ],
}


def _local_settings() -> Settings:
    return dataclasses.replace(
        Settings.load(CONFIG_PATH),
        profile="local",
        local=LocalSettings(audit_path=":memory:"),
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real app over an in-memory ``local`` container, profile named rather than inherited."""
    monkeypatch.setenv("LOAN_DOC_PROFILE", "local")
    container = Container(_local_settings())
    monkeypatch.setattr(deps, "get_container", lambda: container)
    monkeypatch.setattr(
        deps, "get_loan_doc_service", lambda: deps.build_loan_doc_service(container)
    )
    return TestClient(app, client=("127.0.0.1", 50000))


def _process(client: TestClient) -> dict[str, str]:
    response = client.post("/v1/process", json=_BODY, headers={"X-Dev-Persona": "analyst"})
    assert response.status_code == 200, response.text
    return dict(response.headers)


def test_the_local_stub_answers_as_the_name_the_pill_first_shows(client: TestClient) -> None:
    """Under ``local`` the pill before and after the answer name the same stub, never Gemini."""
    headers = _process(client)
    assert headers[ANSWERED_BY] == STUB_GENERATOR_MODEL
    assert _local_settings().generator_model == STUB_GENERATOR_MODEL
    assert SEARCH_USED not in headers


def test_a_route_that_calls_no_model_names_none(client: TestClient) -> None:
    """``/healthz`` answers from settings; a header there would claim an answer nobody gave."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert ANSWERED_BY not in response.headers
    assert SEARCH_USED not in response.headers


def test_a_call_that_searched_says_so_and_the_next_request_starts_fresh(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No adapter here attaches a search tool, so the tool is faked to prove the wiring."""
    original = LocalDeterministicLLMAdapter.generate

    def searching(self: LocalDeterministicLLMAdapter, request: LlmRequest) -> LlmResponse:
        provenance.note_model("fake-searching-model")
        provenance.note_search()
        return original(self, request)

    monkeypatch.setattr(LocalDeterministicLLMAdapter, "generate", searching)
    headers = _process(client)
    assert headers[ANSWERED_BY] == f"fake-searching-model, {STUB_GENERATOR_MODEL}"
    assert headers[SEARCH_USED] == "true"
    monkeypatch.setattr(LocalDeterministicLLMAdapter, "generate", original)
    headers = _process(client)
    assert headers[ANSWERED_BY] == STUB_GENERATOR_MODEL
    assert SEARCH_USED not in headers


# --------------------------------------------------------------------------------------- #
# The Gemini adapter, through a fake SDK.
# --------------------------------------------------------------------------------------- #
class _FakeModels:
    def __init__(self, text: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text

    def generate_content(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(text=self._text, usage_metadata=None)


def _fake_genai(monkeypatch: pytest.MonkeyPatch, text: str) -> _FakeModels:
    models = _FakeModels(text)
    genai = types.ModuleType("google.genai")
    sdk = types.ModuleType("google.genai.types")
    sdk.GenerateContentConfig = lambda **kw: SimpleNamespace(**kw)  # type: ignore[attr-defined]
    sdk.ThinkingConfig = lambda **kw: SimpleNamespace(**kw)  # type: ignore[attr-defined]
    sdk.ThinkingLevel = SimpleNamespace(LOW="LOW", HIGH="HIGH")  # type: ignore[attr-defined]
    sdk.Content = lambda **kw: SimpleNamespace(**kw)  # type: ignore[attr-defined]
    sdk.Part = SimpleNamespace(from_text=lambda text: SimpleNamespace(text=text))  # type: ignore[attr-defined]
    genai.types = sdk  # type: ignore[attr-defined]
    genai.Client = lambda **_: SimpleNamespace(models=models)  # type: ignore[attr-defined]
    google = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setattr(google, "genai", genai, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", sdk)
    return models


def _gcp_settings() -> Settings:
    return dataclasses.replace(Settings.load(CONFIG_PATH), profile="gcp")


def _request(**overrides: Any) -> LlmRequest:
    fields: dict[str, Any] = {
        "messages": (LlmMessage(role="user", content="Explain the checks."),),
        "system_instruction": "You explain check outcomes.",
    }
    fields.update(overrides)
    return LlmRequest(**fields)


def test_the_gemini_adapter_notes_the_model_it_called_and_a_free_call_sends_no_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = _fake_genai(monkeypatch, "{}")
    settings = _gcp_settings()
    with provenance.scope() as record:
        GeminiLLMAdapter(settings).generate(_request())
    assert record.models == [settings.models.reasoning]
    assert record.search_used is False
    (call,) = models.calls
    assert call["model"] == settings.models.reasoning
    assert not hasattr(call["config"], "temperature"), "free sampling must send no temperature"


def test_a_pinned_request_reaches_the_gemini_config(monkeypatch: pytest.MonkeyPatch) -> None:
    models = _fake_genai(monkeypatch, "{}")
    GeminiLLMAdapter(_gcp_settings()).generate(_request(temperature=0.0))
    assert models.calls[0]["config"].temperature == 0.0


def test_gemini_triage_notes_the_triage_model_pinned_and_claims_no_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = _fake_genai(monkeypatch, "payslip")
    settings = _gcp_settings()
    with provenance.scope() as record:
        GeminiLLMAdapter(settings).classify("text", ["payslip", "bank_statement"])
    assert record.models == [settings.models.triage]
    assert record.search_used is False
    assert models.calls[0]["config"].temperature == 0.0


def test_the_managed_pill_names_the_model_the_adapter_calls_and_no_flag_moves_it() -> None:
    """``generator_model`` is the adapter's default model; the hard-reasoning switch is gone.

    The switch named a second model the resolver would report while the adapter kept calling
    ``models.reasoning``: a pill that could state a model that never answered.
    """
    settings = _gcp_settings()
    assert settings.generator_model == settings.models.reasoning
    field_names = {f.name for f in dataclasses.fields(ModelSettings)}
    assert not {"use_hard_reasoning", "hard_reasoning"} & field_names
    assert "hard_reasoning" not in Path(CONFIG_PATH).read_text()


def test_the_live_adapter_sends_no_temperature_when_the_call_is_free() -> None:
    """The kit client notes its own model; this proves ``None`` passes through as absence."""
    bodies: list[dict[str, Any]] = []

    def transport(url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        bodies.append(json.loads(body))
        answer = {"model": "fake/local", "choices": [{"message": {"content": "prose"}}]}
        return json.dumps(answer).encode()

    client = LocalModelClient(LocalModelSettings(), transport=transport)
    adapter = LocalModelLLMAdapter(Settings(profile="live"), client=client)
    with provenance.scope() as record:
        adapter.generate(_request())
    assert "temperature" not in bodies[0]
    assert record.models == ["fake/local"]
