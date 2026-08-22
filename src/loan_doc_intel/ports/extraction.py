"""Extraction port : structured field extraction from an applicant document.

Primary GCP adapter: **Document AI** on the Gemini Enterprise Agent Platform, pinned to
``asia-southeast1`` (Singapore) for residency. A specialised processor parses payslips,
bank statements, tax returns and employment letters into structured fields and dated
line items. On-prem migration swaps this for a placeholder adapter with no change to
callers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import ApplicantDocument, DocumentExtract


@runtime_checkable
class DocumentExtractionPort(Protocol):
    def extract(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> DocumentExtract:
        """Extract structured fields + line items from one applicant document."""
        ...
