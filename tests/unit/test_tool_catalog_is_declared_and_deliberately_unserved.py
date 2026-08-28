"""Why this tree declares a governed tool catalog and deliberately does not serve it.

Fourteen trees in the fleet serve the catalog they declare, over MCP 2026-07-28 on stdio. This
one does not, and the reason has to be executable rather than asserted, because "we decided not
to" is exactly the kind of claim that rots into "nobody got round to it".

**The reason is identity, and it is not a gap in the plumbing.** Every domain entry point behind
this catalog authorizes FIRST, fail-closed, against a server-resolved owner:
``LoanDocService._authorize`` asks the ``EntitlementsPort`` who owns the object and denies an
unknown one outright. MCP stdio verifies no end user at all. The fleet's serving trees supply an
EMPTY principal there and rely on entitlement FILTERING, which degrades safely: an empty
principal sees untagged public data and nothing else. This tree's authorization is not
filtering. It is an object-level gate that RAISES, so the same empty principal is refused every
application and every document.

That leaves three options and only one honest one. Serving with an empty principal would bind
cleanly and refuse every call, which is a dead surface wearing a green tick. Manufacturing a
principal or an owner record to make the calls succeed would forge the entitlement the gate
exists to check, on a service whose whole subject is somebody's payslips. Leaving it declared
and unserved is the truthful state, and these guards are what stop that state from being
mistaken for an oversight.

Separately, one declaration was mis-shaped rather than merely unservable, and it has been
narrowed. That is recorded in ``adapters/gcp/mcp_tool_catalog.py`` where the declaration lives.
"""

from __future__ import annotations

import pathlib

import pytest

from loan_doc_intel.adapters.gcp.mcp_tool_catalog import McpToolCatalogAdapter
from loan_doc_intel.config import Settings
from loan_doc_intel.domain.entitlements import ObjectOwner, require_object_access
from loan_doc_intel.domain.errors import AccessDenied
from loan_doc_intel.domain.identity import ANONYMOUS, Principal

#: The identity MCP stdio would supply. ``ANONYMOUS`` is the kit's own name for "resolved by
#: nothing", which is precisely what a stdio transport can establish about its caller.
_MCP_STDIO_PRINCIPAL = ANONYMOUS


@pytest.fixture
def catalog() -> McpToolCatalogAdapter:
    return McpToolCatalogAdapter(Settings.load())


def test_the_identity_mcp_stdio_would_supply_is_refused_an_unknown_object(
    catalog: McpToolCatalogAdapter,
) -> None:
    """The reason this tree does not serve, executed rather than asserted.

    An MCP caller names an application or a document id. The entitlements port resolves no
    owner for a caller that owns nothing, and an unknown object is denied fail-closed. So every
    tool in this catalog would refuse.
    """
    assert catalog.list_tools(), "an empty catalog would make this vacuous"

    with pytest.raises(AccessDenied, match="unknown object"):
        require_object_access(_MCP_STDIO_PRINCIPAL, "app-0001", None)


def test_the_same_identity_is_refused_even_when_the_object_is_known(
    catalog: McpToolCatalogAdapter,
) -> None:
    """The stronger half: supplying the owner record does not rescue it.

    Without this, the guard above proves only that an unknown id is denied, which someone could
    read as "give it a store and it works". The gate is about the CALLER, so a known object with
    a real owner refuses the same principal for the same reason.
    """
    owner = ObjectOwner(tenant="tenant-a", allowed_roles=frozenset({"underwriter"}))

    with pytest.raises(AccessDenied):
        require_object_access(_MCP_STDIO_PRINCIPAL, "app-0001", owner)


def test_an_entitled_principal_is_admitted_so_the_refusals_are_about_identity() -> None:
    """The other half, without which the guards above prove only that something always raises."""
    owner = ObjectOwner(tenant="tenant-a", allowed_roles=frozenset({"underwriter"}))
    entitled = Principal(
        subject="uw@example.invalid", principals=("underwriter",), tenant="tenant-a"
    )

    assert require_object_access(entitled, "app-0001", owner) is owner


def test_no_declared_tool_names_an_identifier_nothing_can_resolve(
    catalog: McpToolCatalogAdapter,
) -> None:
    """``cross_validate`` declared ``extract_ids`` and nothing resolves an extract id.

    Distinct from the identity argument above: this one was not merely unservable pending a
    principal, it was unservable at all, because ``CrossValidator.validate`` takes
    ``DocumentExtract`` objects and no store maps an id to one. It now declares the extracts it
    actually validates.
    """
    spec = catalog.get_tool("cross_validate")

    assert spec is not None
    assert "extract_ids" not in spec.input_schema["properties"], (
        "extract_ids resolves against nothing: CrossValidator.validate takes DocumentExtract "
        "objects and no port maps an id to one"
    )


def test_this_tree_ships_no_mcp_server() -> None:
    """An MCP server must not appear here until the refusals above have an answer.

    Not a style rule. Adding one means either serving tools that always refuse, or supplying a
    principal nobody verified for a service that reads somebody's payslips. Whoever adds the
    server deletes this guard deliberately and says which it is.
    """
    package = pathlib.Path(__file__).resolve().parents[2] / "src" / "loan_doc_intel"

    assert not (package / "mcp").exists(), (
        "an mcp/ package appeared: every declared tool is refused the identity MCP stdio "
        "supplies, so serving this catalog needs a reviewed answer to that first"
    )
