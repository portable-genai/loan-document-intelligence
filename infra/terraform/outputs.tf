# outputs.tf : the handful of ids the app and operators need after apply.

output "project_id" {
  description = "The target project id."
  value       = var.project_id
}

output "region" {
  description = "The pinned deployment region (asia-southeast1)."
  value       = var.region
}

output "document_ai_processor_id" {
  description = "Document AI processor resource name : set as document_ai.processor_id in settings.yaml."
  value       = google_document_ai_processor.loan_docs.id
}

output "dlp_inspect_template" {
  description = "DLP inspect template id : set as dlp.inspect_template in settings.yaml."
  value       = google_data_loss_prevention_inspect_template.applicant_pii.id
}

output "dlp_deidentify_template" {
  description = "DLP de-identify template id : set as dlp.deidentify_template in settings.yaml."
  value       = google_data_loss_prevention_deidentify_template.applicant_pii.id
}

output "model_armor_template_id" {
  description = "Model Armor template id : mirrors model_armor.template_id in settings.yaml."
  value       = google_model_armor_template.loan_doc.template_id
}

output "kms_crypto_key" {
  description = "Regional CMEK key : set as kms_key in settings.yaml."
  value       = google_kms_crypto_key.loan_doc.id
}

output "worm_log_bucket" {
  description = "Locked WORM audit log bucket id."
  value       = google_logging_project_bucket_config.audit_worm.bucket_id
}

output "runtime_service_account" {
  description = "The least-privilege runtime service account email."
  value       = google_service_account.runtime.email
}

output "api_url" {
  description = "The Cloud Run service URL for the B5 API."
  value       = google_cloud_run_v2_service.api.uri
}
