# Deploy-time sovereignty guardrails. Disabled for projects where the caller lacks
# organization-policy authority; production_mode requires them to be enabled.
resource "google_org_policy_policy" "resource_locations" {
  count  = var.enable_org_policies ? 1 : 0
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        allowed_values = [for region in sort(tolist(var.allowed_regions)) : "in:${region}-locations"]
      }
    }
  }
}

resource "google_org_policy_policy" "disable_service_account_key_upload" {
  count  = var.enable_org_policies ? 1 : 0
  name   = "projects/${var.project_id}/policies/iam.disableServiceAccountKeyUpload"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }
}

resource "google_org_policy_policy" "disable_service_account_key_creation" {
  count  = var.enable_org_policies ? 1 : 0
  name   = "projects/${var.project_id}/policies/iam.disableServiceAccountKeyCreation"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }
}
