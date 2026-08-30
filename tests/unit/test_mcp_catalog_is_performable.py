"""The declared MCP tools must ask for what this tree can actually act on.

The catalog is the contract surface a peer agent reads, and it has now been wrong twice about
the same tool for two different reasons. Until 2026-08-28 ``cross_validate`` declared
``extract_ids``, which resolves against nothing: ``CrossValidator.validate`` takes
:class:`DocumentExtract` objects and no port maps an id to one. That narrowing swapped the ids
for ``documents`` and required them, on the reasoning that documents are what the checks run
over. True of the CHECKS. Not true of this TOOL: ``agent.tools.cross_validate`` takes
``(application_id, applicant_name)``, returns the governed check catalog, and reads no document
at all. So a peer agent was still being told to send a payload the callable would silently
ignore, in service of a run it does not perform.

Both narrowings were written by reading, and reading is what missed it the second time. These
tests compare the two sides mechanically instead: the catalog is a literal, the callables are
the implementation, and neither can move without the other. ``actor`` and ``settings`` are
excluded by name and with a reason -- ``actor`` is the verified identity the server resolves and
accepting it from a caller would be identity spoofing, and ``settings`` is the DI seam.

What this file does NOT change is whether the catalog is served. That is answered separately in
``test_tool_catalog_is_declared_and_deliberately_unserved.py``, and for a different reason.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from loan_doc_intel.adapters.gcp.mcp_tool_catalog import McpToolCatalogAdapter
from loan_doc_intel.agent.tools import TOOL_FUNCTIONS
from loan_doc_intel.config import Settings

CONFIG_PATH = "config/settings.yaml"

#: Parameters the model never supplies, so they are absent from the declared schema.
_SERVER_OWNED = frozenset({"actor", "settings"})


@pytest.fixture
def catalog() -> McpToolCatalogAdapter:
    return McpToolCatalogAdapter(Settings.load(CONFIG_PATH))


def _callable_inputs(fn: Any) -> tuple[set[str], set[str]]:
    """(all model-facing parameter names, the subset with no default)."""
    parameters = [
        p for name, p in inspect.signature(fn).parameters.items() if name not in _SERVER_OWNED
    ]
    every = {p.name for p in parameters}
    required = {p.name for p in parameters if p.default is inspect.Parameter.empty}
    return every, required


def test_the_catalog_declares_exactly_the_callables_that_exist(
    catalog: McpToolCatalogAdapter,
) -> None:
    declared = {spec.name for spec in catalog.list_tools()}
    implemented = {fn.__name__ for fn in TOOL_FUNCTIONS}
    assert declared == implemented


@pytest.mark.parametrize("fn", TOOL_FUNCTIONS, ids=lambda fn: fn.__name__)
def test_each_declared_schema_matches_the_callable_it_names(
    catalog: McpToolCatalogAdapter, fn: Any
) -> None:
    """The guard the second narrowing needed and did not have.

    This fails on the 2026-08-28 shape: ``cross_validate`` declared ``documents`` as REQUIRED
    while its callable accepts no such parameter, so a conforming caller would have sent a
    field that went nowhere.
    """
    spec = catalog.get_tool(fn.__name__)
    assert spec is not None, f"{fn.__name__} is implemented but not declared"

    every, required = _callable_inputs(fn)
    assert set(spec.input_schema["properties"]) == every, (
        f"{fn.__name__}'s declared inputs and its signature disagree; a peer agent would send "
        "a payload the callable cannot accept, or omit one it needs"
    )
    assert set(spec.input_schema["required"]) == required


def test_no_declared_tool_asks_for_an_identifier_nothing_can_resolve(
    catalog: McpToolCatalogAdapter,
) -> None:
    """The original defect, kept as an assertion so it cannot come back by another name."""
    for spec in catalog.list_tools():
        assert "extract_ids" not in spec.input_schema["properties"], (
            f"{spec.name} names extract ids: CrossValidator.validate takes DocumentExtract "
            "objects and no port maps an id to one"
        )


def test_the_describing_tool_does_not_advertise_itself_as_the_running_one(
    catalog: McpToolCatalogAdapter,
) -> None:
    """A schema can match a signature perfectly and still describe the wrong capability.

    ``cross_validate`` names the checks; ``process_application`` performs them. The inputs
    alone cannot express that difference, so the description carries it and is asserted here
    rather than left to a reader.
    """
    spec = catalog.get_tool("cross_validate")
    assert spec is not None
    assert "process_application" in spec.description, (
        "cross_validate must point at the tool that actually runs the checks; a peer agent "
        "reading only this description would call it expecting verdicts"
    )


def test_no_declared_schema_leaks_a_server_owned_parameter(
    catalog: McpToolCatalogAdapter,
) -> None:
    """``actor`` must not be askable. A client-asserted actor is a forged audit subject."""
    for spec in catalog.list_tools():
        assert not (_SERVER_OWNED & set(spec.input_schema["properties"])), spec.name
