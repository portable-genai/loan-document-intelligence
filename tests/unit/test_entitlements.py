"""Unit tests for the C2 object-level authorization gate + the local owner registry.

These prove the fail-closed entitlement decision that closes the "no server-side object
authz" gap: authentication proves WHO is calling, but a caller may only act on objects their
tenant owns (resolved SERVER-SIDE, never from the client-submitted body). Cross-tenant access,
a wrong role, and an unknown object are all denied.
"""

from __future__ import annotations

import pytest

from loan_doc_intel.adapters.local.entitlements import LocalEntitlementsAdapter
from loan_doc_intel.config import Settings
from loan_doc_intel.domain.entitlements import (
    ObjectOwner,
    authorize_object,
    require_object_access,
)
from loan_doc_intel.domain.errors import AccessDenied
from loan_doc_intel.domain.identity import Principal

_OBJECT = "app-fictional-0001"
_ROLES = frozenset({"group:loan-analyst"})

# Mirrors the seeded local personas: an entitled demo-bank analyst and the cross-tenant
# other-bank persona (which authenticates but owns nothing in demo-bank).
_ANALYST = Principal(
    subject="demo.analyst@bank.example",
    principals=("group:loan-analyst", "group:underwriting"),
    tenant="demo-bank",
)
_CROSS_TENANT = Principal(
    subject="user@other-tenant.example",
    principals=("group:loan-analyst",),
    tenant="other-bank",
)


# --------------------------------------------------------------------------- #
# authorize_object : the pure fail-closed gate.
# --------------------------------------------------------------------------- #
def test_entitled_principal_is_authorized() -> None:
    # Tenant matches AND a permitted role is held -> no raise.
    authorize_object(_ANALYST, object_id=_OBJECT, object_tenant="demo-bank", allowed_roles=_ROLES)


def test_cross_tenant_principal_is_denied() -> None:
    with pytest.raises(AccessDenied):
        authorize_object(
            _CROSS_TENANT, object_id=_OBJECT, object_tenant="demo-bank", allowed_roles=_ROLES
        )


def test_right_tenant_but_no_permitted_role_is_denied() -> None:
    stranger = Principal(subject="x@demo-bank", principals=("group:marketing",), tenant="demo-bank")
    with pytest.raises(AccessDenied):
        authorize_object(
            stranger, object_id=_OBJECT, object_tenant="demo-bank", allowed_roles=_ROLES
        )


def test_explicit_object_grant_authorizes_without_a_role() -> None:
    granted = Principal(
        subject="x@demo-bank",
        principals=(f"application:{_OBJECT}",),
        tenant="demo-bank",
    )
    # No permitted role, but an explicit per-object grant -> authorized.
    authorize_object(
        granted, object_id=_OBJECT, object_tenant="demo-bank", allowed_roles=frozenset()
    )


def test_empty_object_tenant_is_denied() -> None:
    # An unknown object surfaced as an empty owning tenant must never authorize.
    with pytest.raises(AccessDenied):
        authorize_object(_ANALYST, object_id=_OBJECT, object_tenant="", allowed_roles=_ROLES)


# --------------------------------------------------------------------------- #
# require_object_access : resolves an owner-or-None and enforces (unknown -> deny).
# --------------------------------------------------------------------------- #
def test_unknown_object_is_denied() -> None:
    with pytest.raises(AccessDenied):
        require_object_access(_ANALYST, "app-does-not-exist", owner=None)


def test_require_access_returns_owner_when_entitled() -> None:
    owner = ObjectOwner(tenant="demo-bank", allowed_roles=_ROLES)
    assert require_object_access(_ANALYST, _OBJECT, owner) is owner


# --------------------------------------------------------------------------- #
# LocalEntitlementsAdapter : the seeded SERVER-SIDE owner registry.
# --------------------------------------------------------------------------- #
def _adapter() -> LocalEntitlementsAdapter:
    return LocalEntitlementsAdapter(Settings(profile="local"))


def test_local_adapter_owns_seeded_application() -> None:
    owner = _adapter().owner("app-fictional-0001")
    assert owner is not None
    assert owner.tenant == "demo-bank"
    assert "group:loan-analyst" in owner.allowed_roles


def test_local_adapter_unknown_object_is_none() -> None:
    assert _adapter().owner("app-unknown-9999") is None


def test_local_adapter_end_to_end_cross_tenant_denied() -> None:
    adapter = _adapter()
    owner = adapter.owner("app-fictional-0001")
    with pytest.raises(AccessDenied):
        require_object_access(_CROSS_TENANT, "app-fictional-0001", owner)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
