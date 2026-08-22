"""FastAPI dependency wiring for the B5 Loan Document Intelligence service.

This module builds a single, process-wide :class:`~loan_doc_intel.config.Container` (the
ports-and-adapters registry) and assembles the orchestration service from the Container's
port instances. The Container is created lazily on first access so importing this module :
and therefore the FastAPI app : never touches Google Cloud: a unit test or the on-prem
profile can import the API with no GCP SDK installed.

Each ``get_*`` factory is a FastAPI ``Depends`` provider. The service takes *explicit port
instances* in its constructor (SPEC §5), so the wiring here is the single place that knows
which ports the service needs.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import Container, Settings, build_container
from ..domain.services import CrossValidator, LoanDocService


@lru_cache(maxsize=1)
def get_container() -> Container:
    """Return the process-wide Container, building it on first use."""
    return build_container(Settings.load())


def get_settings() -> Settings:
    """Convenience accessor for the active settings (region, profile, tolerances...)."""
    return get_container().settings


# --------------------------------------------------------------------------- #
# Service factory : assemble the LoanDocService from the Container's ports.
# Constructor argument order mirrors SPEC §5 exactly.
# --------------------------------------------------------------------------- #
def get_loan_doc_service() -> LoanDocService:
    """LoanDocService(extraction, llm, guardrail, redaction, tracer, audit, entitlements)."""
    return build_loan_doc_service(get_container())


def build_loan_doc_service(container: Container) -> LoanDocService:
    """Assemble a :class:`LoanDocService` from an explicit Container.

    The ``get_*`` factory above uses the cached, process-wide Container (right for the
    long-lived FastAPI app). The CLI and the ADK tools instead build their own Container
    per invocation : honouring ``LOAN_DOC_PROFILE`` at call time : so they call this
    ``build_*`` variant with an explicit Container.
    """
    return LoanDocService(
        extraction=container.extraction,
        llm=container.llm,
        guardrail=container.guardrail,
        redaction=container.redaction,
        tracer=container.tracer,
        audit=container.audit,
        entitlements=container.entitlements,
        validator=build_cross_validator(container.settings),
        review_router=container.review_router,
    )


def build_cross_validator(settings: Settings) -> CrossValidator:
    """Build the pure decision engine from reviewed, replayable policy data."""
    policy = settings.validation
    return CrossValidator(
        amount_tolerance=policy.amount_tolerance,
        balance_decline_warn_ratio=policy.balance_decline_warn_ratio,
        balance_decline_fail_ratio=policy.balance_decline_fail_ratio,
        affordability_warn_ratio=policy.affordability_warn_ratio,
        affordability_fail_ratio=policy.affordability_fail_ratio,
    )


def create_app():
    """App factory used by ``uvicorn ...:create_app --factory`` (CLI ``serve``)."""
    from .app import app

    return app
