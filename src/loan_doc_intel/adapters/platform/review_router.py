"""Platform/GCP ReviewRouterPort: submit the routed loan case review to Hrz7 (``review-kit``).

Builds the review from the escalated loan case and submits it to the Hrz7 service intake
(``POST /v1/service/reviews``), S2S-authenticated. The Hrz7 base URL comes from
``HUMAN_REVIEW_URL`` and the S2S credentials reuse this repo's shared platform S2S env vars
(``S2S_TOKEN`` / ``S2S_SIGNING_KEY``, sourced from ``adapters/platform/_s2s``), set on the
Cloud Run / platform service. No cloud SDK is involved (the kit uses stdlib ``urllib`` plus the
wire-compatible S2S headers), so this module imports cleanly with no GCP SDK; it is bound under
both the ``platform`` and ``gcp`` profiles because it makes a real network call to a sibling
service.
"""

from __future__ import annotations

from review_kit import ReviewClient

from ...config import Settings
from ...domain.models import LoanApplicationCase
from ...envread import read_env_setting
from .._review_payload import case_to_review
from ._s2s import SIGNING_KEY_ENV, TOKEN_ENV


class PlatformReviewRouter:
    """Submit escalated loan cases to Hrz7 (rule R8), reusing the shared S2S transport config."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def route(  # pragma: no cover - needs live Hrz7
        self, case: LoanApplicationCase, *, maker: str, tenant: str = ""
    ) -> None:
        # Three states, collapsed DELIBERATELY onto the refusal below: there is no default
        # Hrz7 to fall back to, so an unset variable and one an operator emptied both mean
        # "no review service is configured" and both must refuse to route. The collapse is
        # onto the CLOSED direction, which is why it is safe here and not in the base-URL
        # adapters, whose default is the pod's own loopback.
        base_url = read_env_setting("HUMAN_REVIEW_URL").value
        if not base_url:
            raise RuntimeError("HUMAN_REVIEW_URL must be set to route reviews to Hrz7")
        client = ReviewClient(
            base_url,
            token_env=TOKEN_ENV,
            signing_key_env=SIGNING_KEY_ENV,
        )
        client.submit(
            case_to_review(case, maker=maker, tenant=tenant),
            actor="doc5-loan-document-intelligence",
        )
