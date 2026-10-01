# model_armor_capabilities.tftest.hcl : model_armor_full_capabilities is the one switch for the
# Model Armor capabilities a region may not serve, read as the PLANNED template rather than as
# template text.
#
# Why the file exists: asia-southeast1 refuses a template that asks for the malicious-URI filter
# or multi-language detection with CAPABILITY_NOT_SUPPORTED, so the very first apply fails. A
# deployment there sets the variable false, and nothing else proved the switch actually drops
# both blocks, or that it leaves the rest of the guardrail and the API-required
# template_metadata block in place. `terraform validate` cannot see any of that; a plan can.
#
# Mock providers and plan-only, so this runs with NO credentials and NO state, which is what the
# CI gate's terraform test step runs. Every value is fictional.

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id = "fictional-loan-doc-project"
  org_id     = "123456789012"
  api_image  = "asia-southeast1-docker.pkg.dev/fictional-loan-doc-project/apps/api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  # Named because it has no default; false keeps a plan from ever describing a locked bucket.
  worm_locked      = false
  human_review_url = "https://review.fictional-bank.example"
  # A managed deployment with routing on must name the IAP client the console bearer is minted
  # for, and VPC-SC on must name a numeric access policy; neither bears on the guardrail.
  human_review_iap_audience = "1234567890-fictionaledgeclient.apps.googleusercontent.com"
  enable_vpc_sc             = false
  # Slice 7 turned these reversible controls off by default on 2026-10-01. The runs in this
  # file were written under the old default, so the file states it; a run that sets one
  # explicitly still overrides it. posture_defaults.tftest.hcl pins the new default.
  enable_org_policies = true
}

run "full_capabilities_stated_request_both_regional_features" {
  command = plan

  variables {
    model_armor_full_capabilities = true
  }

  assert {
    condition     = length(google_model_armor_template.loan_doc.filter_config[0].malicious_uri_filter_settings) == 1
    error_message = "The default must keep the malicious-URI filter: a region that serves it should get it without having to ask."
  }

  assert {
    condition     = length(google_model_armor_template.loan_doc.template_metadata[0].multi_language_detection) == 1
    error_message = "The default must keep multi-language detection on."
  }
}

# Slice 7 of the 2026-09-23 posture rule: a control that is not irreversible defaults off in
# code, so the regional capabilities arrive only when a deployment states them.
run "guardrail_regional_capabilities_are_declined_unless_stated" {
  command = plan


  assert {
    condition     = length(google_model_armor_template.loan_doc.filter_config[0].malicious_uri_filter_settings) == 0
    error_message = "model_armor_full_capabilities defaults to false: the malicious-URI filter arrives only when stated."
  }

  assert {
    condition     = length(google_model_armor_template.loan_doc.template_metadata[0].multi_language_detection) == 0
    error_message = "model_armor_full_capabilities defaults to false: multi-language detection arrives only when stated."
  }
}

run "declined_capabilities_drop_the_malicious_uri_block" {
  command = plan

  variables {
    model_armor_full_capabilities = false
  }

  assert {
    condition     = length(google_model_armor_template.loan_doc.filter_config[0].malicious_uri_filter_settings) == 0
    error_message = "model_armor_full_capabilities = false must drop malicious_uri_filter_settings: asia-southeast1 refuses the whole template with CAPABILITY_NOT_SUPPORTED while it is present."
  }

  assert {
    condition     = length(google_model_armor_template.loan_doc.template_metadata[0].multi_language_detection) == 0
    error_message = "model_armor_full_capabilities = false must drop multi_language_detection, which the region refuses the same way."
  }

  # Declining the regional features narrows the guardrail; it must not remove the rest of it.
  assert {
    condition = (
      length(google_model_armor_template.loan_doc.filter_config[0].pi_and_jailbreak_filter_settings) == 1 &&
      google_model_armor_template.loan_doc.filter_config[0].pi_and_jailbreak_filter_settings[0].filter_enforcement == "ENABLED" &&
      length(google_model_armor_template.loan_doc.filter_config[0].rai_settings[0].rai_filters) == 4
    )
    error_message = "Declining the regional capabilities must leave prompt-injection/jailbreak screening and the four RAI filters in place."
  }

  # The API requires template_metadata on every update even when it is otherwise empty; a plan
  # without it creates the template and then fails the second apply.
  assert {
    condition = (
      length(google_model_armor_template.loan_doc.template_metadata) == 1 &&
      google_model_armor_template.loan_doc.template_metadata[0].log_sanitize_operations == false
    )
    error_message = "template_metadata must stay present with log_sanitize_operations = false when the regional capabilities are declined."
  }
}
