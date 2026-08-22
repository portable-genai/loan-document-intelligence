"""Local document parser : the ``local`` profile's stand-in for Document AI.

SDK-free, deterministic plain-text extraction. If ``pypdf`` is importable and the bytes
look like a PDF, each PDF page becomes text; otherwise the bytes are decoded as UTF-8 and
parsed line by line into the flat ``fields`` / ``line_items`` shape the deterministic
cross-validation reads. There is no Google emulator for Document AI, so this path is
unconditional and imports no google-cloud package.

The text grammar is intentionally simple so a fixture file (or an inline payload) can drive
a real extraction offline:

* ``key: value`` lines become flat ``fields`` (``name``, ``employer``, ``net_pay`` ...).
* ``line: <label> <amount> [on <YYYY-MM-DD>]`` lines become dated ``LineItem`` rows.
"""

from __future__ import annotations

import re

from ...config import Settings
from ...domain.models import (
    ApplicantDocument,
    Citation,
    DocumentExtract,
    LineItem,
    SourceType,
)

# A "line: <label words> <amount> [on <date>]" row (a bank credit, an opening balance...).
_LINE_RE = re.compile(
    r"^line:\s*(?P<label>.+?)\s+(?P<amount>-?\d[\d,]*\.?\d*)\s*(?:on\s+(?P<date>\d{4}-\d{2}-\d{2}))?$",
    re.IGNORECASE,
)
# A "key: value" flat field row.
_FIELD_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_ ]*?):\s*(?P<value>.+)$")


class LocalDocumentParser:
    """Parse a document's bytes into a structured :class:`DocumentExtract`, no SDK required."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, document: ApplicantDocument, content: bytes, mime_type: str) -> DocumentExtract:
        text = self._to_text(content, mime_type)
        fields: dict[str, str] = {}
        line_items: list[LineItem] = []
        period = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            line_match = _LINE_RE.match(line)
            if line_match:
                line_items.append(
                    LineItem(
                        label=line_match.group("label").strip(),
                        amount=float(line_match.group("amount").replace(",", "")),
                        currency="SGD",
                        date=line_match.group("date"),
                    )
                )
                continue
            field_match = _FIELD_RE.match(line)
            if field_match:
                key = field_match.group("key").strip().lower().replace(" ", "_")
                value = field_match.group("value").strip()
                if key == "period":
                    period = value
                fields[key] = value
        confidence = 0.9 if (fields or line_items) else 0.1
        citation = Citation(
            source_id=document.id,
            source_type=SourceType.DOCUMENT,
            title=fields.get("title", document.doc_type.value),
            page=1,
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

    # ------------------------------------------------------------------ #
    # Text decoding (pypdf when available, else UTF-8 passthrough)
    # ------------------------------------------------------------------ #
    def _to_text(self, content: bytes | str, mime_type: str) -> str:
        if isinstance(content, str):
            return content
        if self._looks_like_pdf(content, mime_type):
            pages = self._extract_pdf_pages(content)
            if pages:
                return "\n".join(pages)
        return content.decode("utf-8", errors="replace") if isinstance(content, bytes) else ""

    @staticmethod
    def _looks_like_pdf(content: bytes, mime_type: str) -> bool:
        if "pdf" in (mime_type or "").lower():
            return True
        return isinstance(content, bytes) and content[:5] == b"%PDF-"

    @staticmethod
    def _extract_pdf_pages(content: bytes) -> list[str]:
        """Extract per-page text via pypdf when available; empty list if it is not."""
        try:
            import io

            from pypdf import PdfReader  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001 - pypdf is optional; fall back to text decode
            return []
        try:
            reader = PdfReader(io.BytesIO(content))
            return [(page.extract_text() or "") for page in reader.pages]
        except Exception:  # noqa: BLE001 - a malformed PDF falls back to text decode
            return []
