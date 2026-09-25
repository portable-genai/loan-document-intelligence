"""The review hand-off reaches a deployed console through the portal's IAP edge.

A deployed human-review-console is an embedded app behind the portal's IAP edge, and the edge
accepts one bearer: a Google-signed ID token minted for the deployment's IAP OAuth client id. No
environment variable can hold that token, so when ``HUMAN_REVIEW_IAP_AUDIENCE`` is named the
router hands ``review-kit`` a provider that mints one per submission; when it is not, the static
S2S token path is unchanged. Under ``gcp`` routing on now needs BOTH the console URL and the
audience, and a backend-service path pasted as the audience refuses at boot by name.

Minting is always faked here: nothing in this module reaches a metadata server.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from loan_doc_intel.adapters.platform.review_router import PlatformReviewRouter
from loan_doc_intel.config import (
    HUMAN_REVIEW_IAP_AUDIENCE_ENV,
    HUMAN_REVIEW_URL_ENV,
    REVIEW_ROUTING_ENV,
    Settings,
)
from loan_doc_intel.domain.models import Applicant, LoanApplicationCase
from loan_doc_intel.envread import ConfiguredEmptyError

_PROFILE_ENV = "LOAN_DOC_PROFILE"
_CONFIG = "config/settings.yaml"
_EDGE = "https://edge.example.test/apps/human-review-console/api"
_AUDIENCE = "123456789-abcdef.apps.googleusercontent.com"
_BACKEND_PATH = "/projects/123456789/global/backendServices/987654321"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        HUMAN_REVIEW_URL_ENV,
        HUMAN_REVIEW_IAP_AUDIENCE_ENV,
        REVIEW_ROUTING_ENV,
        "S2S_TOKEN",
        "S2S_SIGNING_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(_PROFILE_ENV, "local")


class _Recorder:
    """A fake review-kit transport that records every request and accepts it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> dict[str, Any]:
        self.calls.append((url, dict(headers)))
        return {"review_id": f"r-{len(self.calls)}", "tenant": "demo-bank", "state": "pending"}


class _Minter:
    """A fake minting function: records each audience and returns a distinct token per call."""

    def __init__(self) -> None:
        self.audiences: list[str] = []

    def __call__(self, audience: str) -> str:
        self.audiences.append(audience)
        return f"minted-{len(self.audiences)}-for-{audience}"


def _case() -> LoanApplicationCase:
    applicant = Applicant(id="app-fictional-001", name="Jane Example (FICTIONAL)")
    return LoanApplicationCase(id=applicant.id, applicant=applicant)


def _route(router: PlatformReviewRouter) -> None:
    router.route(_case(), maker="officer@bank.test", tenant="demo-bank")


# --------------------------------------------------------------------------- #
# The router: minted bearer when the audience is named, static token otherwise
# --------------------------------------------------------------------------- #
def test_the_router_sends_a_bearer_minted_for_the_named_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, _EDGE)
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, _AUDIENCE)
    minter, transport = _Minter(), _Recorder()
    router = PlatformReviewRouter(Settings(profile="gcp"), mint=minter, transport=transport)
    assert minter.audiences == [], "constructing the router must not spend a token"

    _route(router)

    assert minter.audiences == [_AUDIENCE]
    url, headers = transport.calls[0]
    assert url == f"{_EDGE}/v1/service/reviews"
    assert headers["Authorization"] == f"Bearer minted-1-for-{_AUDIENCE}"


def test_every_submission_mints_its_own_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, _EDGE)
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, _AUDIENCE)
    minter, transport = _Minter(), _Recorder()
    router = PlatformReviewRouter(Settings(profile="gcp"), mint=minter, transport=transport)

    _route(router)
    _route(router)

    assert minter.audiences == [_AUDIENCE, _AUDIENCE]
    bearers = [headers["Authorization"] for _, headers in transport.calls]
    assert bearers == [f"Bearer minted-1-for-{_AUDIENCE}", f"Bearer minted-2-for-{_AUDIENCE}"]


