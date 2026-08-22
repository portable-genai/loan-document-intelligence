# variables.tf : the only knobs. Everything else is a concrete in-region value.
#
# General Principle map:
#   P-03 (residency): `region` defaults to asia-southeast1 and is validated so a caller
#         cannot accidentally point this stack at a non-Singapore region.
#   P-08 (auditability/retention): `retention_days` is a Terraform variable (the WORM
#         bucket lock is irreversible, so retention must be deliberate).
#
# Per the build contract, ONLY project_id and a few genuinely per-tenant values (org/billing
# ids, the VPC-SC toggle) are variables. All service identifiers, locations, and template
# names are concrete.

variable "project_id" {
  description = "Target GCP project id (required). Single-tenant, Singapore-resident."
  type        = string
}

variable "api_image" {
  description = "Reviewed Doc5 API image pinned by sha256 digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
    error_message = "api_image must use an immutable @sha256 digest."
  }
}

variable "allowed_regions" {
  description = "Institution-approved residency allowlist."
  type        = set(string)
  default     = ["asia-southeast1"]

  validation {
    condition     = var.allowed_regions == toset(["asia-southeast1"])
    error_message = "allowed_regions must contain only asia-southeast1 for this workload."
  }
}

variable "region" {
  description = "Deployment region. Pinned to Singapore; validated to fail fast (P-03)."
  type        = string
  default     = "asia-southeast1"

  validation {
    condition     = contains(var.allowed_regions, var.region)
    error_message = "region must be present in allowed_regions (P-03)."
  }
}

variable "zone" {
  description = "Default zone within Singapore for zonal resources."
  type        = string
  default     = "asia-southeast1-a"
}

variable "retention_days" {
  description = "WORM audit-log retention in days. Default ~7 years. Lock is irreversible."
  type        = number
  default     = 2557 # ~7 years; mirrors config/settings.yaml logging.retention_days

  validation {
    condition     = var.retention_days >= 2557
    error_message = "Compliance retention must be at least 2557 days (~7 years) (P-08)."
  }
}

variable "lock_audit_bucket" {
  description = "Irreversibly lock the audit bucket. Keep false for disposable demos; production requires true."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect the Cloud Run API from deletion. Production requires true."
  type        = bool
  default     = false
}

variable "production_mode" {
  description = "Require the complete production guardrail posture rather than disposable-demo defaults."
  type        = bool
  default     = false
}

variable "org_id" {
  description = "Organization id : required for Org Policy and Access Context Manager."
  type        = string
}

variable "billing_account" {
  description = "Billing account id (used by FinOps tagging)."
  type        = string
  default     = ""
}

variable "access_policy_id" {
  description = <<-EOT
    Existing Access Context Manager policy id (numeric, no prefix) for the org.
    Required when enable_vpc_sc = true; the service perimeter is created under it.
    Create once per org with:
      gcloud access-context-manager policies create \
        --organization=ORG_ID --title="sg-residency"
  EOT
  type        = string
  default     = ""
}

variable "vpc_network_name" {
  description = "Name of the VPC that hosts the private workload and PSA range."
  type        = string
  default     = "loan-doc-vpc"
}

variable "enable_vpc_sc" {
  description = "Create the VPC Service Controls perimeter around the AI/data APIs (P-03)."
  type        = bool
  default     = true

  validation {
    condition     = !var.enable_vpc_sc || can(regex("^[0-9]+$", var.access_policy_id))
    error_message = "access_policy_id must be numeric when enable_vpc_sc is true."
  }
}

variable "vpc_sc_enforce" {
  description = "Enforce the VPC-SC perimeter only after a clean dry-run."
  type        = bool
  default     = false
}

variable "enable_org_policies" {
  description = "Apply project resource-location and no-service-account-key policies."
  type        = bool
  default     = false
}

variable "alert_notification_channels" {
  description = "Monitoring channels for guardrail, SA-key, perimeter, and CMEK alerts."
  type        = list(string)
  default     = []
}

variable "iap_jwt_audience" {
  description = "Exact IAP audience verified by the API when hosted securely."
  type        = string
  default     = ""
}

variable "frame_ancestors" {
  description = "Exact hosted parent origins allowed to frame the UI."
  type        = set(string)
  default     = ["'self'"]
}

variable "cors_origins" {
  description = "Explicit standalone browser origins; same-origin embedding needs none."
  type        = set(string)
  default     = []
}
