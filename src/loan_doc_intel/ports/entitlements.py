"""EntitlementsPort : resolve the SERVER-SIDE owner of a protected object.

The hexagon boundary for object-level authorization. Given an object id (an application id
or a document id submitted in the request body), the adapter returns the object's
:class:`~loan_doc_intel.domain.entitlements.ObjectOwner` (its owning tenant + the roles
permitted to act on it) from a server-side source of truth, or ``None`` when the object is
unknown. The domain gate
(:func:`~loan_doc_intel.domain.entitlements.authorize_object`) then compares the VERIFIED
principal against that owner, fail-closed.

The active profile picks the adapter:

* ``local`` resolves from a seeded in-process registry (offline demos / tests),
* ``gcp`` resolves from the managed regional ACL store (Firestore object-ownership records),
* ``platform`` delegates to the shared platform entitlements service over HTTP, and
* ``onprem`` is the placeholder for the client's own entitlement service.

Keeping the owner source behind a port keeps it swappable by configuration (P-02) and,
crucially, SERVER-SIDE: the client can never assert who owns the object it submits, so a
spoofed application id cannot read or process another tenant's applicant.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.entitlements import ObjectOwner


@runtime_checkable
class EntitlementsPort(Protocol):
    def owner(self, object_id: str) -> ObjectOwner | None:
        """Resolve the SERVER-SIDE owner of ``object_id``; ``None`` if unknown (deny)."""
        ...
