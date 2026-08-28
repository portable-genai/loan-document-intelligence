"""MCP tool catalog adapter : the governed tool surface for system B5.

Backs the domain ``ToolCatalogPort`` by exposing B5's governed, least-privilege
capabilities as ``ToolSpec`` objects: ``process_application``, ``extract_document`` and
``cross_validate``. These are the tools the agent (or a peer agent) may invoke : each with
an explicit JSON input schema so access is scoped and auditable (P-07, least privilege).

Interop: the catalog speaks **MCP 2026-07-28**. In an ADK deployment these specs are
surfaced to the agent through an ``McpToolset`` connected to an MCP server that fronts the
domain services; here the adapter only *declares* the governed catalog. The ``mcp``
package is imported lazily and only when an actual MCP wire object is requested.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import ToolSpec

MCP_PROTOCOL_VERSION = "2026-07-28"

_DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "doc_type": {
            "type": "string",
            "enum": ["payslip", "bank_statement", "tax_return", "employment_letter", "id"],
        },
        "uri": {"type": "string"},
    },
    "required": ["id", "doc_type", "uri"],
    "additionalProperties": False,
}


def _build_catalog() -> dict[str, ToolSpec]:
    """Declare the three governed tools with explicit, least-privilege input schemas."""
    return {
        "process_application": ToolSpec(
            name="process_application",
            description=(
                "Process a loan application: extract income/bank data from its documents "
                "and deterministically cross-validate into a cited income verification. "
                "Output requires human review (the underwriter decides)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "documents": {"type": "array", "items": _DOCUMENT_SCHEMA},
                },
                "required": ["application_id", "documents"],
                "additionalProperties": False,
            },
        ),
        "extract_document": ToolSpec(
            name="extract_document",
            description=(
                "Extract structured fields and dated line items from a single applicant "
                "document via Document AI."
            ),
            input_schema={
                "type": "object",
                "properties": {"document": _DOCUMENT_SCHEMA},
                "required": ["document"],
                "additionalProperties": False,
            },
        ),
        "cross_validate": ToolSpec(
            name="cross_validate",
            description=(
                "Run the deterministic consistency checks across an applicant's document "
                "extracts (salary-credit match, name/address, balance trend, affordability)."
            ),
            # This declared ``extract_ids`` until 2026-08-28, and nothing in this service can
            # resolve an extract id: ``CrossValidator.validate`` takes ``DocumentExtract``
            # objects and no port maps an id to one. The declaration was written and never
            # served, so nothing had checked it. Narrowed to the documents the checks actually
            # run over, which is the shape ``process_application`` already uses, rather than
            # inventing a store for ids that were never minted.
            input_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "documents": {"type": "array", "items": _DOCUMENT_SCHEMA},
                },
                "required": ["application_id", "documents"],
                "additionalProperties": False,
            },
        ),
    }


class McpToolCatalogAdapter:
    """Declarative MCP 2026-07-28 catalog of B5's three governed tools."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._catalog: dict[str, ToolSpec] = _build_catalog()

    def list_tools(self) -> list[ToolSpec]:
        return list(self._catalog.values())

    def get_tool(self, name: str) -> ToolSpec | None:
        return self._catalog.get(name)

    def as_mcp_tools(self) -> list[Any]:
        """Render the catalog as MCP ``Tool`` objects (MCP 2026-07-28 schema)."""
        from mcp import types as mcp_types  # lazy

        # verify: https://modelcontextprotocol.io/specification/2026-07-28
        return [
            mcp_types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_schema,
            )
            for spec in self._catalog.values()
        ]
