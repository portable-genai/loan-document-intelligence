"""The Model Armor adapter blocks on a match, and fails closed on no verdict or an API error.

It also fails closed on an INCOMPLETE screen: ``invocationResult`` ``PARTIAL`` or ``FAILURE``
means some or all filters were skipped or failed, and a skipped filter reports
``NO_MATCH_FOUND``. Padding a prompt past the prompt-injection filter's token limit would
otherwise get it through unscreened, so only ``NO_MATCH_FOUND`` with ``SUCCESS`` allows.

The mapping this replaced was
``allowed = match_state != "MATCH_FOUND" if match_state is not None else not findings``: it
allowed ``FILTER_MATCH_STATE_UNSPECIFIED``, an empty or missing ``sanitizationResult``, and a
``NO_MATCH_FOUND`` whose ``invocationResult`` was ``PARTIAL`` or ``FAILURE``, because it never
read ``invocationResult`` at all.

The adapter speaks REST, so what it parses is the proto3 JSON mapping of the sanitize
response: camelCase fields, enums by member name, default-valued fields omitted. This module
tests at two levels:

* **SDK-free** (always runs, including the offline gate's SDK-free ``make check``): responses
  are hand-built JSON whose enum strings come from ``_MirrorState`` / ``_MirrorInvocation``,
  stdlib ``IntEnum`` copies of the real members by name and number.
* **Real SDK** (runs where ``google-cloud-modelarmor`` is installed, skips otherwise): responses
  are built from the real ``modelarmor_v1`` messages and serialised with the SDK's own JSON
  mapping, so the adapter reads exactly the wire shape the service returns. The first of these
  tests pins the mirror to the real enums, so the SDK-free half cannot drift.

Both halves go through ``screen()`` with a fake HTTP client, so nothing touches the network.
"""

from __future__ import annotations

import enum
import json
from typing import Any

import httpx
import pytest

from loan_doc_intel.adapters.gcp import model_armor_guardrail as ma_module
from loan_doc_intel.adapters.gcp.model_armor_guardrail import ModelArmorGuardrailAdapter
from loan_doc_intel.config import Settings
from loan_doc_intel.domain.models import Direction

TEXT = "What is the net monthly pay on this payslip?"
DIRECTIONS = [Direction.INPUT, Direction.OUTPUT]


class _MirrorState(enum.IntEnum):
    """``modelarmor_v1.FilterMatchState``'s members, by name and number."""

    FILTER_MATCH_STATE_UNSPECIFIED = 0
    NO_MATCH_FOUND = 1
    MATCH_FOUND = 2


class _MirrorInvocation(enum.IntEnum):
    """``modelarmor_v1.InvocationResult``'s members, by name and number."""

    INVOCATION_RESULT_UNSPECIFIED = 0
    SUCCESS = 1
    PARTIAL = 2
    FAILURE = 3


# --------------------------------------------------------------------------- #
# A fake HTTP client, so screen() runs end to end with no network
# --------------------------------------------------------------------------- #
class _Response:
    def __init__(self, body: Any, error: Exception | None) -> None:
        self._body = body
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def json(self) -> Any:
        return self._body


class _FakeClient:
    """Answers every POST with the canned body, or fails with the canned error."""

    def __init__(
        self,
        body: Any = None,
        *,
        status_error: Exception | None = None,
        transport_error: Exception | None = None,
    ) -> None:
        self._body = body
        self._status_error = status_error
        self._transport_error = transport_error
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json: Any, headers: Any, timeout: Any) -> _Response:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self._transport_error is not None:
            raise self._transport_error
        return _Response(self._body, self._status_error)


def _adapter(client: _FakeClient, monkeypatch: pytest.MonkeyPatch) -> ModelArmorGuardrailAdapter:
    adapter = ModelArmorGuardrailAdapter(Settings(project_id="p", profile="gcp"))
    adapter._client = client  # skip the real client; the mapping is what is under test
    monkeypatch.setattr(adapter, "_bearer_token", lambda: "test-token")
    return adapter


def _screen(body: Any, monkeypatch: pytest.MonkeyPatch, direction: Direction = Direction.INPUT):
    return _adapter(_FakeClient(body), monkeypatch).screen(TEXT, direction)


