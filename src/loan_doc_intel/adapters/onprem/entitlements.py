"""On-prem EntitlementsPort placeholder : integrate the client's own ACL / entitlement store.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile this
port resolves object ownership from the managed ACL store; switching ``profile`` to ``onprem``
rebinds it here. Fill this in to resolve an object's owning tenant and permitted roles from
your on-premise entitlement service, then map them to an :class:`ObjectOwner`. Like the other
on-prem placeholders it constructs with no external dependency and fails fast: an unresolved
owner must never be silently treated as allowed : the domain gate is fail-closed, and this
stub refuses to guess. Core domain logic is unchanged.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.entitlements import ObjectOwner

_MESSAGE = (
    "On-prem EntitlementsPort adapter is a migration placeholder; implement resolution of an "
    "object's owning tenant and permitted roles from your on-premise entitlement store. Core "
    "domain logic is unchanged."
)


class OnPremEntitlementsAdapter:
    """Placeholder entitlements adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def owner(self, object_id: str) -> ObjectOwner | None:
        raise NotImplementedError(_MESSAGE)
