# Security FAQ

For an application-security team reviewing this repo (Doc5, Loan / Mortgage Document
Intelligence) before adopting it as a base. Answers reflect the current code.
Cross-references: [`ARCHITECTURE.md`](../../ARCHITECTURE.md), [`COMPLIANCE.md`](../../COMPLIANCE.md),
[`docs/embedding-and-identity.md`](../embedding-and-identity.md), and the per-check evidence
in [`docs/practices-audit.md`](../practices-audit.md).

### How is a request authenticated? Can a client spoof its identity?

No. Identity is resolved **server-side** from the transport context by an `IdentityPort`
adapter (`api/security.py` -> `domain/identity.py`), never from the request body. The
request schemas carry no `actor` field (`api/schemas.py`), and any client-asserted actor or
ACL is discarded. The audit actor and the entitlement principal both come from the verified
`Principal`. Per profile: `local` = seeded dev personas via the `X-Dev-Persona` header (no
IdP, offline only), `gcp` / `platform` = the Cloud IAP-injected assertion, `onprem` = an
enterprise-IdP placeholder. An unverifiable identity is a 401. This repo owns **no login
flow** of its own (no OIDC adapter, no session cookie), so there is no PKCE / JWKS / nonce
surface to review here; that responsibility is delegated to IAP (audit check C8 is N-A).

### How is object-level authorization (multi-tenant isolation) enforced?

An `EntitlementsPort` (`ports/entitlements.py`) resolves an object's owner (owning tenant +
permitted roles) from a **server-side** store keyed by object id, never from the request
body. `domain/entitlements.py::require_object_access` runs as **step 0** of
`LoanDocService.process` / `extract_only`, before any redaction, extraction or audit, and is
fail-closed: an unknown object, an empty tenant, a cross-tenant principal, and a
right-tenant-wrong-role principal all deny. At the route layer a cross-tenant persona gets a
**403 with no side effects**. Proven in `tests/unit/test_entitlements.py` and
`tests/unit/test_api_identity.py`; each was RED before the fix (audit check C2).

### What about the service-to-service calls in the `platform` profile?

The platform adapters source `hex_service_kit.s2s` via `adapters/platform/_s2s.py`. All six
delegates (`remote_audit`, `remote_guardrail`, `remote_entitlements`, `remote_redaction`,
`remote_registry`, `remote_evaluation`) validate their base URL at construction (https-only
outside loopback, rejected otherwise), attach an S2S bearer credential, and propagate the
verified end-user actor as a **signed header pair** (`X-Ld-Actor` / `X-Ld-Actor-Sig`) rather
than a trust-me JSON field. The receiving platform services own verification (audit check
C7).

### Is the demo/dev server safe? Does anything bind 0.0.0.0 by default?

No. There are two bounds, and the load-bearing one rides the **app object** rather than an
entry point.

`main()` binds **loopback (127.0.0.1)** via `hex_service_kit.resolve_bind_host`, and the
Makefile defaults `API_HOST` to `127.0.0.1`. On its own that is a property of one entry
point, not of the application: the Dockerfile `CMD` is
`uvicorn loan_doc_intel.api.app:app --host 0.0.0.0 --port ${PORT}`, and a `uvicorn ... --host
0.0.0.0` typed by hand behaves the same way, so neither ever reaches that call. The real
bound is `add_loopback_exposure_guard`, registered on the app object as the outermost
middleware, so it holds however the service is started: a non-loopback peer is refused with a
503 before CORS, before the header baseline and before any route or dependency runs.

**What switches it off is the identity BINDING, and nothing else.** The guard asks the
adapter bound to the identity port whether it verifies the end user (see
`src/loan_doc_intel/ports/identity.py`). The seeded persona adapter reads `X-Dev-Persona`, a
header the caller writes, so it declares `client-asserted` and the guard stays on; the
on-premises placeholder resolves nobody, so it declares `unimplemented` and the guard stays
on; only the IAP adapter, which verifies a signed assertion, declares `verified` and stands
the guard down. A run that named NO profile is bounded too, and additionally refuses the
seeded personas outright, so a lost environment variable cannot publish an unauthenticated
API.