def _mirror_body(
    state: _MirrorState | None, invocation: _MirrorInvocation | None = _MirrorInvocation.SUCCESS
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if state is not None:
        result["filterMatchState"] = state.name
    if invocation is not None:
        result["invocationResult"] = invocation.name
    return {"sanitizationResult": result}


# --------------------------------------------------------------------------- #
# SDK-free: the mapping itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_match_found_blocks_sdk_free(direction: Direction, monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _screen(_mirror_body(_MirrorState.MATCH_FOUND), monkeypatch, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert verdict.findings


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_no_match_found_with_success_allows_sdk_free(
    direction: Direction, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = _screen(_mirror_body(_MirrorState.NO_MATCH_FOUND), monkeypatch, direction)
    assert verdict.allowed is True
    assert verdict.sanitized_text == TEXT
    assert verdict.findings == ()


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("invocation", list(_MirrorInvocation), ids=lambda m: m.name)
def test_match_found_blocks_however_many_filters_ran_sdk_free(
    direction: Direction, invocation: _MirrorInvocation, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = _screen(_mirror_body(_MirrorState.MATCH_FOUND, invocation), monkeypatch, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "invocation",
    [
        _MirrorInvocation.PARTIAL,
        _MirrorInvocation.FAILURE,
        _MirrorInvocation.INVOCATION_RESULT_UNSPECIFIED,
        None,
    ],
    ids=["PARTIAL", "FAILURE", "UNSPECIFIED", "absent"],
)
def test_no_match_from_an_incomplete_screen_blocks_sdk_free(
    direction: Direction, invocation: _MirrorInvocation | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped filter reports no match. That is not a pass: the text was not screened."""
    verdict = _screen(_mirror_body(_MirrorState.NO_MATCH_FOUND, invocation), monkeypatch, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert "no complete filter decision" in verdict.reason


def test_exactly_one_combination_allows_sdk_free(monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = [
        (state.name, invocation.name)
        for state in _MirrorState
        for invocation in _MirrorInvocation
        if _screen(_mirror_body(state, invocation), monkeypatch).allowed
    ]
    assert allowed == [("NO_MATCH_FOUND", "SUCCESS")]


@pytest.mark.parametrize(
    "body",
    [
        _mirror_body(_MirrorState.FILTER_MATCH_STATE_UNSPECIFIED),
        _mirror_body(None),
        _mirror_body(None, None),
        {"sanitizationResult": None},
        {},
        None,
        [],
        # Integers are how proto-plus serialises enums by default; the REST API sends names.
        # A number is not a name, so it is no verdict, not a pass.
        {"sanitizationResult": {"filterMatchState": 1, "invocationResult": 1}},
    ],
    ids=[
        "unspecified-state",
        "state-absent",
        "empty-result",
        "none-result",
        "empty-body",
        "null-body",
        "list-body",
        "integer-enums",
    ],
)
def test_no_verdict_fails_closed_sdk_free(body: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _screen(body, monkeypatch)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert verdict.findings


def test_a_filter_match_blocks_even_when_the_top_level_state_says_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contradictory answer is resolved toward blocking."""
    body = _mirror_body(_MirrorState.NO_MATCH_FOUND)
    body["sanitizationResult"]["filterResults"] = {
        "pi_and_jailbreak": {
            "piAndJailbreakFilterResult": {"matchState": "MATCH_FOUND", "confidenceLevel": "HIGH"}
        }
    }
    verdict = _screen(body, monkeypatch)
    assert verdict.allowed is False
    assert {f.category.value for f in verdict.findings} == {"prompt_injection"}


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_every_call_carries_the_deadline(
    direction: Direction, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient(_mirror_body(_MirrorState.NO_MATCH_FOUND))
    _adapter(client, monkeypatch).screen(TEXT, direction)
    assert [c["timeout"] for c in client.calls] == [ma_module._TIMEOUT_SECONDS]
    assert 0 < ma_module._TIMEOUT_SECONDS <= 60


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://modelarmor.example/v1/x:sanitizeUserPrompt")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"{code} from Model Armor", request=request, response=response)


@pytest.mark.parametrize("code", [403, 429, 500, 503])
def test_an_api_error_status_propagates(code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx/5xx from Model Armor must not turn into an allow; it reaches the caller.

    The canned body is a clean ``NO_MATCH_FOUND`` + ``SUCCESS``, so a mapping that read the
    body past a failed status would allow; the error must win.
    """
    client = _FakeClient(
        _mirror_body(_MirrorState.NO_MATCH_FOUND), status_error=_status_error(code)
    )
    with pytest.raises(httpx.HTTPStatusError, match=str(code)):
        _adapter(client, monkeypatch).screen(TEXT, Direction.INPUT)


def test_a_timeout_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(transport_error=httpx.ReadTimeout("deadline exceeded"))
    with pytest.raises(httpx.ReadTimeout, match="deadline"):
        _adapter(client, monkeypatch).screen(TEXT, Direction.OUTPUT)


# --------------------------------------------------------------------------- #
# Real SDK: real modelarmor_v1 messages, serialised the way the REST API sends them
# --------------------------------------------------------------------------- #
def _ma() -> Any:
    return pytest.importorskip("google.cloud.modelarmor_v1")


def _wire(message: Any, *, print_defaults: bool) -> Any:
    """The REST body for ``message``: proto3 JSON, enums by NAME (the service's wire form)."""
    text = type(message).to_json(
        message,
        use_integers_for_enums=False,
        always_print_fields_with_no_presence=print_defaults,
    )
    return json.loads(text)


def _real_response(
    direction: Direction,
    state_name: str | None,
    invocation_name: str = "SUCCESS",
    *,
    skipped: bool = False,
) -> Any:
    """A real sanitize response; ``state_name=None`` leaves ``sanitization_result`` unset.

    ``skipped`` adds the prompt-injection filter as not having run, the shape a prompt padded
    past that filter's token limit produces.
    """
    ma = _ma()
    cls = (
        ma.SanitizeUserPromptResponse
        if direction is Direction.INPUT
        else ma.SanitizeModelResponseResponse
    )
    if state_name is None:
        return cls()
    filter_results = {}
    if skipped:
        filter_results["pi_and_jailbreak"] = ma.FilterResult(
            pi_and_jailbreak_filter_result=ma.PiAndJailbreakFilterResult(
                execution_state=ma.FilterExecutionState.EXECUTION_SKIPPED,
                match_state=ma.FilterMatchState.NO_MATCH_FOUND,
            )
        )
    return cls(
        sanitization_result=ma.SanitizationResult(
            filter_match_state=ma.FilterMatchState[state_name],
            invocation_result=ma.InvocationResult[invocation_name],
            filter_results=filter_results,
        )
    )


PRINT_DEFAULTS = pytest.mark.parametrize(
    "print_defaults", [False, True], ids=["defaults-omitted", "defaults-printed"]
)


@pytest.mark.parametrize(
    ("mirror", "real_name"),
    [(_MirrorState, "FilterMatchState"), (_MirrorInvocation, "InvocationResult")],
    ids=["FilterMatchState", "InvocationResult"],
)
def test_the_mirror_matches_the_real_enum(mirror: Any, real_name: str) -> None:
    real = getattr(_ma(), real_name)
    assert {m.name: int(m) for m in real} == {m.name: int(m) for m in mirror}


def test_the_wire_form_carries_the_fields_the_adapter_reads() -> None:
    """Pins the JSON field names and enum spelling the adapter keys on to the real SDK."""
    body = _wire(_real_response(Direction.INPUT, "NO_MATCH_FOUND"), print_defaults=False)
    assert body["sanitizationResult"]["filterMatchState"] == "NO_MATCH_FOUND"
    assert body["sanitizationResult"]["invocationResult"] == "SUCCESS"


@PRINT_DEFAULTS
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_match_found_blocks(
    direction: Direction, print_defaults: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _wire(_real_response(direction, "MATCH_FOUND"), print_defaults=print_defaults)
    verdict = _screen(body, monkeypatch, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None


@PRINT_DEFAULTS
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_no_match_found_with_success_allows(
    direction: Direction, print_defaults: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _wire(_real_response(direction, "NO_MATCH_FOUND"), print_defaults=print_defaults)
    verdict = _screen(body, monkeypatch, direction)
    assert verdict.allowed is True
    assert verdict.sanitized_text == TEXT


@PRINT_DEFAULTS
@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "state_name",
    [None, "FILTER_MATCH_STATE_UNSPECIFIED"],
    ids=["missing-result", "unspecified-state"],
)
def test_no_verdict_fails_closed(
    direction: Direction,
    state_name: str | None,
    print_defaults: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _wire(_real_response(direction, state_name), print_defaults=print_defaults)
    verdict = _screen(body, monkeypatch, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None


@PRINT_DEFAULTS
@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("invocation_name", ["PARTIAL", "FAILURE", "INVOCATION_RESULT_UNSPECIFIED"])
def test_no_match_from_a_screen_where_filters_did_not_run_blocks(
    direction: Direction,
    invocation_name: str,
    print_defaults: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _real_response(direction, "NO_MATCH_FOUND", invocation_name, skipped=True)
    verdict = _screen(_wire(message, print_defaults=print_defaults), monkeypatch, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert "no complete filter decision" in verdict.reason


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_an_api_error_beats_a_clean_real_body(
    direction: Direction, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean real response behind a 503 still raises: the status is checked first."""
    body = _wire(_real_response(direction, "NO_MATCH_FOUND"), print_defaults=False)
    client = _FakeClient(body, status_error=_status_error(503))
    with pytest.raises(httpx.HTTPStatusError):
        _adapter(client, monkeypatch).screen(TEXT, direction)
