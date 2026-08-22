"""Local EntitlementsPort adapter: a seeded, in-process object-owner registry.

The SDK-free ``local`` profile must run fully offline, so this adapter is the SERVER-SIDE
source of truth for object ownership: a small seeded registry mapping each demo / fixture
object id (the synthetic applications AND their documents) to its owning tenant and the roles
permitted to act on it. It lets the offline demos and tests exercise per-tenant object-level
authorization (a ``demo-bank`` persona is entitled; the seeded ``other-bank`` persona is not)
without standing up any external ACL store. Unknown objects resolve to ``None`` and are denied
fail-closed by the domain gate. Every id here is clearly fictional.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.entitlements import ObjectOwner

# The demo tenant that owns every seeded fixture object. The cross-tenant persona
# (``other-bank``, see adapters/local/identity.py) is deliberately NOT this tenant, so it is
# denied by the tenant-isolation check even though it authenticates successfully.
_OWNER_TENANT = "demo-bank"

# Roles permitted to act on a seeded object within the owning tenant. Mirrors the seeded local
# personas' entitlement groups (analyst / underwriting / approver / audit).
_ALLOWED_ROLES: frozenset[str] = frozenset(
    {"group:loan-analyst", "group:underwriting", "group:loan-approver", "group:audit"}
)

# Seeded object ids owned by demo-bank: the synthetic applications (the /v1/process object)
# AND their documents (the /v1/extract object), so both object checks pass for a demo-bank
# persona while a cross-tenant persona is refused.
_SEEDED_OBJECT_IDS: frozenset[str] = frozenset(
    {
        # Applications.
        "app-fictional-0001",
        "app-fictional-0002",
        # Their documents.
        "doc-payslip-2026-04",
        "doc-bank-2026-04",
        "doc-payslip-0002",
        "doc-bank-0002",
    }
)


class LocalEntitlementsAdapter:
    """Resolve object ownership from a seeded in-process registry (local profile, no store)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._registry: dict[str, ObjectOwner] = {
            object_id: ObjectOwner(tenant=_OWNER_TENANT, allowed_roles=_ALLOWED_ROLES)
            for object_id in _SEEDED_OBJECT_IDS
        }

    def owner(self, object_id: str) -> ObjectOwner | None:
        """The seeded owner of ``object_id``, or ``None`` when unknown (denied fail-closed)."""
        return self._registry.get(object_id)

    def seed(self, object_id: str, tenant: str, allowed_roles: frozenset[str]) -> None:
        """Register an additional object owner (for a demo / test planting a scenario)."""
        self._registry[object_id] = ObjectOwner(tenant=tenant, allowed_roles=allowed_roles)
