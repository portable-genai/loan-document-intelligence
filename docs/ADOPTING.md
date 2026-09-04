# Adopting this repo as your base

This repository is a **common base** that BFSI institutions (and other regulated industries)
fork to build their own document-diligence agents: income / affordability verification,
credit-memo review, trade-finance checking, insurance-claims triage, KYC / source-of-wealth.
It ships a reusable hexagonal core (a pure-stdlib domain, typed ports, swappable adapter
profiles, a green offline gate) plus a fully worked **loan / mortgage income-verification**
vertical you can keep, replace, or learn from.

This guide is the step-by-step for making it yours. It has two halves: a **mechanical
rebrand** (one script) and the **human decisions** the script cannot make for you.

> Related reading: [`ARCHITECTURE.md`](../ARCHITECTURE.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md)
> (adding a port / sub-service), the [`faq/`](faq/) directory, and [`docs/practices-audit.md`](practices-audit.md) (the
> per-check state of this base, including its known gaps).

---

## 1. What you keep vs what you rewrite

The domain is pure and stdlib-only, split by responsibility:

| Layer | Where | For a new vertical |
|---|---|---|
| **Kernel** (vertical-neutral) | `domain/kernel.py`, the citations / grounding machinery (`domain/_grounded.py`), the audit event and eval-report types, `Severity`, the reconciliation-engine mechanics, `domain/serialization.py`, and the generic ports (extraction, generation, safety, governance, identity, observability, runtime) | keep untouched |
| **Policy** (your numbers) | the validation thresholds (amount tolerance, affordability warn / fail ratios, balance-decline bands) | change by config once B4 lands; for now these are module constants in `domain/cross_validator.py` (see the note below) |
| **Vertical** (loan / income artifacts) | `domain/models.py`, `domain/cross_validator.py` checks, `domain/income_service.py`, `domain/prompts.py`, the local fixtures, the eval golden set, the UI summary views | rewrite for your artifacts |

`domain/kernel.py` is the kernel import surface AND the place those definitions live: it imports
nothing from this package, and `domain/models.py` imports it (re-exporting every kernel name for
backward compatibility). So the arrow runs vertical to kernel, never the reverse, and a fork can
lift `kernel.py` plus the ports typed against it and rewrite `models.py` without editing a kernel
line. `tests/unit/test_kernel_boundary.py` holds that direction in place by executing it: a fresh
interpreter importing the kernel must never pull the vertical module into `sys.modules`, which is
what audit check A7 verifies.

If your product is another *document-diligence* vertical, most of the domain and the
deterministic reconciliation engine transfer directly; you replace the artifact models and
the prompts, and retune the validation policy and taxonomy.

## 2. Core-vs-adopter-owned files (so upstream merges stay mechanical)

Upstream keeps evolving these; avoid diverging from them so you can pull fixes cleanly:

- **Upstream-owned** (take our changes): `ports/`, `tests/contract/`, the eval harness
  (`eval/run_eval.py` mechanics), CI workflows, and the hexagon wiring (`config.py`
  `Container`).
- **Adopter-owned** (yours; expect to edit): `config/settings.yaml` *values*, the local
  fixtures and the `examples/` synthetic applications, `adapters/onprem/*`, UI theming /
  branding, the golden eval dataset (`eval/datasets/`), and the `COMPLIANCE.md` jurisdiction
  rows.

Track upstream via git tags; rebase your adopter-owned
changes onto each release rather than merging `main` continuously.

## 3. The mechanical rebrand (one script)

`scripts/rename_fork.py` rewrites the package name, CLI entry point, `LOAN_DOC_` env prefix,
and resource / distribution ids across the tree in one pass. In this repo the distribution
name, the CLI name and the resource-id stem are the same string (`loan-document-intelligence`), so
`--dist` defaults to `--resource` and a fork normally sets all three to one new value.
Preview first, then apply:

```bash
# Preview (writes nothing):
python scripts/rename_fork.py --package acme_income_agent --cli acme-income \
    --env-prefix ACME --resource acme-income-agent --dry-run

# Apply:
python scripts/rename_fork.py --package acme_income_agent --cli acme-income \
    --env-prefix ACME --resource acme-income-agent --yes

# Then recreate the environment (the distribution name changed) and prove it is green:
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make lint test eval
```

