"""ReviewRouterPort: the boundary that routes an escalated loan case to Hrz7 (rule R8).

Every ``LoanApplicationCase`` is consequential and always requires human review (the
underwriter is the checker, P-06). Rule R8 says a producer that sets ``requires_human_review``
MUST route the item to the Hrz7 Human-Review & Maker-Checker Console rather than terminate the
escalation in a per-repo boolean. This port is that hand-off. The domain stays pure: the adapter
(not this port) depends on the shared ``review-kit`` client and does the S2S submission.

The tenant is a call parameter, not a field on the case: ``LoanApplicationCase`` carries no
tenant, so the verified :class:`~loan_doc_intel.domain.identity.Principal`'s tenant is threaded
in by the service at the routing boundary.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import LoanApplicationCase


@runtime_checkable
class ReviewRouterPort(Protocol):
    def route(self, case: LoanApplicationCase, *, maker: str, tenant: str = "") -> None:
        """Route an escalated loan case to Hrz7 for human review (idempotent per case is ideal)."""
        ...
