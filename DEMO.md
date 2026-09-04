# Demo guide : `loan-document-intelligence` Loan / Mortgage Document Intelligence

Step-by-step scripts for demoing `loan-document-intelligence` two ways:

- **Demo A : Offline income verification on a laptop** (the headline flow): the service
  redacts applicant PII, extracts a payslip and a bank statement, normalises income, then
  runs the **deterministic cross-validation** and produces a cited, maker-checker-gated
  income verification. Two synthetic applications : one consistent (verdict VERIFIED), one
  with planted inconsistencies (verdict INCONSISTENT). Runs **fully offline** (no cloud,
  no API key, no SDK).
- **Demo B : The same flow on the managed GCP stack**: the same artifacts produced against
  real Document AI / Gemini / Model Armor / DLP in `asia-southeast1`, behind the REST API
  and the React console.

> The synthetic applicant data is **fictional**. Do not run against real applicant
> documents without your own legal, security and model-risk sign-off.

---

## 0. Prerequisites

| Need | Demo A (local) | Demo B (GCP) | Notes |
|------|:--:|:--:|-------|
| `git` | yes | yes | clone the repo |
| **Python 3.12+** | yes | yes | the package pins `>=3.12` |
| Node.js 18+ & npm | for the UI / Playwright | for the UI | only if you show the browser console |
| **Playwright** (`pip install playwright` + `playwright install chromium`) | for the guided walkthrough | — | Demo A's presenter walkthrough |
| A GCP project + `gcloud` | — | yes | billing enabled; `asia-southeast1` available |
| Terraform | — | yes | provisions Document AI, DLP, Model Armor, WORM bucket, CMEK |
| Cloud KMS key (regional) | — | yes | CMEK; set `LOAN_DOC_KMS_KEY` |

Install/setup references (read these once):

