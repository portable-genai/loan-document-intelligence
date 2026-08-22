# model_armor.tf : the Model Armor guardrail template for B5 (A1, R1).
#
# General Principle map:
#   P-04 / P-05 (safety at the model boundary): every prompt and response is screened for
#         prompt injection, jailbreak, sensitive-data leakage, malicious URIs and RAI
#         categories before/after the model call.
#   P-03 (residency): the template is regional (asia-southeast1) so screening stays in-country.
#
# The app's Model Armor adapter references this template id via settings (model_armor.template_id)
# and screens on the regional endpoint modelarmor.asia-southeast1.rep.googleapis.com.

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

    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
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

  depends_on = [google_project_service.required]
}
