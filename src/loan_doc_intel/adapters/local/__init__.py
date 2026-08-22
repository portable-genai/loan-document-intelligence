"""Local deployment profile adapters : a WORKING, offline laptop stack.

The ``local`` profile is the third deployment option alongside ``gcp`` (managed
Google Cloud services) and ``onprem`` (fail-fast Google Distributed Cloud migration
placeholders). Unlike ``onprem``, every adapter here is a *real, deterministic*
implementation that runs the whole B5 pipeline end to end with **no Google Cloud, no
API key, and no running emulators by default**:

* Extraction (Document AI) -> a local document parser plus a seedable canned-extract
  store, so the deterministic cross-validation always receives real structured fields.
* LLM (Gemini) -> a deterministic, schema-driven income normaliser (no model, no
  network).
* Guardrail (Model Armor) -> a heuristic that blocks prompt-injection / jailbreak text.
* PII redaction (DLP) -> regex de-identification (SG NRIC/FIN, emails, account numbers).
* Audit (Cloud Logging WORM) -> an append-only local store (SQLite or JSONL).
* Tracer (Cloud Trace) -> no-op spans.
* Session / memory / registry / tool catalog -> SQLite or in-process stores, seedable.
* Agent runtime -> the in-process ``LoanDocService`` (a laptop runs one app).
* Evaluation -> delegates to the in-repo offline eval gate (``eval/run_eval.py``).

Everything is **seedable** so the test suite stays deterministic, and the default code
path imports **no google-cloud package at module top level**. Optional higher-fidelity
local runs route to Google's official emulators when the standard ``*_EMULATOR_HOST``
env vars are set (the google client is imported lazily, only on that branch); see
:mod:`loan_doc_intel.adapters.local._emulator`.
"""
