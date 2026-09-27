"""Model Armor guardrail adapter (A1 Guardrail Gateway, primary GCP backend).

Implements :class:`GuardrailPort` against **Model Armor**, the runtime AI-safety service
of the Gemini Enterprise Agent Platform. Inbound prompts are screened with
``:sanitizeUserPrompt`` and outbound responses with ``:sanitizeModelResponse`` on the
regional endpoint ``modelarmor.asia-southeast1.rep.googleapis.com`` so all screening stays
inside Singapore for residency.

FAIL CLOSED. The verdict is ALLOWED only when ``sanitizationResult.filterMatchState`` is
``NO_MATCH_FOUND`` AND ``sanitizationResult.invocationResult`` is ``SUCCESS`` (the REST JSON
carries both enums by member name) and no individual filter reports a match. Everything else
blocks: ``MATCH_FOUND``; ``FILTER_MATCH_STATE_UNSPECIFIED``; a missing or empty
``sanitizationResult`` (the API omits default-valued fields, so an unscreened answer arrives as
``{}``); and ``NO_MATCH_FOUND`` with ``invocationResult`` ``PARTIAL`` or ``FAILURE``.
``invocationResult`` is set independently of the match state: a filter skipped past its token
limit, on an unsupported language, or on a detector error reports ``EXECUTION_SKIPPED`` and no
match, so "no match" from a screen that did not run is refused, not passed. Every call carries
a deadline (``_TIMEOUT_SECONDS``), and an HTTP or auth error propagates to the caller, which
audits the refusal.

The per-filter results (prompt-injection / jailbreak, Sensitive Data Protection, malicious-URI
and Responsible-AI) are parsed into :class:`GuardrailFinding` records that explain a block.

All Google Cloud / auth SDK imports are lazy so the on-prem and test profiles import this
module with no GCP SDK installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import (
    Direction,
    GuardrailCategory,
    GuardrailFinding,
    GuardrailVerdict,
)

_MATCH_FOUND = "MATCH_FOUND"
_NO_MATCH_FOUND = "NO_MATCH_FOUND"
_SUCCESS = "SUCCESS"
_TIMEOUT_SECONDS = 30.0

_RAI_CATEGORY: dict[str, GuardrailCategory] = {
    "hate_speech": GuardrailCategory.HATE,
    "harassment": GuardrailCategory.HARASSMENT,
    "sexually_explicit": GuardrailCategory.SEXUAL,
    "dangerous": GuardrailCategory.DANGEROUS,
}


class ModelArmorGuardrailAdapter:
    """Screen prompts and responses through Model Armor's REST API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._armor = settings.model_armor
        self._project = settings.project_id
        self._region = settings.region
        self._client: Any | None = None
        self._credentials: Any | None = None
        self._auth_request: Any | None = None

    # -- public API -------------------------------------------------------- #
    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        """Screen ``text`` and return a verdict; blocks on any filter match."""
        verb = "sanitizeUserPrompt" if direction is Direction.INPUT else "sanitizeModelResponse"
        payload = self._build_payload(text, direction)
        url = (
            f"https://{self._armor.host}/v1/projects/{self._project}"
            f"/locations/{self._region}/templates/{self._armor.template_id}:{verb}"
        )
        response = self._post(url, payload)
        return self._parse(response, direction, text)

    # -- request construction ---------------------------------------------- #
    def _build_payload(self, text: str, direction: Direction) -> dict[str, Any]:
        # verify: https://docs.cloud.google.com/model-armor/sanitize-prompts-responses
        if direction is Direction.INPUT:
            return {"userPromptData": {"text": text}}
        return {"modelResponseData": {"text": text}}

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        client = self._http_client()
        token = self._bearer_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = client.post(url, json=payload, headers=headers, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()

    def _http_client(self) -> Any:
        import httpx  # lazy

        if self._client is None:
            self._client = httpx.Client()
        return self._client

    def _bearer_token(self) -> str:
        import google.auth  # lazy
        from google.auth.transport.requests import Request  # lazy

        if self._credentials is None:
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._auth_request = Request()
        if not self._credentials.valid:
            self._credentials.refresh(self._auth_request)
        token: str = self._credentials.token
        return token

    # -- response parsing -------------------------------------------------- #
    def _parse(self, response: Any, direction: Direction, original_text: str) -> GuardrailVerdict:
        """Map a sanitize response to a verdict: allowed ONLY on a complete, clean screen."""
        result: Any = response.get("sanitizationResult") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            result = {}
        filter_results: Any = result.get("filterResults")
        if not isinstance(filter_results, dict):
            filter_results = {}

        findings: list[GuardrailFinding] = []
        findings.extend(self._parse_pi_jailbreak(filter_results))
        findings.extend(self._parse_sensitive_data(filter_results))
        findings.extend(self._parse_malicious_uris(filter_results))
        findings.extend(self._parse_rai(filter_results))

        match_state = result.get("filterMatchState")
        invocation = result.get("invocationResult")
        if match_state == _NO_MATCH_FOUND and invocation == _SUCCESS and not findings:
            return GuardrailVerdict(
                allowed=True,
                direction=direction,
                findings=(),
                sanitized_text=self._extract_sanitized_text(filter_results, original_text),
                reason="No blocking Model Armor filter matched.",
            )
        if match_state == _MATCH_FOUND or findings:
            if not findings:
                findings.append(
                    GuardrailFinding(
                        category=GuardrailCategory.OTHER,
                        confidence="high",
                        detail="Model Armor filter match.",
                    )
                )
            categories = ", ".join(sorted({f.category.value for f in findings}))
            reason = f"Blocked by Model Armor: {categories}."
        elif match_state == _NO_MATCH_FOUND:
            reason = "Blocked: Model Armor returned no complete filter decision."
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="high",
                    detail=f"invocationResult={invocation or 'absent'}: not every filter ran.",
                )
            )
        else:
            reason = "Blocked: Model Armor returned no usable verdict."
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="high",
                    detail=f"filterMatchState={match_state or 'absent'}: no screening decision.",
                )
            )
        return GuardrailVerdict(
            allowed=False,
            direction=direction,
            findings=tuple(findings),
            sanitized_text=None,
            reason=reason,
        )

    @staticmethod
    def _is_match(node: Any) -> bool:
        return isinstance(node, dict) and node.get("matchState") == _MATCH_FOUND

    def _parse_pi_jailbreak(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        node: Any = (filter_results.get("pi_and_jailbreak") or {}).get("piAndJailbreakFilterResult")
        if not self._is_match(node):
            return []
        confidence = str(node.get("confidenceLevel", "")).lower() or "high"
        return [
            GuardrailFinding(
                category=GuardrailCategory.PROMPT_INJECTION,
                confidence=confidence,
                detail="Model Armor prompt-injection / jailbreak filter matched.",
            )
        ]

    def _parse_sensitive_data(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        inspect = (filter_results.get("sdp") or {}).get("sdpFilterResult", {}).get("inspectResult")
        if not self._is_match(inspect):
            return []
        info_types = sorted(
            {
                str(f.get("infoType", ""))
                for f in (inspect.get("findings") or [])
                if f.get("infoType")
            }
        )
        detail = (
            f"Sensitive data detected: {', '.join(info_types)}."
            if info_types
            else "Model Armor Sensitive Data Protection filter matched."
        )
        return [
            GuardrailFinding(
                category=GuardrailCategory.SENSITIVE_DATA,
                confidence="high",
                detail=detail,
            )
        ]

    def _parse_malicious_uris(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        node = (filter_results.get("malicious_uris") or {}).get("maliciousUriFilterResult")
        if not self._is_match(node):
            return []
        return [
            GuardrailFinding(
                category=GuardrailCategory.MALICIOUS_URL,
                confidence="high",
                detail="Model Armor malicious-URI filter matched.",
            )
        ]

    def _parse_rai(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        rai: Any = (filter_results.get("rai") or {}).get("raiFilterResult")
        if not self._is_match(rai):
            return []
        sub_results = rai.get("raiFilterTypeResults", {}) or {}
        findings: list[GuardrailFinding] = []
        for key, category in _RAI_CATEGORY.items():
            sub: Any = sub_results.get(key)
            if not self._is_match(sub):
                continue
            confidence = str(sub.get("confidenceLevel", "")).lower() or "medium"
            findings.append(
                GuardrailFinding(
                    category=category,
                    confidence=confidence,
                    detail=f"Model Armor Responsible-AI filter matched: {key}.",
                )
            )
        if not findings:
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="medium",
                    detail="Model Armor Responsible-AI filter matched.",
                )
            )
        return findings

    def _extract_sanitized_text(
        self, filter_results: dict[str, Any], original_text: str
    ) -> str | None:
        deidentify = (
            (filter_results.get("sdp") or {}).get("sdpFilterResult", {}).get("deidentifyResult")
        )
        if isinstance(deidentify, dict):
            data = deidentify.get("data") or {}
            text = data.get("text")
            if isinstance(text, str) and text:
                return text
        return original_text
