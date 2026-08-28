# document_ai.tf : the Document AI processor that parses applicant documents (B5 core).
#
# General Principle map:
#   P-03 (residency): PARTIAL, and stated rather than absorbed. The processor is created at
#         var.docai_location, which defaults to the `us` MULTI-REGION -- so applicant document
#         content is parsed in the United States while the rest of the stack stays in
#         Singapore. Document AI serves asia-southeast1 only once Google grants single-region
#         access; set var.docai_location (and LOAN_DOC_DOCAI_LOCATION) to asia-southeast1 the
#         day it lands.
#   P-01 (managed-first): extraction uses a managed Document AI processor rather than a
#         self-hosted OCR/ML stack.
#
# The app's Document AI adapter reads this processor id from settings (document_ai.processor_id)
# and routes income/bank-statement documents through it for structured-field extraction.

resource "google_document_ai_processor" "loan_docs" {
  project      = var.project_id
  location     = var.docai_location # NOT var.region: Document AI serves neither every region nor, yet, ours in-country
  display_name = "loan-document-intelligence-processor"

  # A form/document parser type covers payslips, bank statements, tax returns and
  # employment letters; a specialised lending parser can replace this without any app change.
  type = "FORM_PARSER_PROCESSOR"

  depends_on = [google_project_service.required]
}
