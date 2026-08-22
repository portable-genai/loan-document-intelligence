"""Identity value objects for server-side, verified principals.

The service never trusts a client-asserted ``actor`` or ACL. A :class:`Principal` is
resolved server-side by an :class:`~loan_doc_intel.ports.identity.IdentityPort` adapter
(local dev persona, GCP IAP-verified assertion, or an on-prem client IdP) from the inbound
transport context, and becomes the audit actor (the underwriter / service identity written
into the WORM audit trail). Pure stdlib: nothing here imports a web framework or a cloud SDK.

Nothing is DECLARED in this module any more. :class:`Principal`, :class:`RequestContext`,
:class:`IdentityError` and :data:`ANONYMOUS` come from :mod:`hex_service_kit.identity`, which
owns the one definition the whole catalog shares. They were hand-copied into every repo, which
is a shape that drifts silently: two copies of a frozen dataclass stay assignable to each other
in mypy's eyes long after their fields have diverged. Re-exporting keeps the existing
``from ..domain.identity import Principal`` call sites working while there is only one
definition left to change.
"""

from __future__ import annotations

from hex_service_kit.identity import ANONYMOUS as ANONYMOUS
from hex_service_kit.identity import IdentityError as IdentityError
from hex_service_kit.identity import Principal as Principal
from hex_service_kit.identity import RequestContext as RequestContext

__all__ = ["ANONYMOUS", "IdentityError", "Principal", "RequestContext"]
