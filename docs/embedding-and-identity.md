# Embedding and identity: client integration guide (B5 Loan / Mortgage Document Intelligence)

This guide shows how an enterprise client runs the B5 Loan Document Intelligence service and,
when desired, embeds its UI inside an existing web application with secure single sign-on (SSO)
so users never see a second login. It is grounded in what the codebase implements today, and it
names the further hardening layers that a wider multi-host rollout would add (Section 8).

The B5 service ships as two cooperating pieces:

- **Backend**: a FastAPI service (default port `8092`) exposing the artifact endpoints
  (`/v1/process`, `/v1/extract`, `/v1/validate`), health (`/healthz`), the seeded-persona list
  (`/v1/personas`), and the A2A agent card (`/.well-known/agent-card.json`).
- **UI**: a Next.js console (default port `3000`) that calls the backend and renders the cited
  income verification. `NEXT_PUBLIC_EMBED=1` drops the UI's own chrome
  (`ui/app/layout.tsx`); the UI base path and API base are build-time env vars
  (`ui/next.config.mjs`, `ui/lib/api.ts`).

The one invariant across every shape: **the server never trusts a client-asserted actor.** The
request body carries no `actor` field. The audit actor is the server-verified `Principal`
(`src/loan_doc_intel/domain/identity.py`) resolved by the active `IdentityPort` adapter, and a
request whose identity cannot be verified is a hard `401`.

---

## 1. The three deployment shapes

Pick the cheapest shape the host can actually satisfy.

| # | Shape | Use when the host... | Host work | Identity |
|---|-------|----------------------|-----------|----------|
| 1 | **Embedded, same-origin reverse proxy** | controls its own edge (nginx / Next.js rewrites) and can federate its IdP into Cloud IAP. | Two proxy routes (`/agent/*`, `/agent/api/*`) plus one `<iframe src="/agent/">`. | IAP-verified `x-goog-iap-jwt-assertion` (`adapters/gcp/iap_identity.py`); the proxy forwards the header. |
| 2 | **Standalone behind Cloud IAP** | has no host app, or wants a separate console at its own URL. | DNS + HTTPS LB + IAP. | IAP-verified assertion; IAP + Workforce Identity Federation gives SSO. |
| 3 | **Local dev, no auth** | is evaluating offline, with no IdP. | None. | Seeded personas via `X-Dev-Persona` (`adapters/local/identity.py`). |

Because the iframe in shape 1 is first-party (same origin as the host), there are no
third-party-cookie issues and no CORS to configure.

---

## 2. Shape 3: run locally, no auth

Local mode (`LOAN_DOC_PROFILE=local`) runs the entire pipeline offline: a local document
parser, a deterministic LLM, regex PII redaction, and an append-only SQLite audit store, with
**no IdP, AD, or LDAP**. Identity is resolved from a small set of seeded dev personas
(`adapters/local/identity.py`) selected by an `X-Dev-Persona` request header, defaulting to the
first persona.

```bash
# Backend (repo root)
export LOAN_DOC_PROFILE=local
make run-api                      # uvicorn on http://localhost:8092

# UI (in ./ui)
cp .env.local.example .env.local  # NEXT_PUBLIC_API_BASE defaults to http://localhost:8092
npm install && npm run dev        # http://localhost:3000
```

The UI fetches `GET /v1/personas` and sends the chosen id as `X-Dev-Persona`. The seeded
personas deliberately span different entitlements and tenants (including a cross-tenant one) so
per-user and per-tenant authorization is demoable offline:

| Persona id | Subject | Tenant | Entitlement principals |
|------------|---------|--------|------------------------|
| `analyst` | `demo.analyst@bank.example` | `demo-bank` | `group:loan-analyst`, `group:underwriting` |
| `approver` | `demo.approver@bank.example` | `demo-bank` | `group:loan-analyst`, `group:underwriting`, `group:loan-approver` |
| `auditor` | `demo.auditor@bank.example` | `demo-bank` | `group:audit` |
| `other-tenant` | `user@other-tenant.example` | `other-bank` | `group:loan-analyst` |

