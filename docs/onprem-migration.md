# On-prem migration guide (the exit story, P-12)

Doc5's reversibility is a *demonstrable* property, not a claim. The domain core talks only to
ports; the managed Google Cloud services sit behind adapters in `adapters/gcp/`. Switching to
an on-premise platform (for example Google Distributed Cloud, or a sovereign data centre) is a
matter of filling in the `adapters/onprem/` placeholders : the domain logic and the service
callers do not change.

## What "onprem" means today

`config/settings.yaml` binds every port to three adapter families: `gcp`, `platform` and
`onprem`. Setting `LOAN_DOC_PROFILE=onprem` (or `profile: onprem`) rebinds the whole stack to
the placeholders. The contract tests (`tests/contract/test_port_parity.py`) prove that each
placeholder:

1. constructs cleanly with a single `Settings` argument and **no Google Cloud SDK installed**,
2. structurally satisfies its `runtime_checkable` Protocol, and
3. declares every member the Protocol requires.

So the on-prem profile imports, builds the container and runs the full pipeline shape today;
the methods raise `NotImplementedError` until a real backend is wired in.

## The placeholders and what each needs

| Port | On-prem placeholder | What to implement |
| --- | --- | --- |
| `DocumentExtractionPort` | `adapters/onprem/extraction.py` | A document parser (OCR + field extraction) for payslips, bank statements, tax returns. Must return a `DocumentExtract` with the fields/line items the `CrossValidator` reads. |
| `LLMPort` | `adapters/onprem/llm.py` | An on-prem LLM endpoint for income normalisation + explanation. It must honour the structured-output schema. |
| `GuardrailPort` | `adapters/onprem/guardrail.py` | A real screening backend; do not fail-open. |
| `PIIRedactionPort` | `adapters/onprem/redaction.py` | A real de-identifier; do not pass text through unredacted (P-04). |
| `AuditSinkPort` | `adapters/onprem/audit.py` | An immutable (WORM) audit store; do not drop records. |
| `ObservabilityTracerPort` | `adapters/onprem/tracer.py` | Already a safe no-op (tracing absent, not fatal); wire to an internal collector when ready. |
| `EvaluationGatePort` | `adapters/onprem/evaluation.py` | A real eval backend; do not wave a model through unevaluated. |
| `AgentRegistryPort` / `ToolCatalogPort` | `adapters/onprem/registry.py`, `tool_catalog.py` | An on-prem catalog. |
| `AgentRuntimePort` / `SessionPort` / `MemoryPort` | `adapters/onprem/runtime.py`, `session.py`, `memory.py` | On-prem hosting / state / memory. |

## Migration checklist

1. Stand up the on-prem backends (parser, LLM, guardrail, redactor, audit store).
2. Implement each `adapters/onprem/*` method against those backends. Keep the constructor
   signature `def __init__(self, settings: Settings) -> None`.
3. Run `make test` under `LOAN_DOC_PROFILE=onprem`: the contract + unit suites must stay green.
   No `domain/` change should be needed.
4. Run `make eval` to confirm the deterministic validation still meets the promotion bar.
5. Point `config/settings.yaml` (or a new settings file via `LOAN_DOC_SETTINGS`) at the
   on-prem endpoints and deploy.

Because the deterministic cross-validation lives entirely in `domain/cross_validator.py` with
no external dependency, the heart of Doc5 : the part that actually decides PASS / WARN / FAIL :
runs identically on any platform.