A service-to-service credential is deliberately **not** part of that decision. It
authenticates a calling service and no end user, so setting one changes nothing about the
end-user routes. A guard derived from it would switch off for exactly the routes it was
protecting.

`LOAN_DOC_ALLOW_INSECURE_DEMO=1` remains the single documented opt-out. CORS is an explicit
allowlist (`hex_service_kit.cors_allowlist` from `LOAN_DOC_CORS_ORIGINS`, never `*`); the
localhost dev-origin fallback and the `X-Dev-Persona` header are **local-profile-only**.
Proven by `tests/unit/test_serving_path_exposure.py` and `tests/unit/test_netdefaults.py`
(audit check C5).

### What HTTP security headers are set?

Honest answer: **partial today.** Both the API middleware (`api/app.py`) and the UI
(`ui/next.config.mjs`) emit CSP `frame-ancestors` + `X-Frame-Options`, but neither yet sets
`X-Content-Type-Options: nosniff` or `Referrer-Policy`, there is no HSTS on the secure API,
and the UI has no full `default-src 'self'` / scoped `connect-src` CSP. This is tracked as
audit check **C6 (PARTIAL)** in [`docs/practices-audit.md`](../practices-audit.md); it is
not a load-bearing check but a reviewer should know it before deploying. Edge ingress (IAP /
LB) is expected to add the transport-level headers in a managed deployment.

### Is customer PII redacted, and where?

Redact-before-everything: redaction is **step 1** of `LoanDocService._process_inner` (before
guardrail, model and audit), extracted free-text fields (name / address / employer) are
re-redacted after extraction, and the `AuditEvent` stores only `redacted_prompt` /
`redacted_response` (audit check C3). National-identifier detection is jurisdiction-driven
(`domain/pii_patterns.py`, one SG / HK / JP / AU pack selected by `pii.jurisdictions` /
`LOAN_DOC_PII_JURISDICTIONS`), so a fork scrubs and gates on its own identifiers. The
runtime guardrail / DLP gateway itself is the sibling **Hrz1** service; this repo consumes
it rather than re-implementing it.

### How tamper-evident is the audit trail? What are its limits?

The `local` audit store wraps the shared `hex_service_kit.audit.HashChainedAuditLog`: a
SHA-256 hash chain over canonical JSON, SQLite `UPDATE` / `DELETE` triggers enforcing
append-only, JSONL export / restore, a `verify_chain()` check, and an **honest-limits
docstring** stating exactly which tamper classes the chain alone cannot catch (a full rewrite
carries no secret). In production the `gcp` profile writes to a locked WORM Cloud Logging
bucket, which provides non-rewritability itself. This repo does **not** replace the platform
audit system (Hrz5); the local chain is the offline stand-in (audit check C9).

### Supply chain: are dependencies pinned and scanned?

Yes. Committed lockfiles (`requirements-dev.lock`, `requirements-gcp.lock`) are installed in
CI and the Docker build; the base image is digest-pinned; GitHub Actions are SHA-pinned;
`.github/dependabot.yml` proposes bumps; and a CI `supply-chain` job runs `pip-audit` over
both lockfiles as a **hard gate** (reporting no known vulnerabilities). `ruff` is exact-pinned
(`ruff==0.15.18`). `npm audit --audit-level=high` gates the UI (audit checks D1, D2).

### Where are secrets? Are any committed?

No secret values are in the repo. `config/settings.yaml` stores only the **names** of env
vars holding secrets and KMS resource paths; values are read at construction and never
logged. A literal-secret grep over `config/` is clean (audit check C10). The bundled fixtures
and golden set are obviously fictional.

### What is explicitly out of scope / a residual risk?

- Transport security headers are partial (C6, above).
- The deterministic validation thresholds are not yet fully threaded from config into the
  engine (audit check B4); they are module-level constants a fork must edit in code today.
- The hash chain needs the WORM bucket (or an external head anchor) to resist truncation;
  the local chain states its own limits.
- This is a reference build: run your own pen-test, threat model, and model-risk review
  before any live-data deployment (stated throughout the docs and in
  [`docs/ADOPTING.md`](../ADOPTING.md)).
