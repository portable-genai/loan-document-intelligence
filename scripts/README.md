# Demo scripts : `loan-document-intelligence` loan / mortgage document intelligence

All scripts are SDK-free and run against the in-process `local` stack (no Google Cloud,
no API key, no emulator). Run them from the repo root with the package on the path and the
local profile selected:

```bash
export LOAN_DOC_PROFILE=local
export PYTHONPATH=src
```

| Script | What it does |
|--------|--------------|
| `loan_doc_demo.py` | Drives the real `LoanDocService.process` pipeline offline over two synthetic applications (one consistent, one with planted inconsistencies) and writes the audit-view JSON. |
| `render_loan_doc_ui.py` | Renders that JSON into static audit-first HTML pages (one per applicant + a verdict summary) for screenshots. |
| `loan_doc_demo_server.py` | A **live, click-through** server (stdlib only) that runs the *real* pipeline one step per click and renders the audit-first UI. Demo port `8093` (distinct from the API port `8092`). |
| `loan_doc_demo_playwright.py` | A **presenter-controlled** Playwright walkthrough of the live server: it narrates each step and waits for you to press Enter before performing it. |

The scripts reuse the domain pipeline exactly as the API `POST /v1/process` and the React
console do : redact applicant PII (P-04), guardrail-screen, extract each document, let the
LLM normalise income figures, then run the **deterministic** `CrossValidator` (the verdict
authority : the model never changes a check status), and always flag the case for
maker-checker review (P-06). The two synthetic applicants are clearly fictional.

## Static artifacts (slides / screenshots)

```bash
python scripts/loan_doc_demo.py loan_doc_demo.json        # prints the per-applicant summary
python scripts/render_loan_doc_ui.py loan_doc_demo.json ./out
# -> ./out/loan-doc-app-fictional-0001.html (VERIFIED),
#    ./out/loan-doc-app-fictional-0002.html (INCONSISTENT),
#    ./out/loan-doc-summary.html
```

## Live, presenter-controlled demo

Two terminals:

```bash
# 1) the live demo server  (http://localhost:8093)
LOAN_DOC_PROFILE=local PYTHONPATH=src python scripts/loan_doc_demo_server.py

# 2) the guided walkthrough  (a real Chrome window opens)
pip install playwright && playwright install chromium      # one-time
python scripts/loan_doc_demo_playwright.py
```

The walkthrough is **paced by you**: it prints what the next step will do, waits for you to
press **Enter**, then clicks **Next ▶** and spotlights the panel to look at. The three steps
are: applications queued -> process the consistent one (VERIFIED, six green checks) ->
process the planted-inconsistency one (INCONSISTENT, failed checks + red flags).

You can also just open `http://localhost:8093` and click **Next ▶** / **Restart** by hand :
the server holds the live session, so the buttons drive the same real pipeline.

`make demo` runs the static path end to end (JSON + HTML into `./demo-out/`).

## Environment overrides for `loan_doc_demo_playwright.py`

| Var | Default | Purpose |
|-----|---------|---------|
| `DEMO_URL` | `http://127.0.0.1:8093` | server base URL (point at `http://localhost:3000` to drive the live console) |
| `HEADLESS=1` | off | run without a window (self-test / recording) |
| `DEMO_AUTO=1` | off | don't wait for Enter : advance automatically |
| `SLOWMO_MS` | `250` headed | per-action slow motion |
| `CHROME_PATH` | — | explicit Chromium/Chrome binary |
| `lock.py` | Compiles both lockfiles and puts the header back, because `uv pip compile` REPLACES the output file: it writes its own two-line provenance comment and destroys the `tag = commit` map the pin tests check against. `make lock` runs this rather than uv directly. |

> Playwright is a **demo-time** dependency only (`pip install playwright`). It is never
> added to the package core or the `[gcp]` / `[dev]` extras, and the scripts live outside
> the lint/type/test gate.
