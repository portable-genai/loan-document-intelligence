# Portability FAQ

For architecture, cloud-governance, and exit-planning teams. The claim this repo makes is
"no vendor lock-in" (General Principle P-02 / P-12), enforced by the port layer and the
contract tests. Cross-references: [`ARCHITECTURE.md`](../../ARCHITECTURE.md),
[`docs/onprem-migration.md`](../onprem-migration.md), [`COMPLIANCE.md`](../../COMPLIANCE.md)
(P-02, P-12).

### What does "portable" actually mean here?

Three axes: **compute** (the whole stack migrates by a one-line profile change, no domain
edits), **data** (the audit trail exports in an open, documented format and reloads
elsewhere with its integrity re-verified), and **experience / identity** (identity resolves
across hosts by an adapter swap, not a rewrite). The compute and identity axes are proven by
the contract-test suite today; a single offline exit-code-gating portability script that
walks all of them in one command is not yet built (audit check **F3**, see the honest gap
below).

### How does the profile switch work?

The pure-domain core speaks only to `@runtime_checkable` **ports** (`ports/*.py`); four
**adapter families** implement them, and `config/settings.yaml` binds one adapter per port
per profile. Setting `LOAN_DOC_PROFILE` (or `profile:` in the settings) rebinds the entire
stack:

- `local`: a WORKING offline stack (local document parser, deterministic LLM, regex DLP,
  heuristic guardrail, hash-chained SQLite audit). No Google Cloud SDK. The default for
  dev / test / CI.
- `gcp`: real managed services (Document AI, Gemini, Model Armor, Cloud DLP, Cloud Logging
  WORM, Cloud Trace, Gen AI Evals).
- `platform`: thin HTTP clients delegating to the sibling horizontal-platform services.
- `onprem`: fail-fast placeholder stubs that still satisfy every Protocol (the sovereign-exit
  target); a primary CLI command exits code 2 by design, naming the migration target.

No `domain/` code changes across any of these. `tests/contract/test_port_parity.py` proves
both `local` and `onprem` construct and satisfy every port with **no cloud SDK installed**,
with a set-equality drift guard (`test_port_protocols_matches_settings_adapters`) that fails
in both directions; `tests/contract/test_behavioral_parity.py` proves same-request parity
across `local` and `platform` for representative ports (audit check A6).

### How do we get our data out?

The `local` audit store wraps `hex_service_kit.audit.HashChainedAuditLog`, which supports
**JSONL export / restore**: the chain reloads into a fresh store and is re-verified line by
line (`verify_chain()`), so the exit story for the audit trail is "copy the JSONL file", not
"migrate a product". Domain artifacts (`LoanApplicationCase`, `CrossValidationResult`,
`IncomeVerificationSummary`) serialize through `domain/serialization.py` (`to_jsonable`) with
enum members as their string wire values, so a case rehydrates without the originating stack.

### Is on-prem / sovereign deployment real or aspirational?

The `onprem` adapters are deliberate fail-fast placeholders (they raise
`NotImplementedError`) that nonetheless satisfy every Protocol and construct with a single
`Settings` arg, so the *interface contract* for a sovereign migration is proven and enforced
by CI today. The actual on-prem implementations are the migration work, scoped in
[`docs/onprem-migration.md`](../onprem-migration.md) (Google Distributed Cloud, zero domain
changes). This repo is not the sovereign-exit *planner*, that is the sibling **the exit-and-portability planner Exit &
Portability Planner**; this repo is one of the systems whose exit that planner reasons about.

### Does the kernel/vertical split affect portability?

The domain is pure and stdlib-only (no cloud SDK, no framework reaches it), so the port
layer and the reconciliation engine transfer to a fork for free. One honest caveat: the
kernel-vs-vertical boundary is currently a **documented convention, not a separate
`kernel.py` module** (the vertical-neutral machinery and the loan / income artifacts share
`domain/models.py`); this is tracked as audit check **A7 (PARTIAL)**. It does not weaken the
port-level portability guarantee, but a fork extracting a reusable kernel does that split
itself for now.

### Does residency compromise portability?

No: residency is a deploy-time pin (region `asia-southeast1` / Singapore, CMEK, VPC-SC),
and portability is the ability to change *where* the stack runs by configuration. They are
orthogonal, and the region is validated to fail fast. The residency-violation CI gate is the
sibling **the data-residency validator Data Residency / Sovereignty Validator**; the exit / concentration-risk plan
is **the exit-and-portability planner**. This repo enforces residency in its own infra and is one of the systems those
tools reason about. See [compliance-faq.md](compliance-faq.md) for the residency controls.

### What is NOT yet portable / proven?

- **The single executable portability demo (F3).** The profile-swap / port-parity /
  tamper-evident-audit / export-reload / identity-swap claim is proven piecewise by the
  contract tests, but there is no `scripts/portability_demo.py` that exit-code-gates all of
  it in one offline run yet.
- **On-prem adapters** are placeholders (above), so a full sovereign deployment is migration
  work, not a config flip.
