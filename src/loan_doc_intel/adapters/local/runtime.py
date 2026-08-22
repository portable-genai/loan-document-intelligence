"""Local agent-runtime adapter (AgentRuntimePort) : in-process processing loop.

The ``local`` profile's stand-in for **Agent Runtime** (the hosted reasoning engine):
rather than calling a deployed endpoint, it assembles the domain ``LoanDocService`` from
the other local adapters and processes the application in-process. Under ``local`` the
platform client runs the app itself (a laptop runs one app, not the whole platform).
SDK-free and unconditional.

``query`` processes the built-in synthetic application + documents (the seeded extraction
store grounds the deterministic cross-validation), returning a real, cited
``LoanApplicationCase`` offline.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import LoanApplicationCase, Session
from ._seed import SEED_APPLICANT, SEED_DOCUMENTS


class LocalAgentRuntimeAdapter:
    """In-process agent runtime: processes via the local LoanDocService."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service = None

    def _loan_doc_service(self):  # type: ignore[no-untyped-def]
        if self._service is None:
            from ...domain.loan_doc_service import LoanDocService
            from .audit import LocalAppendOnlyAuditAdapter
            from .entitlements import LocalEntitlementsAdapter
            from .extraction import LocalExtractionAdapter
            from .guardrail import LocalHeuristicGuardrailAdapter
            from .llm import LocalDeterministicLLMAdapter
            from .redaction import LocalRegexRedactionAdapter
            from .tracer import LocalNoopTracerAdapter

            self._service = LoanDocService(
                extraction=LocalExtractionAdapter(self._settings),
                llm=LocalDeterministicLLMAdapter(self._settings),
                guardrail=LocalHeuristicGuardrailAdapter(self._settings),
                redaction=LocalRegexRedactionAdapter(self._settings),
                tracer=LocalNoopTracerAdapter(self._settings),
                audit=LocalAppendOnlyAuditAdapter(self._settings),
                entitlements=LocalEntitlementsAdapter(self._settings),
            )
        return self._service

    def query(self, session: Session, message: str) -> LoanApplicationCase:
        # The seeded application is owned by demo-bank; the in-process runtime acts as a
        # demo-bank underwriter so object-level authorization (C2) passes for it.
        from ...domain.identity import Principal

        principal = Principal(
            subject=session.user_id or "local-runtime",
            principals=("group:loan-analyst", "group:underwriting"),
            tenant="demo-bank",
            source="local-runtime",
        )
        return self._loan_doc_service().process(SEED_APPLICANT, list(SEED_DOCUMENTS), principal)

    def health(self) -> bool:
        return True