- Local install & profiles -> [README "Run locally"](README.md#run-locally-offline-no-google-cloud)
- GCP install & deploy -> [`docs/runbook.md`](docs/runbook.md) "Deploy (gcp profile)"
- The demo scripts -> [`scripts/README.md`](scripts/README.md)
- The UI console -> [`ui/README.md`](ui/README.md)
- Config (`${ENV_VAR}` resolved at load) -> [`config/settings.yaml`](config/settings.yaml)

---

## 1. Common setup (both demos)

```bash
git clone https://github.com/portable-genai/loan-document-intelligence.git
cd loan-document-intelligence

python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # core + dev tooling (NO google-cloud-* packages)

# Sanity-check the offline stack before presenting:
export LOAN_DOC_PROFILE=local
make lint test                   # ruff + mypy + pytest (all local, no cloud)
```

---

## 2. Demo A : Offline income verification (local, no cloud)

The `local` profile is a real, SDK-free laptop stack: a local document parser stands in for
Document AI, a deterministic generator stands in for Gemini, a regex DLP and a heuristic
guardrail stand in for the safety services, and an append-only SQLite store stands in for
the WORM audit bucket. **No API key, no emulator, no `google-cloud-*` package.** Four ways
to present it, in order of polish.

### 2.1 Guided, presenter-controlled walkthrough (recommended)

One command starts the real server, opens the headed walkthrough and cleans up both processes:

```bash
make demo-ui
```

Pass advanced runner options directly when invoking `scripts/run_ui_demo.py`, for example
`python scripts/run_ui_demo.py --from inconsistent-case --screenshots demo-out/walkthrough`.
Step ids are matched exactly, and `--list` prints them.

A real browser opens; the script narrates each step and **waits for you to press Enter**
before performing it, so you control the pace. (One-time: `pip install playwright &&
playwright install chromium`.)

```bash
# Terminal 1 : the live demo server (http://localhost:8093)
source .venv/bin/activate
LOAN_DOC_PROFILE=local PYTHONPATH=src python scripts/loan_doc_demo_server.py

# Terminal 2 : the guided walkthrough (a Chrome window opens)
source .venv/bin/activate
python scripts/loan_doc_demo_playwright.py
```

You'll step through, pressing Enter each time:

1. **Applications queued** : two synthetic applications ready to verify.
2. **Consistent application** (Jordan Tester Fictional) : all six checks PASS (income
   consistency, salary-credit match, name/address, balance trend, affordability); verdict
   **VERIFIED**; every figure cited to its document.
3. **Inconsistent application** (Sam Two Fictional) : payslip net pay (9000) contradicts
   the declared income (6500) and the bank credit (3000), the name differs across
   documents, the balance halves -> several checks **FAIL**; verdict **INCONSISTENT** with
   red flags.

**What to point at on screen:** the verdict badge, the deterministic cross-validation card
(each check's expected-vs-observed and citation chips), the red-flags list on the
inconsistent case, and the "HUMAN REVIEW REQUIRED" banner that is always present. Full
options (`SLOWMO_MS`, `HEADLESS`, `DEMO_AUTO`, `CHROME_PATH`, …) are in
[`scripts/README.md`](scripts/README.md).

### 2.2 Manual, click-through (no Playwright)

Run only the server and drive it yourself in any browser:

```bash
LOAN_DOC_PROFILE=local PYTHONPATH=src python scripts/loan_doc_demo_server.py   # :8093
```

Open `http://localhost:8093` and click **Next ▶** to process the next application,
**Restart** to reset. Same three steps as above.

Or drive the **real React console** against the live API, exactly as a user would:

```bash
# Terminal 1 : the API (profile=local, offline)
make run-api PROFILE=local        # FastAPI on :8092

# Terminal 2 : the Next.js console, built and served the way it ships
cd ui && npm install && npm run build && npm run start   # http://localhost:3000
```

Every demo runs against a production build, never a development server. `make run-ui` is the
developer loop with hot reload, and it is not what a presenter shows.

Click **Process sample application** : the console calls `POST /v1/process` on `:8092` and
renders the same `LoanApplicationCase` (income verification + cross-validation + extracts).

### 2.3 Static artifacts (slides / screenshots)

Generate the audit-first pages and JSON without a browser:

```bash
LOAN_DOC_PROFILE=local PYTHONPATH=src python scripts/loan_doc_demo.py loan_doc_demo.json
PYTHONPATH=src python scripts/render_loan_doc_ui.py loan_doc_demo.json ./out
# -> ./out/loan-doc-app-fictional-0001.html (VERIFIED),
#    ./out/loan-doc-app-fictional-0002.html (INCONSISTENT),
#    ./out/loan-doc-summary.html
```

`make demo` runs both steps into `./demo-out/` in one shot.

### 2.4 One-shot verification via the CLI (quick variant)

If you only want to show a single cited verification (not the two-application story):

```bash
export LOAN_DOC_PROFILE=local
loan-document-intelligence process examples/application.json   # full pipeline, VERIFIED
loan-document-intelligence validate examples/extracts.json     # deterministic checks only (no model)
```

---

## 3. Demo B : The same flow on the managed GCP stack

Shows the same domain producing a cited income verification against **real managed
services** in `asia-southeast1`. Follow [`docs/runbook.md`](docs/runbook.md) "Deploy (gcp
profile)" for the authoritative steps; the short version:

### 3.1 GCP setup

```bash
source .venv/bin/activate
pip install -e ".[gcp,dev]"                 # adds google-adk, google-genai, documentai, dlp, ...

export GOOGLE_CLOUD_PROJECT=your-sg-project
export LOAN_DOC_PROFILE=gcp
export LOAN_DOC_KMS_KEY="projects/.../locations/asia-southeast1/keyRings/.../cryptoKeys/..."
gcloud auth application-default login
```

### 3.2 Provision infra (one-time)

```bash
make tf-plan          # review the plan : the WORM bucket lock is IRREVERSIBLE
cd infra/terraform && terraform apply && cd ../..
# Export the outputs the app reads:
export LOAN_DOC_DOCAI_PROCESSOR="$(terraform -chdir=infra/terraform output -raw document_ai_processor_id)"
export LOAN_DOC_DLP_INSPECT_TEMPLATE="$(terraform -chdir=infra/terraform output -raw dlp_inspect_template)"
export LOAN_DOC_DLP_DEIDENTIFY_TEMPLATE="$(terraform -chdir=infra/terraform output -raw dlp_deidentify_template)"
```

Details and gotchas (region fail-fast, key rotation, retention): [`docs/runbook.md`](docs/runbook.md).

### 3.3 Run and show

```bash
make run-api PROFILE=gcp          # FastAPI on :8092, profile=gcp
```

Then demo any surface. Replace the example URIs with reviewed synthetic PDFs in a
Singapore-region bucket and seed server-side entitlements for the application and both document
IDs. The committed `gs://fictional/...` names are local fixture identifiers, not cloud objects.

```bash
# REST : produce an income verification. There is no `actor` in the body: the audit actor
# is the server-verified identity (an IAP assertion in gcp mode, a seeded persona in local
# mode via X-Dev-Persona). In local mode, add e.g. -H 'X-Dev-Persona: approver'.
curl -s localhost:8092/v1/process -H 'content-type: application/json' -d '{
  "application": {
    "id": "app-fictional-0001",
    "name": "Jordan Tester Fictional",
    "address": "123 Imaginary Road, Singapore 000000",
    "declared_income": {"source_doc_id":"declared","amount":6500.0,"currency":"SGD","period":"monthly","kind":"salary"}
  },
  "documents": [
    {"id":"doc-payslip-2026-04","doc_type":"payslip","uri":"gs://YOUR-REVIEWED-DEMO-BUCKET/payslip.pdf"},
    {"id":"doc-bank-2026-04","doc_type":"bank_statement","uri":"gs://YOUR-REVIEWED-DEMO-BUCKET/bank.pdf"}
  ]
}' | python -m json.tool

# Seeded dev personas (local profile only) and agent card / health
curl -s localhost:8092/v1/personas | python -m json.tool
curl -s localhost:8092/.well-known/agent-card.json | python -m json.tool
curl -s localhost:8092/healthz
```

Or the browser console (talks to the API on :8092) : see [`ui/README.md`](ui/README.md):

```bash
cd ui && npm install && npm run build && npm run start   # http://localhost:3000
```

The same console accepts those two GCS URIs in its document-source fields. Through `journey-portal`, use
the portal's hosted HTTPS origin and the `loan-document-intelligence` tab; the immutable `loan-document-intelligence` UI and API image digests
are installation inputs. A successful Terraform validation or plan is not a hosted-demo claim:
retain Cloud Run health, profile/region, browser, artifact and audit-correlation evidence from
the named target.

**What to highlight:** every figure carries a source-document + field **citation**;
applicant PII (name, NRIC, bank account) is redacted before any model / audit / trace call
(P-04); the **deterministic** `CrossValidator` owns every verdict (the LLM only normalises
and explains, never decides); the case is **always** maker-checker gated (P-06); everything
stays in `asia-southeast1` with CMEK ([README "Compliance"](README.md#compliance)).

---

## 4. Talking points

- **The model proposes; arithmetic disposes.** Every PASS/WARN/FAIL is a pure,
  deterministic comparison with an explicit expected vs observed : reproducible and
  auditable. The LLM only normalises figures and writes prose; it can never change a verdict
  (P-06 boundary).
- **Decision-support, not a decision.** `loan-document-intelligence` verifies income; the underwriter approves. Every
  case is `requires_human_review = True`.
- **Audit-first output.** Income summary, the six deterministic checks, and the redacted
  extracts : each figure proven by a document-and-field citation.
- **Guardrails hold.** Redact-before-everything (P-04), heuristic/Model-Armor guardrail on
  input and output, WORM audit (`agent-observability`), single-region + CMEK residency.
- **No vendor lock-in.** The same domain code runs offline (`local`), on the managed stack
  (`gcp`), against the shared platform (`platform`), or fails fast on the on-prem migration
  target (`onprem`) : a one-line profile change.

---

## 5. Troubleshooting & cleanup

| Symptom | Fix |
|---------|-----|
| `python3.12: command not found` | Install Python 3.12+; the package pins `>=3.12`. |
| Playwright: "executable doesn't exist" | `playwright install chromium`, or set `CHROME_PATH=/path/to/chrome`. |
| No display for the headed walkthrough | Use 2.2 (manual browser) on a machine with a display, or `HEADLESS=1 DEMO_AUTO=1 python scripts/loan_doc_demo_playwright.py` to self-run. |
| "Cannot reach the demo server" | Start 2.1 Terminal 1 first; or set `DEMO_URL` if you changed `--port`. |
| Demo port 8093 in use | `python scripts/loan_doc_demo_server.py --port 9000` (then `DEMO_URL=http://127.0.0.1:9000`). |
| API port 8092 in use | `make run-api PROFILE=local API_PORT=9092` (the UI reads `NEXT_PUBLIC_API_BASE`). |
| UI cannot reach the API | The API CORS-allows `localhost:3000`; set `ui/.env.local` `NEXT_PUBLIC_API_BASE` if the API is not on `:8092`. |
| `ModuleNotFoundError: loan_doc_intel` | Set `PYTHONPATH=src` (the demo scripts import the package directly). |
| CLI exits with code 2 naming a migration target | You're on `LOAN_DOC_PROFILE=onprem` (fail-fast). Use `local` (Demo A) or `gcp` (Demo B). |
| GCP deploy / region / CMEK errors | See [`docs/runbook.md`](docs/runbook.md) "Deploy" and "Residency and key rotation". |

**Stop / clean up:** Ctrl-C the demo server, `make run-api` and the console. For GCP,
scale the deployment to zero or remove the app SA's model-access role : the audit trail
remains intact. `make clean` removes local caches/artefacts; `rm -rf demo-out out` removes
the generated demo pages.
