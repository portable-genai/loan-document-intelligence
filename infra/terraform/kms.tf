# kms.tf : regional Customer-Managed Encryption Keys (CMEK) in Singapore.
#
# General Principle map:
#   P-09 (CMEK does NOT cascade): a CMEK on one resource does not automatically protect
#         data that resource hands to another service. Each managed service (Document AI,
#         Agent Runtime, Logging, Secret Manager, DLP outputs) must be told to use this key
#         explicitly. We keep ONE regional key ring + crypto key here and wire it into every
#         resource that supports CMEK in its own file.
#   P-03 (residency): the key ring location is asia-southeast1 : a regional key, never the
#         global/multi-region key. Regional CMEK is what pins crypto material in-country.

resource "google_kms_key_ring" "loan_doc" {
  name     = "loan-document-intelligence-ring"
  location = var.region # asia-southeast1 : regional, in-country key material (P-03)

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "loan_doc" {
  name     = "loan-document-intelligence-cmek"
  key_ring = google_kms_key_ring.loan_doc.id

  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days : periodic rotation for key hygiene

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE" # switch to "HSM" if FIPS/CC HSM is mandated
  }

  lifecycle {
    # A destroyed key is unrecoverable and would strand all CMEK-encrypted data.
    prevent_destroy = true
  }
}

# --------------------------------------------------------------------------- #
# Grant each service agent the right to use the key. CMEK does not cascade (P-09):
# every service that encrypts with this key needs its OWN binding here.
# --------------------------------------------------------------------------- #
data "google_project" "this" {
  project_id = var.project_id
}

# Document AI service agent.
resource "google_kms_crypto_key_iam_member" "documentai" {
  crypto_key_id = google_kms_crypto_key.loan_doc.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-prod-dai-core.iam.gserviceaccount.com"
}

# Vertex AI / Agent Runtime service agent.
resource "google_kms_crypto_key_iam_member" "aiplatform" {
  crypto_key_id = google_kms_crypto_key.loan_doc.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

# Cloud Logging service agent (CMEK on the WORM bucket).
resource "google_kms_crypto_key_iam_member" "logging" {
  crypto_key_id = google_kms_crypto_key.loan_doc.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-logging.iam.gserviceaccount.com"
}
