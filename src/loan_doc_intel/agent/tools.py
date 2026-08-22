"""ADK FunctionTools that expose the B5 domain capabilities to the agent.

Each tool is a thin, side-effect-honest wrapper: it builds the LoanDocService (or the pure
CrossValidator) from a :class:`~loan_doc_intel.config.Container` (so every port is bound to
the adapter selected by the active profile), invokes it, and returns a JSON-safe dict via
:func:`~loan_doc_intel.domain.serialization.to_jsonable`.

Design notes
------------
* The LoanDocService owns orchestration (redact -> guardrail -> extract -> normalise ->
  deterministic validate -> verdict -> guardrail -> audit; SPEC §5). These tools add **no**
  business logic of their own.
* ``google.adk`` is imported lazily inside :func:`build_function_tools` so this module
  imports cleanly under the on-prem/test profile with no ADK installed (SPEC §4). The plain
  Python tool callables are importable and unit-testable without ADK at all.
* Every callable carries a precise type-hinted signature and a docstring: ADK derives the
  tool's name, description and JSON parameter schema from them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import Container, Settings, build_container

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.adk.tools import FunctionTool

_DEFAULT_ACTOR = "loan-document-intelligence-agent"

# The agent runs as a first-party service identity in the demo tenant. Object-level
# authorization (C2) still applies: the LoanDocService resolves each object's owner
# SERVER-SIDE and only proceeds for objects this tenant owns.
_DEFAULT_TENANT = "demo-bank"
_DEFAULT_PRINCIPALS = ("group:loan-analyst", "group:underwriting")


def _container(settings: Settings | None) -> Container:
    return build_container(settings)


def _principal(actor: str) -> Any:
    """Build a verified-equivalent Principal for the agent's service identity."""
    from ..domain.identity import Principal

    return Principal(
        subject=actor or _DEFAULT_ACTOR,
        principals=_DEFAULT_PRINCIPALS,
        tenant=_DEFAULT_TENANT,
        source="agent",
    )


def _documents_from(raw: list[dict[str, Any]]) -> list[Any]:
    from ..domain.models import ApplicantDocument, DocType

    out = []
    for d in raw or []:
        out.append(
            ApplicantDocument(
                id=str(d.get("id", "")),
                doc_type=DocType(str(d.get("doc_type", "payslip"))),
                uri=str(d.get("uri", "")),
            )
        )
    return out


def process_application(
    application_id: str,
    documents: list[dict[str, Any]],
    applicant_name: str = "",
    actor: str = _DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Process a loan application's documents into a cited income verification.

    Extracts income/bank data and deterministically cross-validates it. The result is a
    ``LoanApplicationCase`` flagged for human review (the underwriter decides).

    Args:
      application_id: The application identifier.
      documents: The applicant's documents, each {id, doc_type, uri}.
      applicant_name: The applicant's name (redacted at the boundary before any model call).
      actor: Authenticated underwriter / service identity for the audit trail.

    Returns:
      A JSON-safe ``LoanApplicationCase`` dict.
    """
    from ..domain.models import Applicant
    from ..domain.serialization import to_jsonable
    from ..domain.services import LoanDocService

    c = _container(settings)
    service = LoanDocService(
        extraction=c.extraction,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        tracer=c.tracer,
        audit=c.audit,
        entitlements=c.entitlements,
        review_router=c.review_router,
    )
    applicant = Applicant(id=application_id, name=applicant_name)
    case = service.process(applicant, _documents_from(documents), _principal(actor))
    return to_jsonable(case)


def extract_document(
    document: dict[str, Any],
    actor: str = _DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Extract structured fields and line items from a single applicant document.

    Args:
      document: The document to extract, {id, doc_type, uri}.
      actor: Authenticated underwriter / service identity for the audit trail.

    Returns:
      A JSON-safe ``DocumentExtract`` dict.
    """
    from ..domain.serialization import to_jsonable
    from ..domain.services import LoanDocService

    c = _container(settings)
    service = LoanDocService(
        extraction=c.extraction,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        tracer=c.tracer,
        audit=c.audit,
        entitlements=c.entitlements,
        review_router=c.review_router,
    )
    doc = _documents_from([document])[0]
    extract = service.extract_only(doc, b"", "application/pdf", _principal(actor))
    return to_jsonable(extract)


def cross_validate(
    application_id: str,
    applicant_name: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Describe the deterministic cross-validation checks B5 runs for an application.

    This tool surfaces the governed check catalog so a peer agent understands what
    consistency rules B5 applies; the full run is performed by process_application.

    Args:
      application_id: The application identifier.
      applicant_name: The applicant's name (for context only).

    Returns:
      A JSON-safe dict describing the deterministic check kinds.
    """
    from ..domain.models import CheckKind

    return {
        "application_id": application_id,
        "deterministic_checks": [k.value for k in CheckKind],
        "note": "Every check verdict is deterministic; the model never overrides it.",
    }


TOOL_FUNCTIONS = (
    process_application,
    extract_document,
    cross_validate,
)


def build_function_tools() -> list[FunctionTool]:
    """Wrap each capability callable as an ADK ``FunctionTool``.

    ADK introspects each function's signature and docstring to derive the tool name,
    description and parameter JSON schema. ``google.adk`` is imported here (lazily) so the
    module is import-safe without ADK installed (SPEC §4).
    """
    from google.adk.tools import FunctionTool

    return [FunctionTool(func=fn) for fn in TOOL_FUNCTIONS]
