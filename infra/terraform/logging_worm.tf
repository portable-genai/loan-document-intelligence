# logging_worm.tf : the immutable (WORM) audit trail for B5 (A5, P-07).
#
# General Principle map:
#   P-07 (audited everything): every document-intelligence interaction is written to a
#         locked log bucket as an already-redacted record, write-once and immutable.
#   P-08 (retention): retention is var.retention_days (~7 years) and the bucket is locked,
#         so records cannot be deleted before the window expires : an irreversible control.
#   P-04 (PII minimisation): the app redacts applicant PII before it ever reaches a log
#         entry, so the WORM bucket never stores raw NRIC / bank / income data.

# Retention is always enforced. The irreversible lock is a deliberate production choice.
resource "google_logging_project_bucket_config" "audit_worm" {
  project        = var.project_id
  location       = var.region # asia-southeast1 : in-country audit storage (P-03)
  bucket_id      = "loan-document-intelligence-worm"
  retention_days = var.retention_days
  locked         = var.lock_audit_bucket

  cmek_settings {
    kms_key_name = google_kms_crypto_key.loan_doc.id
  }

  depends_on = [
    google_kms_crypto_key_iam_member.logging,
    google_project_service.required,
  ]
}

resource "google_project_iam_member" "audit_sink_writer" {
  project = var.project_id
  role    = "roles/logging.bucketWriter"
  member  = google_logging_project_sink.audit_sink.writer_identity
}

# Route the app's audit log to the locked bucket via a logging sink.
resource "google_logging_project_sink" "audit_sink" {
  name        = "loan-document-intelligence-audit-sink"
  destination = "logging.googleapis.com/projects/${var.project_id}/locations/${var.region}/buckets/${google_logging_project_bucket_config.audit_worm.bucket_id}"

  # Only the structured audit log emitted by the Cloud Logging adapter.
  filter = "logName=\"projects/${var.project_id}/logs/loan-document-intelligence-audit\""

  unique_writer_identity = true
}
