"""Server-side object-level authorization for loan applications and documents (C2).

The verified :class:`~loan_doc_intel.domain.identity.Principal` proves WHO is calling; this
module decides WHAT they may act on. The application and its documents arrive in the request
body and are therefore client-controlled / spoofable, so the object's owner is NEVER taken
from the body: it is resolved SERVER-SIDE from an entitlements registry keyed by object id
(the ``EntitlementsPort`` adapters), and this fail-closed gate compares the VERIFIED
principal against that owner.

Access model (deliberately simple, override per deployment):

* tenant isolation is mandatory: ``principal.tenant`` must equal the object's owning tenant,
  so an application id alone can never cross a tenant boundary; AND
* the principal must either hold an explicit ``application:<object_id>`` grant (a fine-grained
  entitlement provisioned by the identity/entitlement system) OR be a member of one of the
  object's ``allowed_roles``.

Everything else is denied. An unknown object (no owner record) is denied too. Pure stdlib;
raising :class:`AccessDenied` maps to HTTP 403 at the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import AccessDenied
from .identity import Principal


@dataclass(frozen=True, slots=True)
class ObjectOwner:
    """The SERVER-SIDE ACL record for a protected object (never client-supplied).

    ``tenant`` is the object's owning tenant (tenant isolation); ``allowed_roles`` are the
    entitlement groups permitted to act on the object within that tenant.
    """

    tenant: str
    allowed_roles: frozenset[str] = field(default_factory=frozenset)


def authorize_object(
    principal: Principal,
    *,
    object_id: str,
    object_tenant: str,
    allowed_roles: frozenset[str] = frozenset(),
) -> None:
    """Fail-closed object-level authorization: raise :class:`AccessDenied` unless entitled.

    Grants access only when the VERIFIED principal's tenant owns the object AND the principal
    either carries an explicit ``application:<object_id>`` grant or is a member of one of
    ``allowed_roles``. Default deny; an empty ``object_tenant`` (an unknown object) always
    denies.
    """
    if not object_tenant or principal.tenant != object_tenant:
        raise AccessDenied(
            f"{principal.actor} (tenant {principal.tenant!r}) is not entitled to object "
            f"{object_id!r} owned by tenant {object_tenant!r}"
        )
    if f"application:{object_id}" in principal.principals:
        return
    if any(role in allowed_roles for role in principal.principals):
        return
    raise AccessDenied(
        f"{principal.actor} is not entitled to object {object_id!r} "
        "(no explicit object grant and no permitted role)"
    )


def require_object_access(
    principal: Principal, object_id: str, owner: ObjectOwner | None
) -> ObjectOwner:
    """Authorize ``principal`` against a SERVER-resolved owner; unknown object -> deny.

    ``owner`` is the record the ``EntitlementsPort`` resolved SERVER-SIDE for ``object_id``
    (``None`` when the object is unknown, which is denied fail-closed). Returns the owner on
    success so callers may reuse it.
    """
    if owner is None:
        raise AccessDenied(f"{principal.actor} is not entitled to unknown object {object_id!r}")
    authorize_object(
        principal,
        object_id=object_id,
        object_tenant=owner.tenant,
        allowed_roles=owner.allowed_roles,
    )
    return owner
