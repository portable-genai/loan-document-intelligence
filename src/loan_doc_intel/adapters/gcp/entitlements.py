"""GCP EntitlementsPort adapter: resolve object ownership from the managed ACL store.

In the managed profile the SERVER-SIDE owner of an object (its owning tenant + the roles
permitted to act on it) is held in a regional Firestore collection, written by the
provisioning / entitlement pipeline when an application is ingested. This adapter reads that
record by object id and maps it to an :class:`ObjectOwner`; an object with no record resolves
to ``None`` and is denied fail-closed by the domain gate. The Firestore client import is lazy
(mirroring the other gcp adapters) so the SDK-free local / onprem profiles never import it,
and the ACL store is co-located in the deploy region (``asia-southeast1``) so applicant-linked
ownership records keep the same data residency as the applicant PII they gate.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.entitlements import ObjectOwner

# Firestore collection holding one document per protected object, keyed by object id:
#   { "tenant": "<owner-tenant>", "allowed_roles": ["group:...", ...] }
_COLLECTION = "loan-doc-object-acls"


class GcpEntitlementsAdapter:
    """Resolve object ownership from the managed (Firestore) ACL store (secure mode)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def owner(self, object_id: str) -> ObjectOwner | None:
        # Lazy import keeps the SDK-free profiles import-clean (mirrors the other gcp adapters).
        from google.cloud import firestore

        client = firestore.Client(project=self._settings.project_id or None)
        snapshot = client.collection(_COLLECTION).document(object_id).get()
        if not getattr(snapshot, "exists", False):
            return None
        data = snapshot.to_dict() or {}
        tenant = str(data.get("tenant", "")).strip()
        if not tenant:
            return None
        roles = frozenset(str(role) for role in (data.get("allowed_roles") or ()))
        return ObjectOwner(tenant=tenant, allowed_roles=roles)
