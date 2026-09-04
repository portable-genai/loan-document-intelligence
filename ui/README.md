# `loan-document-intelligence` demo console (UI)

A small React / Next.js console for the `loan-document-intelligence` Loan / Mortgage Document Intelligence service. It
renders the three `loan-document-intelligence` artifacts (the `LoanApplicationCase`, its `CrossValidationResult`, and
the `IncomeVerificationSummary`) returned by the FastAPI backend.

The console has its own gate, separate from the Python one: see [Gate](#gate) below.
`node_modules` and `.next` are gitignored.

## Run

```bash
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_BASE if the API is not on :8092
npm install
npm run dev                        # http://localhost:3000
```

The backend must be running (the API CORS-allows `localhost:3000`):

```bash
cd .. && loan-document-intelligence serve --port 8092
```

## Layout

```
app/         layout.tsx (sets `dynamic = "force-dynamic"`, required by the nonce CSP),
             page.tsx (the process console), globals.css
components/  CaseView, CrossValidationView, IncomeSummaryView, ExtractView, ui primitives
lib/         api.ts (the fetch client), types.ts (mirrors the API schemas),
             csp.mjs (THE policy module: the CSP, the three-state framing read, the refusals)
proxy.ts     the ONLY emitter of the CSP and X-Frame-Options (Next 16's middleware file)
next.config.mjs  no CSP here on purpose; it calls the two build-time refusals plus the
             static-expressible headers (nosniff, no-referrer)
scripts/     assert-hydratable.mjs (starts the BUILT server and proves the page hydrates)
tests/       csp.test.mjs (what a policy STRING can decide)
```

The page sends a synthetic, clearly-fictional application to `POST /v1/process`. All applicant
data is invented and must never be treated as real.

## Gate

```bash
make ui-install   # npm ci
make ui-check     # tsc --noEmit, npm test, next build, then assert-hydratable
```

The order is deliberate and `assert-hydratable` is last, because it runs against the artefact
the build just produced. Everything before it passes identically whether or not the served HTML
carries the CSP nonce: the response header is byte-identical in the working case and in the
broken one, so only starting the built server and comparing its `<script>` tags against the
served nonce can tell a hydrating console from dead markup. See
[`docs/embedding-and-identity.md`](../docs/embedding-and-identity.md) for the policy itself.