```bash
curl -s http://localhost:8092/v1/personas | jq .
curl -s -X POST http://localhost:8092/v1/process \
  -H 'Content-Type: application/json' -H 'X-Dev-Persona: approver' \
  -d @examples/application.json | jq .
```

In secure profiles `X-Dev-Persona` is ignored entirely (Section 4), so leaving persona-selection
code in the UI is harmless in production, and `/v1/personas` returns an empty list outside
`local`. The persona picker in `ui/app/page.tsx` renders only when `health().profile === "local"`.

---

## 3. Shape 2: standalone behind Cloud IAP

When there is no host application, deploy the service on its own URL:

1. Deploy backend and UI behind the same HTTPS load balancer and Cloud IAP.
2. Set `LOAN_DOC_PROFILE=gcp` and `LOAN_DOC_IAP_AUDIENCE` so the backend verifies the IAP
   assertion. IAP authentication is configured **on the GCP service** (the load balancer), not
   hand-rolled in the app, and the backend still independently re-verifies the signed assertion
   (`adapters/gcp/iap_identity.py`): the defense that survives an edge bypass or a forged
   unsigned header.
3. Point the UI at the backend with `NEXT_PUBLIC_API_BASE`. If UI and backend are on
   **different** origins, also set `LOAN_DOC_CORS_ORIGINS` to the UI origin (explicit allowlist,
   never `"*"`):

   ```bash
   export LOAN_DOC_CORS_ORIGINS="https://loan-agent.client.example"
   export NEXT_PUBLIC_API_BASE="https://api.loan-agent.client.example"
   ```

4. Share the URL with authorized users. IAP plus Workforce Identity Federation gives silent SSO
   from the corporate IdP while the corporate session is live.

Leave `LOAN_DOC_FRAME_ANCESTORS` UNSET so its `'self'` default stands: nothing should iframe a
standalone deployment. Do not set it to an empty string to mean that. The backend refuses to
boot on a set-but-empty value rather than inheriting the default, because an allowlist naming
nobody is an expressed intent and quietly granting same-origin framing instead would be
indistinguishable from never having configured it. To refuse framing outright, set it to
`'none'`.

---

## 4. The identity contract

The single invariant, implemented today and preserved across every shape: **the server never
trusts a client-asserted actor.** `get_principal` (`api/security.py`) builds a `RequestContext`
from the inbound headers only, asks the active `IdentityPort` adapter to resolve a verified
`Principal`, and a failure is a hard `401`. Every artifact route receives `principal.actor` as
the audit actor; the request body has no `actor` to spoof. There is no path by which a caller can
assert who they are.

The `Principal` (`domain/identity.py`) models everything enforcement needs: `subject` (the audit
actor), `principals` (entitlement groups/ACL), `tenant` (multi-tenant partition), `assurance`
(auth-strength hint), and `source` (which adapter resolved it).

### Identity options by profile

| Profile | Adapter | What it does |
|---------|---------|--------------|
| `local` | `adapters/local/identity.py` | Offline dev/test identity via `X-Dev-Persona`, no IdP. Default persona when the header is absent; an unknown id is a `401`. |
| `gcp` / `platform` | `adapters/gcp/iap_identity.py` | Verifies the ES256-signed `x-goog-iap-jwt-assertion` (signature, `iss`, `exp`/`iat`, and the exact `aud` resource path in `LOAN_DOC_IAP_AUDIENCE`) against Google's IAP public keys; derives `subject` from `email`/`sub`, `tenant` from the `hd` claim. The verified assertion is never logged. Google SDK imports are lazy, so the SDK-free profiles stay import-clean. |
| `onprem` | `adapters/onprem/identity.py` | Fail-closed placeholder: raises `NotImplementedError` rather than returning an unauthenticated identity. Implement verification against your enterprise IdP (OIDC/SAML) here and map the verified claims to a `Principal`. |

### Defense-in-depth PEP

