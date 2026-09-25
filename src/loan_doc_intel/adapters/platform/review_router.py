"""Platform/GCP ReviewRouterPort: submit the routed loan case review to human-review-console
(``review-kit``).

Builds the review from the escalated loan case and submits it to the human-review-console service
intake (``POST /v1/service/reviews``). The human-review-console base URL comes from
``HUMAN_REVIEW_URL``. It is bound under both the ``platform`` and ``gcp`` profiles because it
makes a real network call to a sibling service.

**Two bearers, chosen by one variable.** A deployed human-review-console is an embedded app
behind the portal's IAP edge, so under ``gcp`` ``HUMAN_REVIEW_URL`` is the edge path
``https://<edge-host>/apps/human-review-console/api`` and the edge accepts one bearer: a
Google-signed ID token minted for the deployment's IAP OAuth client id, named by
``HUMAN_REVIEW_IAP_AUDIENCE``. When that variable is set, the router hands ``review-kit`` a
bearer provider that mints such a token with this service's own workload identity, once per
submission, so an expiring token is never reused. When it is unset, the router keeps the static
S2S path: the credentials reuse this repo's shared platform S2S env vars (``S2S_TOKEN`` /
``S2S_SIGNING_KEY``, sourced from ``adapters/platform/_s2s``). Boot refuses ``gcp`` with routing
on and no audience (see ``config._refuse_unreachable_console``), so the static path is what
``platform`` and a directly reached console use.

The minting import is lazy, so this module still imports with no Google SDK installed.
"""

from __future__ import annotations

from collections.abc import Callable

from review_kit import ReviewClient
from review_kit.client import Transport

from ...config import Settings, review_iap_audience
from ...domain.models import LoanApplicationCase
from ...envread import read_env_setting
from .._review_payload import case_to_review
from ._s2s import SIGNING_KEY_ENV, TOKEN_ENV

#: Mints a Google-signed ID token for an audience. Injectable so tests never reach a metadata
#: server; the default uses this service's own workload identity.
Minter = Callable[[str], str]


def fetch_id_token(audience: str) -> str:  # pragma: no cover - needs a workload identity
    """Mint a Google-signed ID token for ``audience`` with this process's own identity."""
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token as _fetch

    token: str = _fetch(Request(), audience)
    return token


class PlatformReviewRouter:
    """Submit escalated loan cases to human-review-console (rule R8), reusing the shared S2S
    transport config.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        mint: Minter | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._settings = settings
        # Read at construction, beside the boot check, so an emptied or backend-path audience is
        # a named refusal rather than a failed hand-off on every case.
        self._audience = review_iap_audience()
        self._mint = mint or fetch_id_token
        self._transport = transport

    def route(self, case: LoanApplicationCase, *, maker: str, tenant: str = "") -> None:
        # Three states, collapsed DELIBERATELY onto the refusal below: there is no default
        # human-review-console to fall back to, so an unset variable and one an operator emptied
        # both mean
        # "no review service is configured" and both must refuse to route. The collapse is
        # onto the CLOSED direction, which is why it is safe here and not in the base-URL
        # adapters, whose default is the pod's own loopback.
        base_url = read_env_setting("HUMAN_REVIEW_URL").value
        if not base_url:
            raise RuntimeError(
                "HUMAN_REVIEW_URL must be set to route reviews to human-review-console"
            )
        audience, mint = self._audience, self._mint
        client = ReviewClient(
            base_url,
            token_env=TOKEN_ENV,
            signing_key_env=SIGNING_KEY_ENV,
            transport=self._transport,
            bearer_provider=None if audience is None else (lambda: mint(audience)),
        )
        client.submit(
            case_to_review(case, maker=maker, tenant=tenant),
            actor="doc5-loan-document-intelligence",
        )
