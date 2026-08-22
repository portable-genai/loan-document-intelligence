# document_ai.tf : the Document AI processor that parses applicant documents (B5 core).
#
# General Principle map:
#   P-03 (residency): the processor is created in asia-southeast1 (Singapore) so document
#         content is parsed in-country.
#   P-01 (managed-first): extraction uses a managed Document AI processor rather than a
#         self-hosted OCR/ML stack.
#
# The app's Document AI adapter reads this processor id from settings (document_ai.processor_id)
# and routes income/bank-statement documents through it for structured-field extraction.

resource "google_document_ai_processor" "loan_docs" {
  project      = var.project_id
  location     = var.region # asia-southeast1 : in-country document parsing (P-03)
  display_name = "loan-document-intelligence-processor"

  # A form/document parser type covers payslips, bank statements, tax returns and
  # employment letters; a specialised lending parser can replace this without any app change.
  type = "FORM_PARSER_PROCESSOR"

  depends_on = [google_project_service.required]
}
