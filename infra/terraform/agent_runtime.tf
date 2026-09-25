# agent_runtime.tf : the Cloud Run host for the B5 API + the Agent Runtime registration.
#
# General Principle map:
#   P-01 (managed-first): the service runs as a managed Cloud Run service fronting the
#         FastAPI app; the ADK root agent is deployed to Agent Runtime out of band with the
#         Agent Platform SDK (see src/loan_doc_intel/agent/root_agent.py).
#   P-03 (residency): the Cloud Run service is regional (asia-southeast1).
#   P-10 (least privilege): it runs as the dedicated runtime service account from iam.tf.
#
# The container image is built and pushed out of band (see Dockerfile); this resource pins
# where and how it runs.

resource "google_cloud_run_v2_service" "api" {
  name     = "loan-document-intelligence"
  location = var.region # asia-southeast1 : in-country compute (P-03)
  project  = var.project_id

  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = var.deletion_protection

  template {
    service_account = google_service_account.runtime.email
    encryption_key  = one(google_kms_crypto_key.loan_doc[*].id)

    containers {
      image = var.api_image

      ports {
        container_port = 8092
      }

      env {
        name  = "LOAN_DOC_PROFILE"
        value = "gcp"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "LOAN_DOC_DOCAI_PROCESSOR"
        value = google_document_ai_processor.loan_docs.id
      }
      env {
        name  = "LOAN_DOC_DLP_INSPECT_TEMPLATE"
        value = google_data_loss_prevention_inspect_template.applicant_pii.id
      }
      env {
        name  = "LOAN_DOC_DLP_DEIDENTIFY_TEMPLATE"
        value = google_data_loss_prevention_deidentify_template.applicant_pii.id
      }
      env {
        name  = "LOAN_DOC_KMS_KEY"
        value = one(google_kms_crypto_key.loan_doc[*].id)
      }
      env {
        name  = "LOAN_DOC_IAP_AUDIENCE"
        value = var.iap_jwt_audience
      }
      env {
        name  = "LOAN_DOC_FRAME_ANCESTORS"
        value = join(" ", sort(tolist(var.frame_ancestors)))
      }
      # The cheap runtime controls, stated rather than inherited: on in the reference, and off
      # is a deployment choice the service logs at startup.
      env {
        name  = "LOAN_DOC_GUARDRAIL"
        value = tostring(var.guardrail_enabled)
      }
      env {
        name  = "LOAN_DOC_PII_REDACTION"
        value = tostring(var.pii_redaction_enabled)
      }
      env {
        name  = "LOAN_DOC_REVIEW_ROUTING"
        value = tostring(var.review_routing_enabled)
      }
      # Rule R8: the console an escalated case is routed to. Set only when it carries a value:
      # the service reads it in three states and refuses an emptied one, and with routing on it
      # refuses to boot without one (variables.tf validates the same).
      dynamic "env" {
        for_each = trimspace(var.human_review_url) != "" ? [1] : []
        content {
          name  = "HUMAN_REVIEW_URL"
          value = var.human_review_url
        }
      }
      # The IAP OAuth client id the review hand-off bearer is minted for: the console is an
      # embedded app behind the portal's IAP edge. Set only when it carries a value, for the
      # same three-state reason as HUMAN_REVIEW_URL; with routing on the service refuses to
      # boot without it (variables.tf validates the same).
      dynamic "env" {
        for_each = trimspace(var.human_review_iap_audience) != "" ? [1] : []
        content {
          name  = "HUMAN_REVIEW_IAP_AUDIENCE"
          value = var.human_review_iap_audience
        }
      }
      dynamic "env" {
        for_each = length(var.cors_origins) > 0 ? [1] : []
        content {
          name  = "LOAN_DOC_CORS_ORIGINS"
          value = join(",", sort(tolist(var.cors_origins)))
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }

  depends_on = [
    google_kms_crypto_key_iam_member.runtime_cmek,
    google_project_service.required,
  ]
}
