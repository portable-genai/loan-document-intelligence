---
type: Deployment Dossier
title: Doc5 named GCP deployment dossier
description: Non-secret decisions and evidence required before claiming a hosted Doc5 onboarding demonstration.
status: draft
---

# Doc5 named GCP deployment dossier

This file separates repository readiness from a real hosted deployment. It contains no secrets.
Terraform validation and local browser evidence do not satisfy any live-evidence row.

## Observed cloud state

`PENDING`: no organization, project or workstation identity is named yet. Record here, when
available, which projects the deployment identity can describe, whether the target region's
Cloud Run service list is empty, and whether the Cloud Run API is enabled, before any resource
is changed.

## Installation inputs

| Input | Current state | Completion evidence |
| --- | --- | --- |
| Target project and owner | PENDING: choose or create an approved project under the deployment organization | recorded decision and operator |
| Reviewed API and UI image digests | PENDING | release digest artifact, SBOM, scan and signature |
| Terraform state backend | PENDING | regional GCS backend and installation prefix |
| IAP audience and approved users | PENDING | client registration and access test |
| Alert notification channel | PENDING | tested delivery route |
| VPC-SC access policy | PENDING | dry-run perimeter and reviewed violation window |
| Retention lock approval | PENDING | explicit approval before irreversible lock |
| Synthetic demo objects | PENDING | reviewed PDFs in a regional GCS bucket |
| Demo entitlements | PENDING | server-side owners for the application and two document IDs |
| Portal parent origin | PENDING | exact Hrz9 HTTPS origin in frame policy |

## Required evidence sequence

1. Publish and review immutable API and UI digests.
2. Run Terraform init against the approved backend, then retain fmt, validate and plan output.
3. Apply the reversible demo posture with VPC-SC in dry-run and the audit bucket unlocked.
4. Upload only clearly synthetic demo documents and seed server-side entitlements.
5. Prove IAP, health profile/region, the Doc5 artifact, citations, review escalation and audit
   correlation through Hrz9's hosted RM route.
6. Exercise rollback to reviewed prior digests.
7. Promote to `production_mode = true` only after alerting, perimeter, retention and deletion
   controls have their named approvals.

Until every relevant row is complete, the correct claim is “local and deployable by validated
code,” not “running on GCP.”
