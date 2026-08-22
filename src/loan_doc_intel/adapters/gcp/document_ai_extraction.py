"""Document AI extraction adapter (DocumentExtractionPort, primary GCP backend).

Implements :class:`DocumentExtractionPort` against **Document AI** on the Gemini
Enterprise Agent Platform. A processor (a payslip / bank-statement / form parser,
configured in Terraform) parses an applicant document into structured fields and dated
line items used by the deterministic cross-validation. The call is regional
(``projects/{project}/locations/{location}/processors/{processor}``) so document content
stays inside Singapore for residency.

PII note: the extracted fields may contain applicant PII (names, addresses, account
numbers). The orchestrator redacts those free-text fields immediately after extraction
(P-04) before they reach the model or the audit sink : this adapter only parses.

The ``google.cloud.documentai`` import is lazy so on-prem and test profiles load this
module with no GCP SDK installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import (
    ApplicantDocument,
    Citation,
    DocumentExtract,
    LineItem,
    SourceType,
)

# Entity-type names this adapter maps onto the flat ``fields`` dict. The processor's
# schema labels are mapped to the canonical field names the CrossValidator reads.
_FIELD_ALIASES: dict[str, str] = {
    "employee_name": "name",
    "account_holder_name": "name",
    "employee_address": "address",
    "employer_name": "employer",
    "net_pay": "net_pay",
    "net_salary": "net_pay",
    "gross_pay": "gross_pay",
    "pay_period": "period",
    "statement_period": "period",
}

# Line-item entity types that carry a dated monetary amount (bank credits, balances).
_LINE_ITEM_TYPES: tuple[str, ...] = ("transaction", "salary_credit", "balance", "credit")


class DocumentAiExtractionAdapter:
    """Parse an applicant document into a structured ``DocumentExtract`` via Document AI."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._docai = settings.document_ai
        self._client: Any | None = None

    # -- public API -------------------------------------------------------- #
    def extract(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> DocumentExtract:
        """Run Document AI over ``content`` (or fetch ``document.uri``) and map the result."""
        client = self._service_client()
        request = self._build_request(document, content, mime_type)
        response = client.process_document(request=request)
        return self._map_document(document, getattr(response, "document", None))

    # -- client / request -------------------------------------------------- #
    def _service_client(self) -> Any:
        from google.api_core.client_options import ClientOptions  # lazy
        from google.cloud import documentai  # lazy

        if self._client is None:
            # verify: https://cloud.google.com/document-ai/docs/setup
            opts = ClientOptions(api_endpoint=f"{self._docai.location}-documentai.googleapis.com")
            self._client = documentai.DocumentProcessorServiceClient(client_options=opts)
        return self._client

    def _processor_name(self) -> str:
        return self._docai.processor_id

    def _build_request(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> dict[str, Any]:
        raw_document = {"content": content, "mime_type": mime_type}
        request: dict[str, Any] = {
            "name": self._processor_name(),
            "raw_document": raw_document,
        }
        # When no inline bytes are supplied, fetch by GCS URI (gcs_document).
        if not content and document.uri:
            request.pop("raw_document", None)
            request["gcs_document"] = {"gcs_uri": document.uri, "mime_type": mime_type}
        return request

    # -- response mapping -------------------------------------------------- #
    def _map_document(self, document: ApplicantDocument, parsed: Any) -> DocumentExtract:
        if parsed is None:
            return DocumentExtract(
                document_id=document.id, doc_type=document.doc_type, confidence=0.0
            )
        fields: dict[str, str] = {}
        line_items: list[LineItem] = []
        period = ""
        confidences: list[float] = []

        for entity in getattr(parsed, "entities", None) or []:
            etype = str(getattr(entity, "type_", "") or getattr(entity, "type", ""))
            value = str(getattr(entity, "mention_text", "") or "")
            conf = float(getattr(entity, "confidence", 0.0) or 0.0)
            confidences.append(conf)

            if etype in _LINE_ITEM_TYPES:
                amount = self._amount(entity)
                if amount is not None:
                    line_items.append(LineItem(label=etype, amount=amount, date=self._date(entity)))
                continue
            canonical = _FIELD_ALIASES.get(etype, etype)
            if canonical == "period":
                period = value
            fields[canonical] = value

        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        page = self._first_page(parsed)
        citation = Citation(
            source_id=document.id,
            source_type=SourceType.DOCUMENT,
            title=fields.get("title", document.doc_type.value),
            page=page,
            field="",
        )
        return DocumentExtract(
            document_id=document.id,
            doc_type=document.doc_type,
            fields=fields,
            line_items=tuple(line_items),
            period=period,
            confidence=confidence,
            citations=(citation,),
        )

    @staticmethod
    def _amount(entity: Any) -> float | None:
        normalized = getattr(entity, "normalized_value", None)
        money = getattr(normalized, "money_value", None) if normalized else None
        if money is not None:
            units = int(getattr(money, "units", 0) or 0)
            nanos = int(getattr(money, "nanos", 0) or 0)
            return units + nanos / 1e9
        text = str(getattr(entity, "mention_text", "") or "").replace(",", "").strip()
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _date(entity: Any) -> str | None:
        normalized = getattr(entity, "normalized_value", None)
        date_value = getattr(normalized, "date_value", None) if normalized else None
        if date_value is None:
            return None
        year = getattr(date_value, "year", 0)
        month = getattr(date_value, "month", 0)
        day = getattr(date_value, "day", 0)
        if year and month and day:
            return f"{year:04d}-{month:02d}-{day:02d}"
        return None

    @staticmethod
    def _first_page(parsed: Any) -> int | None:
        pages = getattr(parsed, "pages", None)
        if pages:
            page_number = getattr(pages[0], "page_number", None)
            return int(page_number) if page_number else 1
        return 1
