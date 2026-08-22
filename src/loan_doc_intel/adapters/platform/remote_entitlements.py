"""Remote-platform entitlements adapter : thin HTTP client to the shared ACL service.

In the full platform deployment, object ownership is resolved through a shared platform
entitlements / ACL service rather than a per-repo store. This adapter implements
:class:`EntitlementsPort` against that service's owner-lookup endpoint:

* ``owner`` -> ``GET /v1/objects/{object_id}/owner`` (``200`` -> owner record,
  ``404`` -> ``None`` -> denied fail-closed by the domain gate)

The base URL is read from ``HRZ_ENTITLEMENTS_URL`` with a localhost default. A transport
failure or an unexpected status is raised (never silently treated as ``None``): an ACL the
service cannot resolve must not become an implicit allow.
"""

from __future__ import annotations

import httpx

from ...domain.entitlements import ObjectOwner
from ...domain.errors import LoanDocError
from ...envread import setting_or_default
from . import _s2s

_DEFAULT_URL = "http://localhost:8087"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class RemoteEntitlementsError(LoanDocError):
    """Raised when the remote entitlements service returns an unexpected status."""


class RemoteEntitlementsAdapter:
    """HTTP client for the shared platform entitlements / ACL service."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = _s2s.validate_base_url(
            setting_or_default("HRZ_ENTITLEMENTS_URL", _DEFAULT_URL), service="entitlements service"
        )

    def owner(self, object_id: str) -> ObjectOwner | None:
        url = f"{self._base_url}/v1/objects/{object_id}/owner"
        try:
            response = httpx.get(url, timeout=_TIMEOUT, headers=_s2s.headers())
        except httpx.HTTPError as exc:
            raise RemoteEntitlementsError(f"entitlements request to {url} failed: {exc}") from exc
        if response.status_code == 404:
            return None
        if response.status_code // 100 != 2:
            raise RemoteEntitlementsError(
                f"entitlements {url} returned {response.status_code}: {response.text[:200]}"
            )
        body = response.json() or {}
        tenant = str(body.get("tenant", "")).strip()
        if not tenant:
            return None
        roles = frozenset(str(role) for role in (body.get("allowed_roles") or ()))
        return ObjectOwner(tenant=tenant, allowed_roles=roles)
