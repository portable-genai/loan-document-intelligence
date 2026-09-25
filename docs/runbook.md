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

## Runtime controls

`LOAN_DOC_GUARDRAIL`, `LOAN_DOC_PII_REDACTION` and `LOAN_DOC_REVIEW_ROUTING` each switch one
cheap control: the guardrail port (Model Armor under `gcp`, `agent-guardrail-gateway` under
`platform`, the heuristic locally), the redaction port (DLP under `gcp`, the gateway under
`platform`, regex locally) and the review hand-off to `human-review-console`. Each is read once
at startup in three states: unset is on, `true`/`false` (or `on`/`off`, `1`/`0`, `yes`/`no`)
wins, and an emptied or unrecognised value refuses to boot, naming the variable. Off binds an
adapter that does nothing, and a process with any control off logs one warning at startup
naming each. Terraform sets all three on the Cloud Run service from `guardrail_enabled`,
`pii_redaction_enabled` and `review_routing_enabled` (default `true`), `HUMAN_REVIEW_URL`
from `human_review_url` and `HUMAN_REVIEW_IAP_AUDIENCE` from `human_review_iap_audience`, both
of which it requires while routing is on.

**The review hand-off goes through the portal's IAP edge.** A deployed `human-review-console` is
an embedded app behind the portal, so under `gcp` `HUMAN_REVIEW_URL` is
`https://<edge-host>/apps/human-review-console/api` and `HUMAN_REVIEW_IAP_AUDIENCE` names the
deployment's IAP OAuth client id. The router mints a Google-signed ID token for that audience
with the service's own identity on every submission, in place of the static `S2S_TOKEN`. The
console accepts it only if its `REVIEW_IAP_SERVICE_CALLERS_JSON` lists this service's account;
otherwise it answers 403 and the case says `review_routing: "failed"`.

Under `gcp` or `platform`, a control that is on must be able to work, so the process refuses to
boot when:

- review routing is on and `HUMAN_REVIEW_URL` is not set, or, under `gcp`, either it or
  `HUMAN_REVIEW_IAP_AUDIENCE` is not set (the refusal names both). Name them, or set
  `LOAN_DOC_REVIEW_ROUTING=off` to run without routing. Unsetting either variable does not
  pause routing; the switch does.
- `HUMAN_REVIEW_IAP_AUDIENCE` is emptied, or holds the `/projects/.../backendServices/...` path
  rather than the IAP OAuth client id (the edge refuses that path as a bearer audience).
- the guardrail is on, bound to Model Armor, and the template id is empty. Name one, or set
  `LOAN_DOC_GUARDRAIL=off`.

What the controls did is on the case a user reads. `POST /v1/process` carries
`review_routing`: `routed` (the console accepted the case), `failed` (the hand-off failed and
the case is NOT in the console; logged at WARNING with the exception type, and the response
still returns), `off` (routing is switched off) or `not_required`; the agent's
`process_application` tool and the CLI's `process` command report the same value. It also
carries `input_redacted: true` when redaction changed the application before the model saw it.
The console shows both beside the human-review banner.

The DLP inspect config (inline, and the Terraform inspect template) masks only `LIKELY`
findings and excludes loan-file vocabulary (tax authorities, lenders, document types,
employer suffixes such as `Pte Ltd`) from `PERSON_NAME`; both the inline config and the
Terraform de-identify template replace a match with its info-type name (`[PERSON_NAME]`)
rather than a run of `#`. The local redactor leaves an eight-digit amount after a currency
code (`SGD 90000000`) intact rather than masking it as a phone number.

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
- **Boot fails: "Review routing is on under profile 'gcp', which reaches the
  human-review-console through the portal's IAP edge, so it needs both HUMAN_REVIEW_URL ... and
  HUMAN_REVIEW_IAP_AUDIENCE"**: name the console's edge path and the IAP OAuth client id, or set
  `LOAN_DOC_REVIEW_ROUTING=off` to run without routing.
- **Boot fails: "HUMAN_REVIEW_IAP_AUDIENCE must be the IAP OAuth client id"**: the
  backend-service path was pasted as the audience; use the OAuth client id.
- **A case carries `review_routing: "failed"`**: the console was unreachable or refused the
  hand-off and the case is NOT queued for review. Read the WARNING "human-review hand-off
  failed: <exception type>", fix the console or credentials, and re-run the case.
