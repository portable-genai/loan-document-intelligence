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
  description = "Reviewed loan-document-intelligence API image pinned by sha256 digest."
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

variable "worm_locked" {
  description = "Irreversibly lock the audit bucket. NO default: a plan refuses until the deployment states it. false keeps a disposable demo destroyable; production requires true."
  type        = bool
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

variable "resource_location_values" {
  description = <<-EOT
    Value groups for the gcp.resourceLocations Org Policy. Empty (the default) derives the
    strictest form from the deploy region: that region and its sub-locations, nothing else.

    Widen it ONLY where a service this stack genuinely needs has no presence at single-region
    granularity, and treat the width as the residency claim rather than as plumbing. Two
    services in this catalog force the question:

      * Agent Search serves `global`, `us` and `eu` and NO Cloud region at all.
      * Document AI serves the deploy region only once Google grants single-region access,
        and routes to the `us` multi-region until then.

    Move to the smallest value group that still describes ONE JURISDICTION -- `in:us-locations`
    keeps every resource inside the United States -- and state the residency claim at that
    granularity rather than pretending it is still single-region. NEVER list an individual
    foreign region to unblock one service: that turns a jurisdiction boundary into a list of
    exceptions nobody can reason about.

    NOT YET VERIFIED BY EXECUTION: whether a `global` Agent Search data store is subject to
    this constraint at all, or is exempt as a global resource. Confirm at first apply and
    record the answer rather than guessing; the failure mode if it IS subject is an apply
    error naming discoveryengine, which is the good kind of failure.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for value in var.resource_location_values : startswith(value, "in:") || startswith(value, "is:")])
    error_message = "Each value must be an Org Policy location value group (in:...) or a literal location (is:...)."
  }
}

variable "docai_location" {
  description = <<-EOT
    Where the Document AI processor is CREATED. Deliberately NOT var.region.

    Document AI does not serve every Cloud region, and creating a processor in one it does not
    serve 404s at apply. It DOES serve asia-southeast1 -- and serves no us-central1 endpoint at
    all -- but Singapore is "limited support": a subset of processors, several in Preview, and
    access is gated behind Google's Document AI Single Region Request Form. Until that request
    is granted this routes to the `us` MULTI-REGION, which is a stated residency deviation:
    document bytes are extracted in the United States while the rest of the stack stays in
    region. Set this to asia-southeast1 the day access lands.

    Keep it equal to the runtime's LOAN_DOC_DOCAI_LOCATION, which selects the same location for
    the adapter. If the two disagree, Terraform creates the processor in one location and the
    adapter looks for it in another, and the failure surfaces as a confusing 404 at request
    time rather than at apply.

    `us` and `eu` are multi-regions, not `global`: each names ONE jurisdiction. Never widen
    this to a location the service does not serve just to make an apply succeed. Whichever is
    chosen, gcp.resourceLocations must be wide enough to permit it (see var.resource_location_values), and the
    residency claim must be stated at that width rather than at var.region's.
  EOT
  type        = string
  default     = "us"

  validation {
    # Mirrors the runtime rule: the deploy region, or a NAMED multi-region. `global` is refused
    # by name because it names no jurisdiction, and so is any other single region -- an
    # out-of-region single region would be a silent jurisdiction change dressed as a fix.
    condition     = contains(["us", "eu"], var.docai_location) || var.docai_location == var.region
    error_message = "docai_location must be the deploy region (var.region) or a named Document AI multi-region (us, eu). `global` names no jurisdiction and is refused."
  }
}

variable "posture_alerts_enabled" {
  type        = bool
  default     = false
  description = <<-EOT
    Whether this stack creates the posture alert policies and the log-based metrics behind
    them. False by default. Cloud Monitoring bills every metric-based alert condition, and a
    reference deployment that nobody pages gains nothing from them: the signals still land in
    Cloud Logging, where an operator can read them. Set true in a deployment with an on-call
    rota to notify, in that deployment's own tfvars.
  EOT
}

variable "cmek_enabled" {
  type        = bool
  default     = false
  description = <<-EOT
    Whether this stack creates its own Cloud KMS key ring and key and binds every store, log
    bucket and revision to it. False by default, and the default is the point: a key ring can
    never be deleted, a log bucket that has CMEK can never drop it, and registries and document
    stores take their key at creation. None of that changes an answer or a screen, and every
    resource is encrypted at rest with Google-managed keys regardless. A deployment with a
    customer whose data it must be able to shred, whose key access must be audited, or whose
    keys must live in an HSM sets this true in its own tfvars BEFORE its first apply. Flipping
    it off on a stack that already applied it is refused by the keys' prevent_destroy, which is
    the right answer: the stores it bound stay bound.
  EOT
}

variable "guardrail_enabled" {
  description = "Switch the input and output guardrail (LOAN_DOC_GUARDRAIL). A cheap runtime control: on in the reference, reversible, so it takes a default."
  type        = bool
  default     = true
}

variable "model_armor_full_capabilities" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether the guardrail template asks for the capabilities that are not served in every
    region: the malicious-URI filter.

    True by default, because a deployment should get the whole guardrail unless it has a
    reason not to. asia-southeast1 does not serve it, and Model Armor does not degrade -- it
    refuses the template with CAPABILITY_NOT_SUPPORTED, so the stack does not deploy at all.
    A deployment there sets this false, which narrows the guardrail and is a disclosure to
    make in deployment-posture.md rather than a silent downgrade.
  EOT
}

variable "pii_redaction_enabled" {
  description = "Switch PII redaction (LOAN_DOC_PII_REDACTION). A cheap runtime control: on in the reference, reversible, so it takes a default."
  type        = bool
  default     = true
}

variable "review_routing_enabled" {
  description = "Switch review routing to the human-review-console (LOAN_DOC_REVIEW_ROUTING). A cheap runtime control: on in the reference, reversible, so it takes a default."
  type        = bool
  default     = true
}

variable "human_review_url" {
  description = "Base URL of the human-review-console an escalated case is routed to (rule R8). No default: a deployment names its console, or states routing off and sets this to \"\"."
  type        = string

  validation {
    condition     = !var.review_routing_enabled || can(regex("^https://", var.human_review_url))
    error_message = "review_routing_enabled requires human_review_url (rule R8): the service refuses to boot with routing on and no console named. Name one, or set review_routing_enabled = false."
  }
}

variable "human_review_iap_audience" {
  description = "The audience the portal's IAP edge accepts for the review hand-off bearer: the deployment's IAP OAuth client id (HUMAN_REVIEW_IAP_AUDIENCE). The deployed console is an embedded app behind that edge, so the service mints an ID token for this audience per submission. NOT the /projects/.../backendServices/... path (that is iap_jwt_audience, for verifying inbound assertions). Required while review routing is on; \"\" when routing is stated off."
  type        = string
  default     = ""

  validation {
    condition     = !var.review_routing_enabled || trimspace(var.human_review_iap_audience) != ""
    error_message = "review_routing_enabled requires human_review_iap_audience: under gcp the service refuses to boot with routing on and no IAP OAuth client id to mint the console bearer for. Name it, or set review_routing_enabled = false."
  }
  validation {
    condition     = !startswith(var.human_review_iap_audience, "/projects/") && !strcontains(var.human_review_iap_audience, "/backendServices/")
    error_message = "human_review_iap_audience must be the IAP OAuth client id, not the backend-service path: IAP refuses that path as a bearer audience, and the service refuses it at boot."
  }
}
