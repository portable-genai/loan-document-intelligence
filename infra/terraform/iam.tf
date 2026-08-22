# iam.tf : least-privilege service account for the B5 runtime (P-10).
#
# General Principle map:
#   P-10 (least privilege): the runtime service account is granted only the roles it needs
#         to extract (Document AI), screen (Model Armor), redact (DLP), reason (Vertex AI),
#         audit (Logging) and trace (Cloud Trace) : nothing broader.
#   P-04 (no secrets in code): the SA accesses secrets through Secret Manager, never inline.

resource "google_service_account" "runtime" {
  account_id   = "loan-document-intelligence-run"
  display_name = "B5 Loan Document Intelligence runtime"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  runtime_roles = [
    "roles/documentai.apiUser",           # call the Document AI processor
    "roles/dlp.user",                     # run DLP de-identification
    "roles/aiplatform.user",              # Gemini reasoning + Agent Runtime
    "roles/logging.logWriter",            # write structured audit records
    "roles/cloudtrace.agent",             # emit OpenTelemetry spans
    "roles/secretmanager.secretAccessor", # read app secrets
  ]
}

resource "google_project_iam_member" "runtime" {
  for_each = toset(local.runtime_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Allow the runtime SA to use the CMEK key (CMEK does not cascade, P-09).
resource "google_kms_crypto_key_iam_member" "runtime_cmek" {
  crypto_key_id = google_kms_crypto_key.loan_doc.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.runtime.email}"
}
