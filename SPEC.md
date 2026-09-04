# `loan-document-intelligence` Loan / Mortgage Document Intelligence : specification

## 1. Purpose and scope

`loan-document-intelligence` is a document-intelligence agent for retail-lending underwriting. It extracts income and
bank-statement data from an applicant's documents (Document AI) and runs deterministic
cross-validation across them, producing a cited, audited, maker-checker-gated income
verification. It is **decision-support for underwriting, not a lending decision**: the agent
verifies, the underwriter decides (P-06).

`loan-document-intelligence` handles applicant PII (income, bank data), so the full **R1** redaction + guardrail
pipeline applies. It does document extraction + deterministic validation, **not** RAG over a
corpus, so **R3 / `enterprise-knowledge-base` is N/A**.

- Catalog identity: `loan-document-intelligence`, group `doc`, priority P2, buyer Retail Lending. Service port 8092.
- Python package: `loan_doc_intel`. Profile env var: `LOAN_DOC_PROFILE`
  (gcp | local | platform | onprem).

## 2. Deployment and residency

Built ports-and-adapters on the Gemini Enterprise Agent Platform, region pinned to
`asia-southeast1` (Singapore) for applicant-PII residency. The managed stack (`gcp`
profile) uses Document AI, Gemini, Model Armor, DLP, Cloud Logging (WORM) and Cloud Trace.
The `platform` profile delegates safety/governance/audit/eval to the shared
horizontal-platform services. The `onprem` profile binds SDK-free fail-fast placeholder
adapters (the Google Distributed Cloud migration target).

The `local` profile is a WORKING offline laptop stack (the dev / test default): every port
binds to a real, deterministic, SDK-free adapter so the whole pipeline runs with no Google
Cloud, no API key and no emulator. The backends are:

| Concern | Managed (`gcp`) | Offline (`local`) |
| --- | --- | --- |
| Extraction | Document AI | local document parser + seedable canned-extract store |
| LLM | Gemini | deterministic schema-driven income normaliser (no model) |
| Guardrail | Model Armor | heuristic prompt-injection / jailbreak screen |
| PII redaction | Sensitive Data Protection / DLP | regex de-identification driven by the jurisdiction PII pack (SG/HK/JP/AU national ids + email / phone / account) |
| Audit | Cloud Logging WORM bucket | append-only SQLite (or `:memory:`) store |
| Tracer | Cloud Trace | no-op spans |
| Session / memory / registry / tool catalog | Agent Platform / `agent-registry` | in-process stores |
| Agent runtime | Agent Runtime | in-process `LoanDocService` |
| Eval gate | Gen AI evaluation service | the in-repo offline `eval/run_eval.py` |

For the stores with an official Google emulator, the `local` adapters route to it when the
standard `*_EMULATOR_HOST` env var is set and the client lib imports (the google client is
imported lazily, only on that branch); otherwise they use the SQLite / in-process path.
There is no emulator for Document AI, Gemini, Model Armor or DLP, so those stay SDK-free.
The default `local` path imports no `google-cloud-*` package.

## 3. Tech stack (pinned, mid-2026 GA)

- Models: reasoning `gemini-3.5-flash` (thinking=high), triage `gemini-3.5-flash`.
  Unified SDK `google-genai`. ADK `google-adk==2.7.1`. A2A v1.0 + MCP 2026-07-28.
- Extraction: Document AI (a form/lending parser) in `asia-southeast1`.
- Safety: Model Armor (guardrail) + Sensitive Data Protection / DLP (PII redaction).
- Audit: Cloud Logging locked WORM bucket, retention 2557 days. Tracing: Cloud Trace via
  OpenTelemetry, message-content capture OFF. Eval: Gen AI evaluation service.
- Core deps are framework-light (pydantic, pyyaml, httpx, tenacity, typer, fastapi,
  uvicorn, python-dateutil); all google-cloud-* / google-adk / google-genai live in `[gcp]`.

## 4. Artifacts

1. **LoanApplicationCase**: the deliverable, bundling the extracts, the cross-validation
   result and the income summary. Always `requires_human_review = True`.
2. **CrossValidationResult**: the deterministic consistency checks across documents, each
   PASS | WARN | FAIL with expected vs observed and field-level evidence. The heart.
3. **IncomeVerificationSummary**: the verified income figure(s), stability, red flags and a
   verdict (VERIFIED | NEEDS_REVIEW | INCONSISTENT).

Every figure is cited to a source document and field; every interaction is audited (WORM).

## 5. Domain services and the pipeline

- `LoanDocService(extraction, llm, guardrail, redaction, tracer, audit, validator=None,
  income_service=None, review_policy=None)`. `.process(application, documents, actor) ->
  LoanApplicationCase`. `.extract_only(document, content, mime_type, actor) -> DocumentExtract`.
- `CrossValidator` (pure domain, the heart): `validate(extracts, applicant, application_id)
  -> CrossValidationResult` with DETERMINISTIC rules: income figures within tolerance across
  docs; salary credits in the bank statement match the payslip net pay; declared
  name/address match the documents; balance trend not declining; simple affordability ratio.
  The LLM only explains, it never changes a check outcome.
