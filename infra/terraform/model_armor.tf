# model_armor.tf : the Model Armor guardrail template for B5 (A1, R1).
#
# General Principle map:
#   P-04 / P-05 (safety at the model boundary): every prompt and response is screened for
#         prompt injection, jailbreak, sensitive-data leakage, malicious URIs (where the
#         region serves the filter) and RAI categories before/after the model call.
#   P-03 (residency): the template is regional (asia-southeast1) so screening stays in-country.
#
# The app's Model Armor adapter references this template id via settings (model_armor.template_id)
# and screens on the regional endpoint modelarmor.asia-southeast1.rep.googleapis.com.
#
# The malicious-URI filter is gated on var.model_armor_full_capabilities: asia-southeast1
# refuses a template that requests it, failing the very first apply with
# CAPABILITY_NOT_SUPPORTED. Same gated shape as credit-memo-drafting/infra/terraform/model_armor.tf.

resource "google_model_armor_template" "loan_doc" {
  provider    = google-beta
  location    = var.region # asia-southeast1 : in-country screening (P-03)
  template_id = "loan-doc-guardrail"
  project     = var.project_id

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "LOW_AND_ABOVE"
    }

    # Regional capability. asia-southeast1 does not serve it and refuses the template
    # outright with CAPABILITY_NOT_SUPPORTED, so a deployment there declines it EXPLICITLY
    # via the variable and discloses the narrowed guardrail. The default keeps it on, so a
    # region that does serve it gets it without having to ask.
    dynamic "malicious_uri_filter_settings" {
      for_each = var.model_armor_full_capabilities ? [1] : []
      content {
        filter_enforcement = "ENABLED"
      }
    }

    rai_settings {
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }
  }

  # Required by the API even though every field inside it is optional: creating the template
  # without this block succeeds, and the next apply then fails with "The 'template_metadata'
  # field is required" while trying to remove what the service itself populated. Neither
  # `terraform validate` nor the offline suite resolves the API's own field requirements, so
  # this is only ever found by applying twice.
  template_metadata {
    # Multi-language detection is a regional capability, refused the same way the malicious
    # URI filter is, so it follows the same variable and the same disclosure.
    dynamic "multi_language_detection" {
      for_each = var.model_armor_full_capabilities ? [1] : []
      content {
        enable_multi_language_detection = true
      }
    }

    # OFF, and this is the decision rather than the default. Sanitize-operation logs carry
    # the prompt text that was screened, and the prompt here is text extracted from a borrower's loan documents.
    # Copying it into ordinary operation logs would put that material outside the
    # CMEK-encrypted, access-controlled stores this stack exists to keep it in.
    log_sanitize_operations = false
  }

  depends_on = [google_project_service.required]
}
