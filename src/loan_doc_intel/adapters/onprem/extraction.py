"""On-prem placeholder for ``DocumentExtractionPort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile this
port binds to the Document AI adapter; switching ``profile`` to ``onprem`` rebinds it
here. The adapter constructs cleanly with **no external dependencies** and structurally
satisfies the same Protocol as the managed adapter, so the contract tests prove interface
parity. ``extract`` deliberately raises rather than returning empty fields: an
unimplemented extractor must never feed the deterministic cross-validation an empty
extract, so porting on-premise *must* supply a real document parser. Filling this body in
is the only change required.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ApplicantDocument, DocumentExtract

_MESSAGE = (
    "On-prem DocumentExtractionPort adapter is a migration placeholder; implement against "
    "your on-premise platform. Core domain logic is unchanged."
)


class OnPremExtractionAdapter:
    """Placeholder extraction adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(
        self, document: ApplicantDocument, content: bytes, mime_type: str
    ) -> DocumentExtract:
        raise NotImplementedError(_MESSAGE)
