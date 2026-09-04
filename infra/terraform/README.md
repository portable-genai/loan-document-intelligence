# `loan-document-intelligence` infrastructure (Terraform, asia-southeast1)

Concrete, Singapore-resident infrastructure for the `loan-document-intelligence` Loan / Mortgage Document
Intelligence service. Only `project_id`, `org_id` and a few genuinely per-tenant values are
variables; every service identifier, location and template name is a concrete in-region
value, so the stack is auditable and reproducible.

Do NOT run `terraform apply` from CI: this is the deploy-time stack, provisioned once per
environment by a platform engineer.

## Resources

| File | Provisions | Principle |
| --- | --- | --- |
| `providers.tf` | google / google-beta providers, pinned to asia-southeast1 | P-03 |
| `variables.tf` | the only knobs (region validated to Singapore) | P-03, P-08 |
| `apis.tf` | enables Document AI, DLP, Model Armor, Vertex AI, Logging, Trace, KMS, ACM | P-01 |
| `kms.tf` | regional CMEK key ring + per-service-agent bindings (CMEK does not cascade) | P-09 |
| `document_ai.tf` | the Document AI processor that parses applicant documents | P-01, P-03 |
| `dlp.tf` | inspect + de-identify templates (SG NRIC, bank account custom detectors) | P-04 |
| `model_armor.tf` | the guardrail template (PI/jailbreak, malicious URI, RAI) | P-04, P-05 |
| `logging_worm.tf` | the locked WORM audit bucket (~7-year retention) + sink | P-07, P-08 |
| `iam.tf` | the least-privilege runtime service account | P-10 |
| `vpc_sc.tf` | the VPC Service Controls perimeter around the AI/data APIs | P-03 |
| `org_policy.tf` | regional location allowlist and prohibition of service-account keys | P-03, P-06 |
| `monitoring.tf` | alerts for guardrail blocks, key creation, perimeter denials and CMEK changes | P-07 |
| `checks.tf` | rejects an incomplete guardrail set when `production_mode = true` | production gate |
| `agent_runtime.tf` | the Cloud Run host for the API (regional) | P-01, P-03 |
| `outputs.tf` | the ids the app/operators wire back into `config/settings.yaml` | n/a |

## Usage

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in project_id, org_id, ...
terraform init
terraform plan
terraform apply
```

The example is intentionally a disposable-demo posture: VPC-SC starts in dry-run and the
audit bucket is not irreversibly locked. For production, set `production_mode = true`,
enable Org Policy, promote VPC-SC after reviewing dry-run violations, approve the retention
lock, enable deletion protection, configure IAP, and attach at least one alert channel.
Terraform refuses production mode unless all of those choices are explicit. Images must be
pinned by `@sha256:` digest; the release workflow publishes the reviewed API and UI digests.

After apply, copy the outputs (`document_ai_processor_id`, `dlp_inspect_template`,
`dlp_deidentify_template`, `kms_crypto_key`) into the matching keys in
`config/settings.yaml` (or the corresponding `LOAN_DOC_*` environment variables).

## Residency note

Every resource is pinned to `asia-southeast1` (Singapore). The `region` variable is
validated to reject any other value, the WORM bucket and CMEK key are regional, and the
VPC-SC perimeter restricts the AI/data APIs so applicant data cannot leave the boundary.
The dry-run-first switch makes perimeter promotion observable rather than a blind cutover.
