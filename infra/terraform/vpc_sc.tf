# vpc_sc.tf : VPC Service Controls perimeter around the AI/data APIs (P-03).
#
# General Principle map:
#   P-03 (residency / exfiltration control): a service perimeter around the AI and data
#         APIs prevents applicant data from being read or copied to a project outside the
#         perimeter, keeping it inside the Singapore boundary.
#
# Gated on var.enable_vpc_sc so a sandbox can skip it; production sets it true and supplies
# an Access Context Manager policy id.

resource "google_access_context_manager_service_perimeter" "loan_doc" {
  count = var.enable_vpc_sc ? 1 : 0

  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/loan_doc_intel"
  title  = "loan-document-intelligence-sg-perimeter"

  use_explicit_dry_run_spec = !var.vpc_sc_enforce

  dynamic "status" {
    for_each = var.vpc_sc_enforce ? [1] : []
    content {
      resources           = ["projects/${data.google_project.this.number}"]
      restricted_services = local.restricted_services
      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = ["RESTRICTED-SERVICES"]
      }
    }
  }

  dynamic "spec" {
    for_each = var.vpc_sc_enforce ? [] : [1]
    content {
      resources           = ["projects/${data.google_project.this.number}"]
      restricted_services = local.restricted_services
      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = ["RESTRICTED-SERVICES"]
      }
    }
  }

  depends_on = [google_project_service.required]
}

locals {
  restricted_services = [
    "aiplatform.googleapis.com",
    "documentai.googleapis.com",
    "dlp.googleapis.com",
    "logging.googleapis.com",
    "storage.googleapis.com",
  ]
}
