# `loan-document-intelligence` operations runbook

Operational notes for running and deploying the `loan-document-intelligence` Loan / Mortgage Document Intelligence
service. This is a reference build; adapt to your own environment, change-management and
on-call processes.

## Profiles

| Profile | Use | Needs GCP SDK |
| --- | --- | --- |
| `local` | functional offline dev, CI and demo stack | no |
| `platform` | inside the full platform (delegates to `agent-guardrail-gateway`, `agent-registry`, `model-quality-gate`, `agent-observability`) | only for the gcp-bound ports |
| `gcp` | standalone managed deployment | yes (`pip install -e ".[gcp,dev]"`) |
| `onprem` | fail-fast adopter seam until sovereign adapters are supplied | no |

Select with `LOAN_DOC_PROFILE`, or write a `profile:` into `config/settings.yaml`; tests and CI
run `local`. Production sets `LOAN_DOC_PROFILE=gcp` explicitly (see the `Dockerfile`).

**Unset is a third state, not a synonym for `local`.** When neither the variable nor the
settings file names a profile, the SDK-free `local` adapters still bind (nothing else can, with
no cloud SDK installed) but the run counts as unconsented: the seeded no-auth personas are
refused, the localhost CORS fallback is empty, and the bind guard still confines the process to
loopback. A dev or demo run must therefore name `local` deliberately. This is what stops a
missing environment variable from serving retail-lending underwriting with dev loan approvers.

## Local run

```bash
make install            # dev deps only, no GCP SDK
LOAN_DOC_PROFILE=local loan-document-intelligence --help
LOAN_DOC_PROFILE=local make test
LOAN_DOC_PROFILE=local make eval
```

The local profile exercises the real UI, API, orchestration, deterministic validation and
hash-chained audit with SDK-free adapters. Under `onprem` the placeholders instead fail clearly;
that profile proves the exit boundary, not a completed sovereign deployment.

## Deploy (gcp profile)

1. Provision infra: validate and review a plan before any apply (see
   `infra/terraform/README.md`). Production sets `production_mode = true`; disposable demos keep
   irreversible retention locking and deletion protection off.
2. Copy the Terraform outputs into `config/settings.yaml` (or the matching `LOAN_DOC_*` env
   vars): `document_ai_processor_id`, `dlp_inspect_template`, `dlp_deidentify_template`,
   `kms_crypto_key`.
3. Build and push the image (`Dockerfile`) to Artifact Registry in `asia-southeast1`.
4. The Cloud Run service from `agent_runtime.tf` runs the API on port 8092 as the
   least-privilege runtime service account.
5. Deploy the ADK root agent to Agent Runtime out of band with the Agent Platform SDK (see the
   deploy snippet in `src/loan_doc_intel/agent/root_agent.py`), then set
   `agent_engine.resource_name` in settings.

## Residency and key rotation

- **Region** is pinned to `asia-southeast1` everywhere; the Terraform `region` variable is
  validated to reject any other value. Do not add a region override env var.
- **CMEK** keys rotate every 90 days (`infra/terraform/kms.tf`). CMEK does not cascade, so
  each service agent has its own key binding; when adding a service that encrypts data, add a
  binding.
- The **WORM log bucket** lock is irreversible. Set `retention_days` deliberately (default
  2557, ~7 years); it cannot be shortened after the lock.

## Health and observability

- `GET /healthz` reports `{status, profile, region}` for liveness/readiness.
- Trace spans carry no message content (P-09); applicant PII never reaches a span.
- Audit records are written already redacted to the locked WORM bucket; query by the
  structured labels (`action`, `actor`, `decision`).

## Incident notes

- **A guardrail block** is not an error: the API returns a 200 case flagged for human review.
  Investigate the audit `Decision.BLOCKED` records if blocks spike.
- **An INCONSISTENT verdict** is the expected output for an applicant whose documents do not
  reconcile; it escalates the audit decision and surfaces red flags for the underwriter. It is
  not a service failure.
- **Onprem `NotImplementedError`** at runtime means a port is bound to a placeholder; check
  the active profile and the `adapters:` map.
