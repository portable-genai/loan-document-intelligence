# Features FAQ

For product, compliance, and delivery teams: what this agent does, what is deterministic vs
LLM, and, importantly, where its responsibilities **stop** and a sibling catalog system
takes over. Cross-references: [`README.md`](../../README.md), [`DEMO.md`](../../DEMO.md),
[`SPEC.md`](../../SPEC.md).

### What does `loan-document-intelligence` actually produce?

A cited, audited **income verification** for a retail-lending application. From an
applicant's documents (payslips, bank statements, and the declared application data) it
produces a `LoanApplicationCase` carrying three artifacts: the per-document
`DocumentExtract`s, a `CrossValidationResult` (the deterministic consistency checks), and an
`IncomeVerificationSummary` (the verified income figure(s), stability, red flags, and a
verdict of VERIFIED / NEEDS_REVIEW / INCONSISTENT). Every figure carries a
source-document-and-field `Citation`, and the whole interaction writes a WORM audit event.

### What is deterministic vs done by the LLM?

The consequential logic is **deterministic and replayable** (pure stdlib, unit-tested):
`domain/cross_validator.py` is the verdict authority, running declared-income-vs-payslip
-vs-bank-statement-salary-credit reconciliation, name/address consistency, balance-trend and
affordability checks, each PASS / WARN / FAIL with expected vs observed and field-level
evidence; `domain/income_service.py` derives the verified income and verdict; `hitl` decides
the review disposition. The LLM only **normalises** the raw extracts into structured figures
and **explains**; it never overrides a deterministic check verdict. An underwriter (or an
auditor) can recompute every figure without the model. This is the "deterministic domain
service" pattern.

### Is anything auto-approved?

No. It is **decision-support for underwriting, not a lending decision**. Every output sets
`requires_human_review=True`, and `LoanReviewPolicy.requires_review()` always returns
`True` (maker-checker, P-06). Escalation signals (an INCONSISTENT verdict, a failed
affordability check, a declining-balance red flag) only *raise* the review bar; they never
lower it and never auto-execute. Escalated cases route to the human-review console (`human-review-console`)
rather than a per-repo boolean.

### Which capabilities does this repo own vs integrate from the catalog?

This is one system in a catalog of composable GRC systems. It **owns** the loan
document-intelligence domain logic (extraction orchestration, the deterministic
cross-validation, income verification, and the maker-checker policy). It **integrates**
(via the `platform` profile's HTTP adapters) cross-cutting concerns owned by sibling
platform systems; do not rebuild these in a fork:

| Concern | Owned by (catalog id / repo) | `loan-document-intelligence`'s role |
|---|---|---|
| Runtime guardrail: PII redaction, prompt-injection / jailbreak defense | `agent-guardrail-gateway` | consumes it on every request (input + output screen) |
| Agent registry, versioning, identity, entitlements | `agent-registry` | publishes its A2A AgentCard for discovery |
| AI-quality / eval / model-risk promotion gate | `model-quality-gate` | its eval metrics gate promotion; the offline gate mirrors it |
| Observability + immutable WORM prompt/response audit | `agent-observability` | writes audit events to it; traces spans through it |
| Human-review & maker-checker console | `human-review-console` `review-kit` producer | routes an escalated case to it (rule R8) |
| Architecture-conformance validation at intake | `architecture-validator` | is validated by it (R6) |
| On-prem, CPU-only DLP scrub before egress | `onprem-dlp` | the sovereign-DLP option behind the redaction port |

So the guardrail, audit sink, eval platform, and review console are *dependencies*, not
features of this repo. Note this agent does document extraction plus deterministic
validation, **not** RAG over a corpus, so the governed knowledge base (`enterprise-knowledge-base` / R3) is **N/A**
for `loan-document-intelligence`.

### How does this relate to the other lending / financial-crime systems in the catalog?

`loan-document-intelligence` is onboarding-time income verification from documents. It is deliberately scoped to the
document-diligence slice and should not absorb capabilities that have (or may get) their own
catalog home: enterprise credit decisioning, fraud/AML transaction monitoring, or perpetual
re-verification of the existing book. Check
[the organization's repository index](https://github.com/portable-genai) before building a
capability that may already belong to a sibling system.

### Can I use this for a non-lending document-diligence product?

Yes. The reusable core (typed ports, the reconciliation engine mechanics, citations,
grounding, audit, maker-checker, the eval harness) transfers to other document-diligence
verticals (credit-memo review, trade-finance checking, claims triage, KYC / source-of-wealth).
You replace the artifact models and prompts and retune the validation policy and taxonomy.
See [`docs/ADOPTING.md`](../ADOPTING.md) and [adoption-faq.md](adoption-faq.md). Note the
kernel-vs-vertical boundary is currently a documented convention rather than a separate
`kernel.py` module (tracked as A7 in [`docs/practices-audit.md`](../practices-audit.md)).

### How do I see it working?

`make demo` runs the offline demo in one command (`LOAN_DOC_PROFILE=local`), producing an
audit-view JSON plus static HTML from the bundled synthetic application. `make run-local`
processes `examples/application.json` end-to-end offline. Everything runs on synthetic,
fictional data with no cloud and no API key.
