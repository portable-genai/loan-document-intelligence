"""Local extraction adapter (DocumentExtractionPort) : seedable canned extracts + parser.

The ``local`` profile's stand-in for **Document AI**: a deterministic, **seedable** store
of structured ``DocumentExtract`` objects keyed by ``document_id``. It self-seeds from the
built-in synthetic dataset (:mod:`._seed`) so an out-of-the-box local run feeds the
deterministic cross-validation real, consistent fields; callers (and the tests) can
``seed(extracts)`` their own scenario, including the planted-inconsistency one.

When a document is not in the seeded store, the adapter falls back to the SDK-free
:class:`LocalDocumentParser`, which parses the document's bytes / URI body into the same
flat ``fields`` / ``line_items`` shape. There is no Google emulator for Document AI, so
this path is unconditional and imports no google-cloud package.

The schema-aware ``FakeExtraction`` is a real, registered adapter rather than a test
fixture, so the in-memory implementation lives once under ``adapters/local`` and drives
both the offline tests and the CLI.
"""

from __future__ import annotations

from dataclasses import replace

from ...config import Settings
from ...domain.models import ApplicantDocument, DocumentExtract
from ._seed import seed_extracts


class LocalExtractionAdapter:
    """Return the seeded extract for each document; parse the body for unseeded ones."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Self-seed the built-in consistent dataset so an out-of-the-box run is grounded.
        self._by_id: dict[str, DocumentExtract] = {e.document_id: e for e in seed_extracts()}

    def seed(self, extracts: list[DocumentExtract]) -> int:
        """Replace the canned-extract store with ``extracts`` (deterministic test/CLI seed)."""
        self._by_id = {e.document_id: e for e in extracts}
        return len(self._by_id)

    def add(self, extracts: list[DocumentExtract]) -> int:
        """Add / overwrite individual extracts without clearing the store."""
        for extract in extracts:
            self._by_id[extract.document_id] = extract
        return len(extracts)

    def extract(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> DocumentExtract:
        """Return the seeded extract for ``document``, else parse its body locally."""
        seeded = self._by_id.get(document.id)
        if seeded is not None:
            # Preserve the document's declared type even if the seed predates it.
            return (
                replace(seeded, doc_type=document.doc_type)
                if seeded.doc_type != (document.doc_type)
                else seeded
            )
        from .document import LocalDocumentParser

        return LocalDocumentParser(self._settings).parse(document, content, mime_type)