def test_a_minted_bearer_wins_over_a_static_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The edge accepts only the minted token, so a static one must not be what is sent."""
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, _EDGE)
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, _AUDIENCE)
    monkeypatch.setenv("S2S_TOKEN", "static-s2s-token")
    minter, transport = _Minter(), _Recorder()
    _route(PlatformReviewRouter(Settings(profile="gcp"), mint=minter, transport=transport))
    assert transport.calls[0][1]["Authorization"] == f"Bearer minted-1-for-{_AUDIENCE}"


def test_without_an_audience_the_static_token_path_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, "https://review.example.test")
    monkeypatch.setenv("S2S_TOKEN", "static-s2s-token")
    minter, transport = _Minter(), _Recorder()
    _route(PlatformReviewRouter(Settings(profile="platform"), mint=minter, transport=transport))
    assert minter.audiences == [], "the provider is used only when the audience is named"
    assert transport.calls[0][1]["Authorization"] == "Bearer static-s2s-token"


def test_without_an_audience_or_a_token_a_remote_console_still_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, "https://review.example.test")
    minter, transport = _Minter(), _Recorder()
    router = PlatformReviewRouter(Settings(profile="platform"), mint=minter, transport=transport)
    with pytest.raises(ValueError, match="S2S_TOKEN"):
        _route(router)
    assert minter.audiences == [] and transport.calls == []


def test_the_router_refuses_an_emptied_audience_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, "")
    with pytest.raises(ConfiguredEmptyError, match=HUMAN_REVIEW_IAP_AUDIENCE_ENV):
        PlatformReviewRouter(Settings(profile="gcp"), mint=_Minter())


def test_the_router_refuses_a_backend_service_path_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, _BACKEND_PATH)
    with pytest.raises(ValueError, match="backend-service path"):
        PlatformReviewRouter(Settings(profile="gcp"), mint=_Minter())


# --------------------------------------------------------------------------- #
# Boot: under gcp, routing on needs the console URL AND the audience
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("present", "absent"),
    [
        ({HUMAN_REVIEW_IAP_AUDIENCE_ENV: _AUDIENCE}, HUMAN_REVIEW_URL_ENV),
        ({HUMAN_REVIEW_URL_ENV: _EDGE}, HUMAN_REVIEW_IAP_AUDIENCE_ENV),
        ({}, HUMAN_REVIEW_URL_ENV),
    ],
)
def test_gcp_routing_on_refuses_each_missing_variable_naming_both_and_the_way_out(
    monkeypatch: pytest.MonkeyPatch, present: dict[str, str], absent: str
) -> None:
    monkeypatch.setenv(_PROFILE_ENV, "gcp")
    for name, value in present.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfiguredEmptyError) as refused:
        Settings.load(_CONFIG)
    message = str(refused.value)
    assert HUMAN_REVIEW_URL_ENV in message
    assert HUMAN_REVIEW_IAP_AUDIENCE_ENV in message
    assert f"{REVIEW_ROUTING_ENV}=off" in message
    assert f"not set: {absent}" in message


def test_gcp_routing_on_with_both_variables_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_PROFILE_ENV, "gcp")
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, _EDGE)
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, _AUDIENCE)
    assert Settings.load(_CONFIG).controls.review_routing is True


def test_gcp_routing_stated_off_needs_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_PROFILE_ENV, "gcp")
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "off")
    assert Settings.load(_CONFIG).controls.review_routing is False


def test_platform_needs_the_console_but_not_the_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_PROFILE_ENV, "platform")
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, "https://review.example.test")
    assert Settings.load(_CONFIG).controls.review_routing is True


@pytest.mark.parametrize("profile", ["gcp", "platform"])
def test_a_backend_service_path_as_the_audience_refuses_at_boot(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setenv(_PROFILE_ENV, profile)
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, _EDGE)
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, _BACKEND_PATH)
    with pytest.raises(ValueError, match=f"{HUMAN_REVIEW_IAP_AUDIENCE_ENV} must be the IAP"):
        Settings.load(_CONFIG)


@pytest.mark.parametrize("profile", ["gcp", "platform"])
def test_an_emptied_audience_refuses_at_boot(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    monkeypatch.setenv(_PROFILE_ENV, profile)
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, _EDGE)
    monkeypatch.setenv(HUMAN_REVIEW_IAP_AUDIENCE_ENV, "")
    with pytest.raises(ConfiguredEmptyError, match=HUMAN_REVIEW_IAP_AUDIENCE_ENV):
        Settings.load(_CONFIG)