- `IncomeVerificationService` (pure-ish): derive the verified income + verdict from the
  extracts + figures + checks (VERIFIED only if all critical checks PASS).
- `LoanReviewPolicy`: the case is consequential and always `requires_human_review=True`; any
  FAIL or INCONSISTENT escalates the audit decision.

### Pipeline (R1 full safety; tracer.span; audited)

`redaction.redact(inputs)` -> `guardrail.screen(INPUT)` -> per document `extraction.extract`
then redact the extract -> `llm` normalise into IncomeFigure[] -> `CrossValidator.validate`
(deterministic) -> `IncomeVerificationService` verdict -> assemble LoanApplicationCase ->
`guardrail.screen(OUTPUT)` -> review policy (always true) -> `audit.record`. A guardrail
block, missing documents and malformed model JSON all degrade to a safe, human-review
flagged case rather than crashing.

## 6. HTTP API (this repo DEFINES)

All JSON field names mirror the domain dataclasses (enums as strings). No request body carries
an `actor`: the audit actor is the server-verified `Principal` resolved by the `IdentityPort`
from the request identity (a Cloud IAP assertion in secure mode, a seeded persona selected by
`X-Dev-Persona` in local mode); an unverifiable identity is a `401`.

- `POST /v1/process {application, documents[]}` -> `LoanApplicationCase`.
- `POST /v1/extract {document}` -> `DocumentExtract`.
- `POST /v1/validate {application_id, applicant, extracts[]}` -> `CrossValidationResult`.
- `GET /healthz` -> `{status, profile, region}`.
- `GET /v1/personas` -> `[{id, subject, tenant, principals}]` (seeded dev personas; empty
  outside the `local` profile).
- `GET /.well-known/agent-card.json` -> AgentCard `{name, description, url, version,
  provider, skills:[{id,name,description}]}`. Skills: process_application, extract_document,
  cross_validate.

### Cross-repo services `loan-document-intelligence` CONSUMES

- **`agent-guardrail-gateway`** (`GUARDRAIL_GATEWAY_URL`): `POST /v1/guardrail/screen`, `POST /v1/redact`.
- **`agent-registry`** (`AGENT_REGISTRY_URL`): `POST /v1/agents`, `GET /v1/agents/{name}`.
- **`model-quality-gate` AI quality** (`QUALITY_GATE_URL`): `POST /v1/evaluations {target, dataset_id, bundle}`
  (report parsed from `results[]`, not `metrics[]`) and `POST /v1/gate {target, dataset_id,
  bundle}` -> `{passed}`. `target` is structured (`{model, prompt_version, dataset_id, system}`)
  and its `dataset_id` must mirror the top-level one (`model-quality-gate` 422s on divergence). Metrics are
  selected server-side by the registered `bundle` name (`doc5-loan-document-intelligence`), so the client
  never sends bare metric names.
- **`agent-observability`** (`OBSERVABILITY_URL`): `POST /v1/audit`.

## 7. Ports and adapters

| Port | gcp | local | platform | onprem |
| --- | --- | --- | --- | --- |
| DocumentExtractionPort | Document AI | local parser + canned-extract store | n/a | placeholder |
| LLMPort | Gemini | deterministic schema-driven generator | n/a | placeholder |
| GuardrailPort | Model Armor | heuristic injection screen | `agent-guardrail-gateway` | placeholder |
| PIIRedactionPort | DLP | regex de-identification | `agent-guardrail-gateway` | placeholder |
| AuditSinkPort | Cloud Logging WORM | append-only SQLite | `agent-observability` | placeholder |
| ObservabilityTracerPort | Cloud Trace | no-op | n/a | no-op |
| EvaluationGatePort | Gen AI eval | in-repo offline gate | `model-quality-gate` | placeholder |
| AgentRegistryPort | A2A in-process | in-process | `agent-registry` | placeholder |
| ToolCatalogPort | MCP catalog | in-process catalog | n/a | placeholder |
| AgentRuntimePort / SessionPort / MemoryPort | Agent Runtime / Sessions / Memory Bank | in-process | n/a | placeholder |

Every adapter constructor is exactly `def __init__(self, settings: Settings) -> None`. The
dotted paths in `config/settings.yaml` are the build contract (the contract test reads them).

## 8. Eval gate (`model-quality-gate` / P-08)

`eval/run_eval.py` runs the real `LoanDocService` over a synthetic golden set (consistent +
planted-inconsistency cases) with deterministic fakes, computing:
`extraction_accuracy` (>=0.80), `validation_recall` (>=0.90, catches planted
inconsistencies), `validation_precision` (>=0.90, no false flags on consistent docs),
`pii_safety` (>=0.99, no unredacted applicant PII in output/audit). Exit non-zero on fail.

## 9. Dependencies (catalog matrix)

`agent-guardrail-gateway` (R1), `agent-registry` (R4), `model-quality-gate` AI Quality eval gate at promotion (R5), `agent-observability` (R2). Validated by `architecture-validator` at intake (R6). R3 / `enterprise-knowledge-base` (RAG) is N/A. Synthetic
applicant data is fictional.