Add `--include-docs` to sweep Markdown prose too. Pass `--dist` only if you deliberately want
a distribution name that differs from the resource stem. The script deliberately does NOT
touch the human decisions below.

## 4. The human decisions (the script can't make these)

1. **Region / residency.** Set `LOAN_DOC_REGION` (now `<PREFIX>_REGION`) and the Terraform
   `region` / tfvars to your in-country region. The build defaults to `asia-southeast1`
   (Singapore). See [`docs/runbook.md`](runbook.md).
2. **Identity / IdP.** This repo owns no login flow: identity is delegated to Cloud IAP
   (`gcp` / `platform`), seeded dev personas (`local`), or an on-prem IdP placeholder
   (`onprem`). Wire your IdP / IAP config for the secure profiles. See
   [`docs/embedding-and-identity.md`](embedding-and-identity.md).
3. **PII / jurisdiction pack.** Set `pii.jurisdictions` (and `LOAN_DOC_PII_JURISDICTIONS`
   for the eval gate) so redaction and the `pii_safety` metric detect YOUR national
   identifiers, not just the bundled SG / HK / JP / AU set. Add a pattern pack to
   `domain/pii_patterns.py` if your jurisdiction is not yet listed, keeping the account-number
   row ahead of any tax-id row (see the row-order note in that module).
4. **Validation policy.** Own the numbers the `CrossValidator` uses (amount tolerance,
   affordability warn / fail ratios, balance-decline bands). Today these are module-level
   constants in `domain/cross_validator.py`; threading them from `config/settings.yaml` is
   tracked as audit check **B4**. Until it lands, edit them in code and treat the defaults as
   a reference, not your policy.
5. **Reference data is fictional.** Every fixture, the `examples/` applications, and the eval
   golden set use obviously-fake names. Swap them for your own synthetic data. **Do not run
   against live customer data without your own legal, security and model-risk sign-off.**
6. **Eval golden set.** Rebuild `eval/datasets/` and the rubrics for your vertical: a fork
   inherits a green gate that measures the WRONG thing until you do. The gate structure is
   generic; the golden cases are yours.
7. **Deployment posture.** Review the Dockerfile (digest-pinned base, non-root),
   `infra/terraform/` (region validation, CMEK, VPC-SC, WORM logging) and the
   loopback-by-default binding before you expose anything. Note the deploy hardening is not
   yet complete (no CI `terraform validate`, VPC-SC enforced rather than dry-run-first, no Org
   Policy resource-location resource), tracked as audit check **D5**.

## 5. Do not duplicate the platform

This repo is one system in a catalog of composable GRC systems. Several concerns it *touches*
are owned by sibling platform services, and you should integrate rather than rebuild them (see
[`docs/faq/features-faq.md`](faq/features-faq.md) for the full map): the guardrail gateway
(`agent-guardrail-gateway`), the agent registry (`agent-registry`), the AI-quality / eval gate (`model-quality-gate`),
observability + WORM audit (`agent-observability`), the human-review & maker-checker console (`human-review-console`),
architecture validation at intake (`architecture-validator`), and the on-prem DLP gate (`onprem-dlp`). The
`platform` profile's adapters are already thin HTTP clients to those services. This agent does
document extraction plus deterministic validation, **not** RAG over a corpus, so the governed
knowledge base (`enterprise-knowledge-base`) is **N/A**.

## 6. Adoption checklist

- [ ] Ran `scripts/rename_fork.py`, recreated the venv, `make lint test eval` green.
- [ ] Set region + Terraform tfvars to your in-country region.
- [ ] Wired your IdP / IAP for the secure profiles.
- [ ] Set `pii.jurisdictions` + added a pattern pack if needed; `pii_safety` exercises your ids.
- [ ] Owned the validation numbers with your compliance / credit-risk function.
- [ ] Replaced every synthetic fixture and the `examples/` applications.
- [ ] Rebuilt the eval golden set + rubrics for your vertical.
- [ ] Reviewed the deploy posture (Dockerfile, Terraform, bind address).
- [ ] Decided which sibling platform services you integrate vs stub.
- [ ] Recorded your baseline upstream tag so you can take future fixes.