1. **Edge** (Cloud IAP / Apigee) authenticates and gates at ingress.
2. **Hrz1 guardrail** applies central policy.
3. **This backend re-validates** the assertion and **derives identity itself** (`api/security.py`
   plus the active adapter).

Each layer assumes the others may be bypassed. This is the seam that defeats actor spoofing and
the confused-deputy risk.

---

## 5. Shape 1: embed via same-origin reverse proxy

This is the smallest change for a host that controls its edge: serve the agent **under your own
origin** at a sub-path (for example `/agent/`) via a reverse proxy, then drop an iframe pointing
at that same-origin path. The client owns exactly two things: a proxy route and an iframe tag.

### 5a. Reverse-proxy `/agent/*` to the service

**nginx**:

```nginx
# On https://portal.client.example
location /agent/ {
    proxy_pass         http://loan-agent-ui.internal:3000/;   # the Next.js UI
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-Proto $scheme;
}

# The UI's API calls (NEXT_PUBLIC_API_BASE=/agent/api) also resolve same-origin:
location /agent/api/ {
    proxy_pass         http://loan-agent-backend.internal:8092/;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-Proto $scheme;
    # IAP runs in front of this origin, so the x-goog-iap-jwt-assertion header is present
    # on the inbound request and forwarded through to the backend.
}
```

**Next.js host app** (if the parent is itself Next.js, use `rewrites()` in its own config):

```js
// next.config.mjs of the PARENT app
const nextConfig = {
  async rewrites() {
    return [
      { source: "/agent/api/:path*", destination: "http://loan-agent-backend.internal:8092/:path*" },
      { source: "/agent/:path*",     destination: "http://loan-agent-ui.internal:3000/:path*" },
    ];
  },
};
export default nextConfig;
```

### 5b. Mount the agent UI under the sub-path and hide its chrome

```bash
# Environment for the agent UI (build-time)
NEXT_PUBLIC_BASE_PATH=/agent      # mount the UI (and assets) under the sub-path
NEXT_PUBLIC_API_BASE=/agent/api   # same-origin API calls (no CORS needed)
NEXT_PUBLIC_EMBED=1               # hide the UI's own header/nav chrome when embedded
```

### 5c. The iframe tag (host page)

```html
<!-- On https://portal.client.example, inside your existing page, in a sized container -->
<iframe
  src="/agent/"
  title="Loan Document Intelligence"
  style="width:100%; height:100%; border:0;"
  loading="lazy">
</iframe>
```

Height caveat: `height:100%` renders correctly only inside a host container that has a fixed
pixel height. Content-driven height (a long verification with expanded citations) cannot push the
iframe taller today because there is no child-to-parent resize message; give the iframe a sized
container, or add a resize protocol as a further layer (Section 8).

### 5d. Allow the parent origin to frame the UI

The backend emits `Content-Security-Policy: frame-ancestors <LOAN_DOC_FRAME_ANCESTORS>` via
middleware (`api/app.py`), and adds `X-Frame-Options` **only** for the two policies the legacy
header can express: `'self'` becomes `SAMEORIGIN` and `'none'` becomes `DENY`. A multi-origin
allowlist has no `X-Frame-Options` spelling, so none is sent rather than one that contradicts
the CSP:

```bash
export LOAN_DOC_FRAME_ANCESTORS="https://portal.client.example"
# multiple parents are space-separated, per the CSP grammar:
# export LOAN_DOC_FRAME_ANCESTORS="https://portal.client.example https://admin.client.example"
```

The variable is read in three states. Unset keeps the `'self'` default; a value is used as
given; **set but empty refuses at boot** (`ConfiguredEmptyError` from the module-level
resolver), so a deployment template that renders the variable to nothing fails loudly instead
of serving a framing policy nobody chose.

The UI mirrors this. `frame-ancestors` is honoured only on the response of the document the
browser actually frames, and that document is served by Next.js, not the API, so the console
emits the same policy from `NEXT_PUBLIC_FRAME_ANCESTORS` with the same three-state rule
(`ui/lib/csp.mjs::frameAncestors`, mirroring `_frame_ancestors` in the backend; an empty value
throws, and `ui/next.config.mjs` calls the resolver at module scope so `next build` and
`next start` both refuse). Set both to the same origins.

