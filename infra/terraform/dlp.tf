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
      likelihood = "VERY_LIKELY" # a shape match must clear the LIKELY floor below
      regex {
        pattern = "[STFGM][0-9]{7}[A-Z]"
      }
    }

    custom_info_types {
      info_type {
        name = "BANK_ACCOUNT_NUMBER"
      }
      likelihood = "VERY_LIKELY" # a shape match must clear the LIKELY floor below
      regex {
        pattern = "[0-9]{3}-[0-9]{6}-[0-9]"
      }
    }

    # Tuned against false positives (runtime-control contract, 2026-09-24): a loan file names
    # lenders, employers, tax authorities and document types, which POSSIBLE took for people.
    # Only LIKELY findings are masked, and a PERSON_NAME finding containing this domain's
    # vocabulary is excluded. Keep the pattern in step with adapters/gcp/dlp_redaction.py.
    rule_set {
      info_types {
        name = "PERSON_NAME"
      }
      rules {
        exclusion_rule {
          matching_type = "MATCHING_TYPE_PARTIAL_MATCH"
          regex {
            pattern = "(?i)\\b(MAS|IRAS|CPF|HDB|IRD|ATO|NTA|Inland Revenue|Notice of Assessment|Payslip|Pay Slip|Bank Statement|Employment Letter|TDSR|MSR|LTV|DBS|POSB|OCBC|UOB|HSBC|Citibank|Standard Chartered|Maybank|Hang Seng|Bank of China|Pte|Ltd|Limited|Sdn Bhd|Holdings|Payroll|Salary|Bonus|Allowance)\\b"
          }
        }
      }
    }

    min_likelihood = "LIKELY"
  }

  depends_on = [google_project_service.required]
}

# De-identify template : replace every detected info type with its name, e.g. "[PERSON_NAME]"
# (irreversible, and the model still reads the shape of the document).
resource "google_data_loss_prevention_deidentify_template" "applicant_pii" {
  parent       = "projects/${var.project_id}/locations/${var.region}"
  display_name = "loan-document-intelligence-deidentify"
  description  = "Mask applicant PII before model/audit (B5, P-04)."

  deidentify_config {
    info_type_transformations {
      transformations {
        primitive_transformation {
          replace_with_info_type_config = true
        }
      }
    }
  }

  depends_on = [google_project_service.required]
}
