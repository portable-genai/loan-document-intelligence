# dlp.tf : Sensitive Data Protection (DLP) templates for applicant-PII redaction (R1, P-04).
#
# General Principle map:
#   P-04 (minimise data to model): applicant PII (names, addresses, NRIC, bank account
#         numbers, income) is de-identified before it reaches a model or the WORM audit sink.
#   P-03 (residency): templates are regional (asia-southeast1) so inspection stays in-country.
#
# The app's DLP redaction adapter references these template ids via settings (dlp.inspect_template
# / dlp.deidentify_template). Custom info types cover the SG NRIC/FIN and a bank-account pattern
# not in the built-in detector set.

# Inspect template : what to detect.
resource "google_data_loss_prevention_inspect_template" "applicant_pii" {
  parent       = "projects/${var.project_id}/locations/${var.region}"
  display_name = "loan-document-intelligence-inspect"
  description  = "Applicant PII detectors for retail-lending documents (B5)."

  inspect_config {
    dynamic "info_types" {
      for_each = ["PERSON_NAME", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD_NUMBER", "IBAN_CODE", "STREET_ADDRESS"]
      content {
        name = info_types.value
      }
    }

    custom_info_types {
      info_type {
        name = "SG_NRIC_FIN"
      }
      likelihood = "POSSIBLE"
      regex {
        pattern = "[STFGM][0-9]{7}[A-Z]"
      }
    }

    custom_info_types {
      info_type {
        name = "BANK_ACCOUNT_NUMBER"
      }
      likelihood = "POSSIBLE"
      regex {
        pattern = "[0-9]{3}-[0-9]{6}-[0-9]"
      }
    }

    min_likelihood = "POSSIBLE"
  }

  depends_on = [google_project_service.required]
}

# De-identify template : mask every detected info type (irreversible character mask).
resource "google_data_loss_prevention_deidentify_template" "applicant_pii" {
  parent       = "projects/${var.project_id}/locations/${var.region}"
  display_name = "loan-document-intelligence-deidentify"
  description  = "Mask applicant PII before model/audit (B5, P-04)."

  deidentify_config {
    info_type_transformations {
      transformations {
        primitive_transformation {
          character_mask_config {
            masking_character = "#"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.required]
}
