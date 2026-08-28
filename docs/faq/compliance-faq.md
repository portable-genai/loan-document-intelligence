# Compliance FAQ

For compliance, credit-risk, and model-risk teams assessing the regulatory posture of Doc5
(Loan / Mortgage Document Intelligence). Cross-references: [`COMPLIANCE.md`](../../COMPLIANCE.md)
(the full principle -> control map), [`SPEC.md`](../../SPEC.md),
[`docs/practices-audit.md`](../practices-audit.md).

### Is this making lending decisions autonomously?

No. It is **decision-support for underwriting, not a lending decision** (P-05). Every output
sets `requires_human_review = True`, and `hitl.LoanReviewPolicy.requires_review()` always
returns `True` (maker-checker, P-06): the underwriter decides, the agent verifies. Escalation
signals (an INCONSISTENT verdict, a failed affordability check, a declining-balance red flag)
only *raise* the review bar; they never lower it and never auto-execute. Escalated cases are
**routed** to the sibling **Hrz7 Human-Review & Maker-Checker Console** (rule R8), not left as
a per-repo boolean.

### How is the work auditable / reproducible?

The consequential logic is **deterministic and replayable**: `domain/cross_validator.py`
(pure stdlib) is the verdict authority, running declared-income-vs-payslip-vs-bank-statement
reconciliation, name / address consistency, balance-trend and affordability checks, each
PASS / WARN / FAIL with expected vs observed. The LLM only normalises extracts into figures
and explains; it never overrides a check verdict (P-11), so an auditor can recompute every
figure from the same inputs without the model. Every verified figure carries a field-level
`Citation` to its source document (P-10), and every interaction writes an immutable,
already-redacted WORM `AuditEvent` (P-07). The enterprise WORM audit system is **Hrz5**; the
in-repo hash-chained store is the offline stand-in (see
[security-faq.md](security-faq.md) for its exact tamper-evidence limits).

### How is applicant PII handled?

Redact-before-everything (P-04): the orchestrator redacts inputs before any model, guardrail
or audit call, and re-redacts extracted free-text. National-identifier detection is
**jurisdiction-driven** (`pii.jurisdictions` in `config/settings.yaml`,
`domain/pii_patterns.py`, one SG / HK / JP / AU pack) so a non-Singapore deployment scrubs,
and gates on, its own identifiers rather than just the SG NRIC. The runtime guardrail / DLP
gateway itself is the sibling **Hrz1 Agent Guardrail Gateway**; this repo consumes it rather
than re-implementing it.

### What is the model-risk story?

An offline eval gate (`eval/run_eval.py`) scores extraction accuracy, validation
recall / precision, and `pii_safety >= 0.99` against a fictional golden set, failing the build
below threshold (P-08), and runs with zero cloud credentials. The enterprise promotion gate
and model-documentation / red-team harness are the sibling **Hrz4 AI Quality & Model-Risk
Platform**; this repo's `--mode gate` delegates to it (registered bundle
`doc5-loan-document-intelligence`) and the offline gate mirrors its thresholds so merges are guarded
locally. A fork must rebuild the golden set for its own vertical, or the gate measures the
wrong thing.

### Which regulators does this map to?

`COMPLIANCE.md` maps the internal **P-01..P-12** General Principles and the **R1..R6, R8**
platform dependency rules to concrete code, each row pointing at a file. Honest scope note:
there is **not yet a per-regulator crosswalk appendix** (e.g. a MAS / TRM or lending-conduct
mapping) marked adopter-owned; that is tracked as audit check **G2 (PARTIAL)** in
[`docs/practices-audit.md`](../practices-audit.md). The internal control column is stable, so
a fork adds its regulator column and re-reviews with local counsel. At scale, the sibling
**Rsk2 Cloud Control-Mapping Toolkit** and **Rsk1 Compliance Assistant** generate and
maintain these crosswalks; a large estate should integrate them rather than hand-maintain a
table.

### Is data residency enforced?

Yes at deploy time, with one stated exception: a single in-country region (default
`asia-southeast1` / Singapore), validated to fail fast, with regional endpoints, CMEK (P-10),
and a VPC-SC perimeter (P-01, P-09). **Document AI is not in-country:** it reaches
`asia-southeast1` only once Google grants single-region access, so document extraction routes to
the `us` multi-region until then. That is a jurisdiction, not a global endpoint. Other honest
gaps: the perimeter is currently an enforced `status {}` block rather than dry-run-first, and
there is no CI `terraform validate` job (audit check **D5 (PARTIAL)**). The
residency-violation CI gate is the sibling **Rsk4 Data Residency / Sovereignty Validator**;
the exit / concentration-risk plan is **Rsk5 Exit & Portability Planner**. This repo enforces
residency in its own infra and is one of the systems those tools reason about.

### Can we run it against real customer data today?

Not without your own legal, security, and model-risk sign-off. Every fixture and the golden
set are obviously fictional ("Casey Fictional", `gs://fictional/...`), and the docs state
throughout that this is a reference build. The adoption checklist
([`docs/ADOPTING.md`](../ADOPTING.md)) lists the steps, replace reference data, own the
validation policy, wire your IdP, rebuild the eval golden set, that must precede any live-data
use.

### Which lending-lifecycle stages does it cover, and which does it not?

It covers **onboarding-time income verification** from an applicant's own documents
(payslips, bank statements, declared application data), with deterministic cross-validation
and a maker-checker-gated verdict. It is deliberately scoped to the document-diligence slice
and does not absorb enterprise credit decisioning, fraud / AML transaction monitoring, or
perpetual re-verification of the existing book, which have (or may get) their own catalog
homes. Note it does document extraction plus deterministic validation, **not** RAG over a
corpus, so the governed knowledge base (Hrz2, rule R3) is **N/A** for Doc5. See
[features-faq.md](features-faq.md) for the full boundary.
