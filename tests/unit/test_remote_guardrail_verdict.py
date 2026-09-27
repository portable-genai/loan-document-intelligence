"""The remote guardrail client allows ONLY on a literal JSON ``true`` from the gateway.

The mapping this replaced was ``allowed=bool(body.get("allowed", False))``: the strings
``"false"`` and ``"no"``, the number ``1`` and any non-empty object all read as allowed, so a
gateway or proxy answering in the wrong shape cleared the text. The gateway is served here with
``respx`` on the adapter's localhost default, so nothing leaves the process.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from loan_doc_intel.adapters.platform.remote_guardrail import (
    RemoteGuardrailAdapter,
    RemoteGuardrailError,
)
from loan_doc_intel.config import Settings
from loan_doc_intel.domain.models import Direction

GATEWAY = "http://localhost:8080/v1/guardrail/screen"
TEXT = "What is the net monthly pay on this payslip?"


def _screen(body: Any, status: int = 200) -> Any:
    adapter = RemoteGuardrailAdapter(Settings(profile="platform"))
    with respx.mock:
        respx.post(GATEWAY).respond(status, json=body)
        return adapter.screen(TEXT, Direction.INPUT)


def test_a_literal_true_allows() -> None:
    verdict = _screen({"allowed": True, "direction": "input", "findings": [], "reason": "ok"})
    assert verdict.allowed is True


@pytest.mark.parametrize(
    "allowed",
    [False, "true", "false", "yes", 1, 1.0, {"ok": True}, [True], None],
    ids=["false", "str-true", "str-false", "str-yes", "one", "one-float", "object", "list", "null"],
)
def test_anything_but_a_literal_true_blocks(allowed: Any) -> None:
    verdict = _screen({"allowed": allowed, "direction": "input", "findings": []})
    assert verdict.allowed is False


def test_a_missing_allowed_field_blocks() -> None:
    assert _screen({"direction": "input"}).allowed is False


@pytest.mark.parametrize(
    "raw", ["[]", '"allowed"', "true", "null"], ids=["list", "str", "bool", "null"]
)
def test_a_body_that_is_not_an_object_is_an_error(raw: str) -> None:
    adapter = RemoteGuardrailAdapter(Settings(profile="platform"))
    with respx.mock:
        respx.post(GATEWAY).respond(200, content=raw, headers={"content-type": "application/json"})
        with pytest.raises(RemoteGuardrailError, match="non-object"):
            adapter.screen(TEXT, Direction.INPUT)


def test_a_body_that_is_not_json_is_an_error() -> None:
    adapter = RemoteGuardrailAdapter(Settings(profile="platform"))
    with respx.mock:
        respx.post(GATEWAY).respond(200, content="<html>allowed</html>")
        with pytest.raises(RemoteGuardrailError, match="non-JSON"):
            adapter.screen(TEXT, Direction.INPUT)


@pytest.mark.parametrize("status", [401, 500, 503])
def test_a_gateway_error_status_is_an_error_even_with_an_allowing_body(status: int) -> None:
    with pytest.raises(RemoteGuardrailError, match=str(status)):
        _screen({"allowed": True}, status=status)


def test_a_transport_failure_is_an_error() -> None:
    adapter = RemoteGuardrailAdapter(Settings(profile="platform"))
    with respx.mock:
        respx.post(GATEWAY).mock(side_effect=httpx.ConnectTimeout("deadline exceeded"))
        with pytest.raises(RemoteGuardrailError, match="deadline"):
            adapter.screen(TEXT, Direction.INPUT)