### The console's document CSP

`frame-ancestors` alone is not a policy. It says who may frame the page and nothing about where
a script may come from, what an injected `<base>` tag may re-point every relative URL at, or
whether an `<object>` may smuggle in a plugin document. The backend middleware covers API
responses; the document a browser parses and executes is served by Next, so the full policy is
built there.

There is exactly ONE policy module, `ui/lib/csp.mjs`, and exactly ONE emitter, `ui/proxy.ts`
(Next 16's name for the middleware file). Both facts are load-bearing:

- **One module** so the two enforcement points (the per-request response header, and the
  build-time refusals in `next.config.mjs`) cannot drift apart.
- **One emitter** because a policy set in both `proxy.ts` and `next.config.mjs` is delivered to
  the browser as two policies, which are INTERSECTED: the stricter directive wins on each side,
  so a leftover nonce-less `script-src` in the static table would silently override the nonce
  one and the console would render as dead markup.

`script-src` carries a per-request nonce plus `'strict-dynamic'`, which a static `headers()`
table cannot express. Next serves its hydration bootstrap as an inline script carrying the
Flight payload, so without a matching nonce the browser blocks it, `__next_f` never fills,
React never attaches, and every control on the page stops working while the headers, the build
and every test stay green.

Two things must both hold or the nonce makes matters worse rather than better. `proxy.ts` sets
the policy on the REQUEST headers (where Next reads the nonce it stamps onto each script tag)
as well as on the response, and `app/layout.tsx` sets `export const dynamic = "force-dynamic"`
so the route is rendered per request. A statically prerendered route was built before the nonce
existed, so nothing carries it, and `'strict-dynamic'` switches off the `'self'` fallback that
had at least been loading the chunk scripts. `next.config.mjs` refuses to build without the
`force-dynamic`, and `ui/scripts/assert-hydratable.mjs` (run last in `make ui-check`) starts the
BUILT server and asserts every served script tag carries the served nonce. Only that last check
can see the difference: the response header is byte-identical in the working and broken cases.

Scope limit: `frame-ancestors` is honored only on the HTTP response of the document the browser
actually frames, and only when delivered as a real HTTP header (not a `<meta>` element). In
shape 1 the framed document is served same-origin through the proxy, so this header reaches it.

---

## 6. Configuration knobs

| Variable | Side | Purpose |
|----------|------|---------|
| `LOAN_DOC_PROFILE` | backend | `local` \| `gcp` \| `platform` \| `onprem`. Selects the identity adapter (and the whole adapter set). |
| `LOAN_DOC_IAP_AUDIENCE` | backend | The exact IAP-protected-resource path the backend verifies the assertion audience against. Required in `gcp`/`platform`. |
| `LOAN_DOC_CORS_ORIGINS` | backend | Explicit origin allowlist for the cross-origin / standalone case (comma-separated). Never `"*"`. |
| `LOAN_DOC_FRAME_ANCESTORS` | backend | CSP `frame-ancestors` allowlist: parent origins permitted to iframe the UI. Unset defaults to `'self'`; set-but-empty refuses at boot; `'none'` refuses all framing. |
| `NEXT_PUBLIC_FRAME_ANCESTORS` | UI | Same allowlist for the framed document itself (Next.js serves it, so this is the header that governs framing). Resolved by `ui/lib/csp.mjs`, same three-state rule: unset defaults to `'self'`, set-but-empty refuses at build/boot, `'none'` refuses all framing. |
| `NEXT_PUBLIC_API_BASE` | UI | Backend base URL the UI calls, and the extra origin added to the console's CSP `connect-src`. An absolute URL contributes its origin; a rooted path such as `/agent/api` is same-origin and adds nothing; anything else refuses at build. Build-time. |
| `NEXT_PUBLIC_BASE_PATH` | UI | Sub-path the UI is mounted under. Build-time. Blank keeps standalone. |
| `NEXT_PUBLIC_EMBED` | UI | Set to `1` to hide the UI's own chrome. Build-time. |
| `X-Dev-Persona` | request header | **Local profile only.** Selects a seeded dev persona; ignored in secure profiles. |

---

## 7. Checklists

### Client-side integration checklist

**Shape 1 (same-origin reverse proxy):**

- [ ] Reverse-proxy route mapping `/agent/*` to the agent UI service (5a).
- [ ] Reverse-proxy route mapping `/agent/api/*` to the agent backend service.
- [ ] UI built with `NEXT_PUBLIC_BASE_PATH=/agent`, `NEXT_PUBLIC_API_BASE=/agent/api`,
      `NEXT_PUBLIC_EMBED=1` (5b).
- [ ] `<iframe src="/agent/">` on the host page in a sized container (5c).
- [ ] `LOAN_DOC_FRAME_ANCESTORS` set to the exact parent origin(s) (5d).
- [ ] IdP federated into IAP (Workforce Identity Federation) so users carry one session through.

**Shape 2 (standalone):**

- [ ] DNS + HTTPS LB + IAP fronting the deployment.
- [ ] `LOAN_DOC_PROFILE=gcp` and `LOAN_DOC_IAP_AUDIENCE` set.
- [ ] IdP federated into IAP for SSO; URL shared with authorized users/groups.

### Security checklist

- [ ] **HTTPS everywhere** (LB terminates TLS; IAP requires it).
- [ ] **IAP audience configured**: `LOAN_DOC_IAP_AUDIENCE` set to the exact protected-resource
      path in any IAP profile (the backend refuses to verify without it).
- [ ] **Framing locked down**: `LOAN_DOC_FRAME_ANCESTORS` and `NEXT_PUBLIC_FRAME_ANCESTORS`
      set to the exact parent origin(s); left UNSET (not empty) for the `'self'` standalone
      default; never a wildcard.
- [ ] **Origins locked down**: same-origin proxy (no CORS) for shape 1; otherwise
      `LOAN_DOC_CORS_ORIGINS` is an explicit allowlist, never `"*"`.
- [ ] **No client-asserted identity trusted**: production uses `gcp`/`platform` (or an
      implemented `onprem`), not `local`; the request body carries no `actor`.

---

## 8. Further hardening layers (not built in this slice)

The shapes above cover a cooperative, GCP-aligned host. A wider multi-host rollout adds layers
that the architecture is ready to receive but that this slice does not build. The reference
implementation in `cdd-sow-research` documents them in full; in brief:

- **Cross-origin token handoff (no proxy, no IAP)** for SaaS tenants, pure SPAs, and hosts that
  will not federate into IAP: a versioned `<script>` loader plus a custom element that creates a
  sandboxed cross-origin iframe, a versioned `postMessage` contract (with strict origin checks
  and an auto-resize message), and a **host-minted, audience-scoped bearer token in memory**
  (RFC 8693 token exchange preferred) verified by a new JWKS adapter on the same `IdentityPort`
  seam. This is a pure adapter addition: `get_principal` already reads all inbound headers and
  CORS already permits `Authorization`, so no domain change is needed.
- **Launch in a new tab (OIDC redirect login)** as the simplest, most portable standalone
  fallback for any host with an OIDC-compliant IdP.
- **Per-hop OAuth2 token exchange (OBO) + Workload Identity + mTLS** to the Hrz platform
  services, and **DPoP / step-up assurance** (consume `Principal.assurance` before an approver
  action) for high-value operations.
- **Per-tenant framing/CORS/issuer policy** (replace the process-wide env vars with a
  tenant-keyed registry), a **full CSP + Trusted Types on the UI document**, and **fail-closed
  multi-tenant ACLs** before any shared multi-tenant deployment.

Until those land, keep secure deployments to shapes 1 and 2 (same-origin proxy or standalone
behind IAP), both re-verified server-side.
