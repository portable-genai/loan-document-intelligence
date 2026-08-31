"""FastAPI application for the B5 Loan / Mortgage Document Intelligence service.

Exposes the three artifact endpoints (process an application, extract a document,
cross-validate extracts) plus health, and publishes the A2A AgentCard at
``/.well-known/agent-card.json``. The React/Next.js UI and the CLI consume this surface.

Design constraints:

* **Import-safe.** Building the :class:`~loan_doc_intel.config.Container` is deferred to
  request time via the ``deps`` factories, so importing this module (or ``app``) never
  touches Google Cloud. The on-prem/test profile imports it with no GCP SDK installed.
* **Guardrail blocks are not errors.** A :class:`GuardrailBlockedError` from the service is
  translated to a 200 carrying a *blocked* case flagged for human review, never a 500.
* **Region pinned** to ``asia-southeast1`` (Singapore) for applicant-PII residency.

Run locally with ``python -m loan_doc_intel.api.app`` (uvicorn on :8092).
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from hex_service_kit import (
    ConfiguredEmptyError,
    cors_allowlist,
    read_env_setting,
    resolve_bind_host,
)
from hex_service_kit.web import add_loopback_exposure_guard

from ..config import end_user_auth_kind
from ..domain.entitlements import require_object_access
from ..domain.errors import AccessDenied, GuardrailBlockedError
from ..domain.identity import Principal
from ..domain.services import LoanDocService
from ..ports.identity import VERIFIED
from . import deps
from .schemas import (
    AgentCardModel,
    CrossValidationResponse,
    ExtractModel,
    ExtractRequest,
    HealthResponse,
    LoanApplicationCaseResponse,
    ProcessRequest,
    ValidateRequest,
)
from .security import CurrentPrincipal

_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Embedding-surface controls. In secure/embedded mode the service is served same-origin via
# the parent app's reverse-proxy (no CORS needed); for the cross-origin / standalone dev
# case, LOAN_DOC_CORS_ORIGINS is an explicit per-tenant allowlist (never "*").
# LOAN_DOC_FRAME_ANCESTORS is the CSP frame-ancestors allowlist of parent origins permitted
# to iframe the UI.
_CORS_ORIGINS_ENV = "LOAN_DOC_CORS_ORIGINS"
_FRAME_ANCESTORS_ENV = "LOAN_DOC_FRAME_ANCESTORS"
_DEFAULT_FRAME_ANCESTORS = "'self'"


#: Entries that are a wildcard by BEHAVIOUR rather than by spelling, so the asterisk test below
#: cannot see them. ``null`` is the one that matters: a sandboxed iframe presents a null origin,
#: so ``frame-ancestors null`` admits framing from a document whose own origin the browser has
#: already decided not to trust, and a null CORS origin trusts the same document WITH
#: credentials. ``'*'`` is the quoted form CSP also honours and ``*.*`` is the subdomain
#: wildcard; both carry an asterisk, and both are named here so the set reads as the complete
#: refusal rather than as a list of leftovers. Matching is exact, so ``https://nullify.example``
#: remains a perfectly good origin. The same four are refused in ``ui/lib/csp.mjs``.
_WILDCARD_TOKENS = frozenset({"*", "'*'", "null", "*.*"})


def _refuse_wildcard(origins: list[str] | tuple[str, ...], setting: str) -> None:
    """An origin policy naming everybody is not an allowlist, so refuse to boot with one.

    "never ``*``" was written in the comment above and enforced nowhere, which is the same
    as unenforced: the shared ``cors_allowlist`` docstring promises it never returns ``*``
    while its set-and-valid branch returns exactly what the operator wrote. ``*`` in the CORS
    allowlist trusts every origin WITH credentials, and in frame-ancestors it lets any page
    on the internet frame the UI and drive it as the signed-in user. The rule catches a
    wildcard hiding inside an origin too (``https://*.example``): a legitimate origin has no
    ``*`` anywhere in it, so this refuses no configuration a deployment could correctly hold.

    Raised from the import-time resolvers below, so it is a BOOT refusal in the same way the
    emptied state already is: the process never comes up serving a policy nobody chose.

    The asterisk test alone was not the whole rule. ``null`` carries no asterisk, so it passed
    both allowlists and reached ``CORSMiddleware`` and the CSP directive verbatim: see
    :data:`_WILDCARD_TOKENS`. The two halves are a UNION, and the union is what
    ``ui/lib/csp.mjs`` already enforced for the document a browser actually frames, so until
    now the two surfaces disagreed about what an origin policy may hold.
    """
    offending = [origin for origin in origins if "*" in origin or origin in _WILDCARD_TOKENS]
    if offending:
        raise ValueError(
            f"{setting} origin policy must never contain a wildcard, got {offending}. "
            "Name each permitted origin in full."
        )


def _frame_ancestors() -> str:
    """Resolve the CSP ``frame-ancestors`` allowlist in THREE states, never two.

    ``os.environ.get(name, "").strip() or _DEFAULT`` distinguishes only two outcomes,
    because the ``or`` collapses "absent" and "present but empty" into the same branch. The
    variable an operator deliberately emptied (a Terraform variable that renders to nothing,
    a Cloud Run env var declared with no value, a ``.env`` line left as ``VAR=``) then
    inherited the unset default and the service answered ``frame-ancestors 'self'`` plus
    ``X-Frame-Options: SAMEORIGIN``, INDISTINGUISHABLE from never having configured it. An
    operator who empties the allowlist to name no parent has expressed an intent, and
    silently granting same-origin framing instead is reading an absence as consent.

    * unset: no intent was expressed, so the documented restrictive default stands.
    * set and empty: an intent WAS expressed and it names nothing. Refused, not silently
      widened. This resolver runs at import, so the refusal is a BOOT refusal: the process
      never comes up serving a framing policy nobody chose.
    * set with a value: used as given, stripped.
    """
    setting = read_env_setting(_FRAME_ANCESTORS_ENV)
    if setting.is_configured_empty:
        raise ConfiguredEmptyError(
            f"{_FRAME_ANCESTORS_ENV} is set but empty. An empty allowlist names no parent "
            "origin, and inheriting the default would silently permit same-origin framing "
            f"nobody asked for. Unset {_FRAME_ANCESTORS_ENV} to keep the "
            f"{_DEFAULT_FRAME_ANCESTORS} default, name the parent origins that may frame the "
            "UI, or set it to 'none' to refuse framing outright."
        )
    _refuse_wildcard(setting.value.split(), _FRAME_ANCESTORS_ENV)
    return setting.value or _DEFAULT_FRAME_ANCESTORS


_FRAME_ANCESTORS = _frame_ancestors()

# The two frame-ancestors policies the pre-CSP header can also express.
_LEGACY_FRAME_OPTIONS = {"'self'": "SAMEORIGIN", "'none'": "DENY"}


def _cors_origins() -> list[str]:
    """Explicit allowlist, never "*"; the localhost dev fallback applies ONLY under a
    DELIBERATELY chosen local profile (shared hex-service-kit rule).

    Keyed off ``exposure_profile`` rather than the raw profile: granting cross-origin
    credentialed access to localhost is a relaxation, so a run that never named a profile
    must not look like ``local`` here and gets an empty allowlist instead.

    The CONFIGURED value is judged by :func:`_refuse_wildcard` before the kit is called, and
    that ordering is the point rather than an accident. ``cors_allowlist`` now refuses a
    wildcard itself, raising ``InsecureCorsError``, so whichever of the two runs first is the
    one that decides which message an operator reads. This repo owns the rule: it names the
    variable, and its union covers the behavioural tokens as well as the asterisk. Running it
    first keeps it the single authority and leaves the kit an unreachable backstop on the
    configured path. The trailing call still guards the RESOLVED list, which under the unset
    default is a value the operator never wrote.
    """
    setting = read_env_setting(_CORS_ORIGINS_ENV)
    if setting.has_value:
        _refuse_wildcard(
            [origin.strip() for origin in setting.value.split(",") if origin.strip()],
            _CORS_ORIGINS_ENV,
        )
    resolved = cors_allowlist(
        deps.get_settings().profile_choice.exposure_profile,
        origins_env=_CORS_ORIGINS_ENV,
        dev_origins=tuple(_DEV_ORIGINS),
    )
    _refuse_wildcard(resolved, _CORS_ORIGINS_ENV)
    return resolved


app = FastAPI(
    title="B5 Loan / Mortgage Document Intelligence",
    version="0.1.0",
    description=(
        "Document AI extraction + deterministic cross-validation of applicant income and "
        "bank-statement data for retail-lending underwriting, on the Gemini Enterprise "
        "Agent Platform. Decision-support, not a lending decision."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Dev-Persona"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next: Any) -> Any:
    """Emit embedding-surface headers: CSP frame-ancestors (who may iframe the UI).

    ``_FRAME_ANCESTORS`` is guaranteed non-empty by :func:`_frame_ancestors`, so the directive
    emitted here always carries a value a browser will honour. ``X-Frame-Options`` is the
    pre-CSP equivalent, so it accompanies the two policies it can express: ``'self'`` maps to
    ``SAMEORIGIN`` and ``'none'`` to ``DENY``. A named allowlist has no ``X-Frame-Options``
    spelling, so none is sent there rather than one that contradicts the CSP.
    """
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = f"frame-ancestors {_FRAME_ANCESTORS}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if deps.get_settings().profile in {"gcp", "platform"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    legacy = _LEGACY_FRAME_OPTIONS.get(_FRAME_ANCESTORS)
    if legacy is not None:
        response.headers["X-Frame-Options"] = legacy
    return response


# A request arrives with nothing authenticating the END USER unless BOTH of these hold, and
# the guard bounds every case where either fails:
#
#   1. a profile was chosen. Absent that, nobody selected an identity scheme, the seeded
#      persona adapter refuses to construct, and every end-user route answers 401; but
#      /healthz and the agent card would still answer a stranger, and a deployment in that
#      state has no business being reachable at all. It is also the one case where a settings
#      file that bound a verifying adapter must NOT buy the relaxation: unset is not consent,
#      whatever the binding says;
#   2. the identity adapter the active binding names DECLARES that it verifies the end user.
#      Seeded personas arrive on the X-Dev-Persona header the caller wrote (client-asserted)
#      and the on-premises placeholder resolves nobody at all (unimplemented); neither
#      authenticates anyone, so neither may switch this off. Reading the BINDING rather than
#      the profile string is also what keeps a rebound adapter honest: an on-premises
#      deployment that swaps in its own verifying IdP is answered about that adapter.
#
# Note what is NOT in this expression: any service-to-service credential. A service credential
# is evidence about a calling SERVICE and says nothing about the end-user routes, so setting
# one must not, and cannot, disable their bound.
_END_USER_AUTHENTICATED = deps.get_settings().profile_explicit and end_user_auth_kind() == VERIFIED

# The RESTRICTION's profile string. `bind_profile` already reads an unconsented run as
# `local`; this widens the same rule to every posture that cannot authenticate an end user, so
# the start-up bound in `main()` and the request-time guard agree instead of one binding every
# interface while the other refuses every caller on it.
_BIND_PROFILE = (
    deps.get_settings().profile_choice.bind_profile if _END_USER_AUTHENTICATED else "local"
)

# Registered LAST, so it is the OUTERMOST middleware: an off-loopback caller is refused before
# CORS, before the header baseline and before any route or dependency runs. Bound to the APP
# OBJECT, not to `main()`: the Dockerfile CMD is
# `uvicorn loan_doc_intel.api.app:app --host 0.0.0.0 --port ${PORT}`, so a guard reachable
# only from `main()` never runs in a shipped process and the seeded personas would be served
# to the LAN.
add_loopback_exposure_guard(
    app,
    unauthenticated=not _END_USER_AUTHENTICATED,
    insecure_demo_env="LOAN_DOC_ALLOW_INSECURE_DEMO",
    # The EXPOSURE profile, so a run nobody configured names itself 'unconfigured' in the
    # refusal rather than borrowing the name of a profile an operator never chose.
    posture=deps.get_settings().profile_choice.exposure_profile,
)


# --------------------------------------------------------------------------- #
# Object-level authorization (C2): map a fail-closed domain denial to HTTP 403.
# --------------------------------------------------------------------------- #
def _forbidden(exc: AccessDenied) -> HTTPException:
    """A 403 for a failed SERVER-SIDE object-entitlement check (never a data leak)."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _authorize_object(principal: Principal, object_id: str) -> None:
    """Authorize ``principal`` for ``object_id`` via the SERVER-SIDE EntitlementsPort.

    Used by routes that do not go through :class:`LoanDocService` (e.g. ``/v1/validate``);
    the process/extract routes authorize inside the service. Raises ``AccessDenied``.
    """
    owner = deps.get_container().entitlements.owner(object_id)
    require_object_access(principal, object_id, owner)


# --------------------------------------------------------------------------- #
# Artifact endpoints
# --------------------------------------------------------------------------- #
@app.post("/v1/process", response_model=LoanApplicationCaseResponse, tags=["artifacts"])
def process(
    request: ProcessRequest,
    principal: CurrentPrincipal,
    service: Annotated[LoanDocService, Depends(deps.get_loan_doc_service)],
) -> LoanApplicationCaseResponse:
    """Process an application's documents into a cited income verification.

    Object-level authorization is enforced inside the service, fail-closed: a caller not
    entitled to this application (resolved SERVER-SIDE, never from the body) gets a 403 and
    nothing is processed or audited. The pipeline otherwise degrades gracefully on a
    guardrail block, returning a 200 case flagged for human review rather than a 500.
    """
    applicant = request.application.to_domain()
    documents = [d.to_domain() for d in request.documents]
    try:
        case = service.process(applicant, documents, principal)
    except AccessDenied as exc:
        raise _forbidden(exc) from exc
    except GuardrailBlockedError:
        from ..domain.models import (
            Applicant,
            IncomeFigure,
            IncomeVerificationSummary,
            LoanApplicationCase,
        )

        blocked_applicant: Applicant = applicant
        case = LoanApplicationCase(
            id=request.application.id,
            applicant=blocked_applicant,
            income=IncomeVerificationSummary(
                application_id=request.application.id,
                verified_income=IncomeFigure(source_doc_id="blocked", amount=0.0),
                red_flags=("Request blocked by the safety guardrail.",),
            ),
        )
    return LoanApplicationCaseResponse.from_domain(case)


@app.post("/v1/extract", response_model=ExtractModel, tags=["artifacts"])
def extract(
    request: ExtractRequest,
    principal: CurrentPrincipal,
    service: Annotated[LoanDocService, Depends(deps.get_loan_doc_service)],
) -> ExtractModel:
    """Extract structured fields + line items from a single applicant document.

    Object-level authorization is enforced inside the service (the document is the object):
    a caller not entitled to it gets a 403 and nothing is extracted or audited.
    """
    document = request.document.to_domain()
    try:
        result = service.extract_only(document, b"", "application/pdf", principal)
    except AccessDenied as exc:
        raise _forbidden(exc) from exc
    return ExtractModel.from_domain(result)


@app.post("/v1/validate", response_model=CrossValidationResponse, tags=["artifacts"])
def validate(request: ValidateRequest, principal: CurrentPrincipal) -> CrossValidationResponse:
    """Run the deterministic cross-validation over the supplied extracts.

    This is the pure-domain heart : no model, no adapters : but the application it names is
    still an owned object, so object-level authorization is enforced first (fail-closed, the
    owner resolved SERVER-SIDE): a caller not entitled to the application gets a 403. The
    request also resolves a verified :class:`Principal` (a 401 on an unknown identity),
    keeping identity + entitlement enforcement uniform across every artifact route.
    """
    from ..domain.models import (
        Citation,
        DocType,
        DocumentExtract,
        LineItem,
        SourceType,
    )

    try:
        _authorize_object(principal, request.application_id)
    except AccessDenied as exc:
        raise _forbidden(exc) from exc
    applicant = request.applicant.to_domain()
    extracts = [
        DocumentExtract(
            document_id=e.document_id,
            doc_type=DocType(e.doc_type),
            fields=dict(e.fields),
            line_items=tuple(
                LineItem(label=li.label, amount=li.amount, currency=li.currency, date=li.date)
                for li in e.line_items
            ),
            period=e.period,
            confidence=e.confidence,
            citations=tuple(
                Citation(
                    source_id=c.source_id,
                    source_type=SourceType(c.source_type),
                    title=c.title,
                    page=c.page,
                    field=c.field,
                    snippet=c.snippet,
                )
                for c in e.citations
            ),
        )
        for e in request.extracts
    ]
    result = deps.build_cross_validator(deps.get_settings()).validate(
        extracts, applicant, application_id=request.application_id
    )
    return CrossValidationResponse.from_domain(result)


# --------------------------------------------------------------------------- #
# Health & governance
# --------------------------------------------------------------------------- #
@app.get("/healthz", response_model=HealthResponse, tags=["ops"])
def healthz() -> HealthResponse:
    """Liveness/readiness probe. Reports the active profile and pinned region."""
    settings = deps.get_settings()
    return HealthResponse(
        status="ok",
        profile=settings.profile,
        region=settings.region,
        runtime=settings.runtime,
        generator_model=settings.generator_model,
    )


@app.get("/v1/personas", tags=["ops"])
def personas() -> list[dict[str, str]]:
    """List seeded dev personas for the local persona picker (empty outside local profile).

    Local mode runs with no IdP; the UI uses this to let a demo/test pick an identity
    (and thus exercise per-user authorization) via the ``X-Dev-Persona`` header. Secure
    profiles resolve identity from the IAP assertion, so this returns an empty list.
    """
    identity = deps.get_container().identity
    lister = getattr(identity, "personas", None)
    if lister is None:
        return []
    return [dict(p) for p in lister()]


@app.get("/.well-known/agent-card.json", response_model=AgentCardModel, tags=["governance"])
def agent_card() -> AgentCardModel:
    """Publish this service's A2A AgentCard for discovery (A3 Registry / interop)."""
    from ..agent.agent_card import build_agent_card

    settings = deps.get_settings()
    card = build_agent_card(settings)
    return AgentCardModel.from_domain(card)


def main() -> None:
    """Run the API locally with uvicorn (Cloud Run / Agent Runtime use this app object)."""
    import uvicorn

    uvicorn.run(
        "loan_doc_intel.api.app:app",
        # Fail-closed bind (shared hex-service-kit rule): the no-auth local
        # profile binds loopback unless LOAN_DOC_ALLOW_INSECURE_DEMO=1; secure profiles keep
        # 0.0.0.0 (container-local; ingress is fronted by the platform). Keyed off
        # ``_BIND_PROFILE``, which fails closed in the OPPOSITE direction to the CORS
        # relaxation above: here ``local`` is the restrictive case, so an unconsented run, and
        # any run whose identity binding cannot verify an end user, must look like ``local``
        # and stay on loopback. That is the same value the request-time guard was built with,
        # so the two cannot disagree.
        host=resolve_bind_host(
            _BIND_PROFILE,
            host_env="LOAN_DOC_API_HOST",
            insecure_demo_env="LOAN_DOC_ALLOW_INSECURE_DEMO",
        ),
        # PORT is exempted from the three-state rule (see
        # tests/unit/test_three_state_env_reads.py): it names a listen port, not a posture.
        port=int(os.environ.get("PORT", "8092")),
        # The reload flag collapses UNSET and SET-AND-EMPTY deliberately, onto the closed
        # direction: no auto-reloader. Only a non-empty value turns the development
        # reloader on, so an emptied variable can never enable it in a deployment.
        reload=bool(read_env_setting("LOAN_DOC_API_RELOAD").value),
    )


if __name__ == "__main__":
    main()
