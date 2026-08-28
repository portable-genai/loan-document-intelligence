"""An unreadable model amount must not become a zero income figure.

`coerce_amount` returned 0.0 for anything it could not parse, so a model answering "not
disclosed", "see attached" or an empty string produced an `IncomeFigure` of 0.00 attributed to a
real source document. Nothing downstream could tell that apart from a genuine zero.

The repository already had the correct shape ten files away: `cross_validator._amount` parses the
same kind of string and returns `None` when it cannot, which is the three-state read the fleet
requires (a value, an absence, and never an absence wearing a value's clothes). The two halves
disagreed, and the deterministic half was the one that got it right.

It matters here more than in most places because these figures are money. A fabricated 0.00
drags a monthly average down and, worse, manufactures a discrepancy in the income cross-check
against the applicant's declared income, so a clean file can be flagged on a figure the lender
never saw.
"""

from __future__ import annotations

from loan_doc_intel.domain import _grounded as g
from loan_doc_intel.domain.loan_doc_service import LoanDocService
from loan_doc_intel.domain.models import DocType, DocumentExtract


def _extract() -> DocumentExtract:
    return DocumentExtract(document_id="doc-1", doc_type=DocType.PAYSLIP)


def test_an_unreadable_amount_is_absent_rather_than_zero() -> None:
    assert g.coerce_amount("not disclosed") is None
    assert g.coerce_amount("") is None
    assert g.coerce_amount(None) is None


def test_a_real_zero_is_still_a_zero() -> None:
    """Absence and a genuine zero must stay distinguishable in both directions."""
    assert g.coerce_amount(0) == 0.0
    assert g.coerce_amount("0.00") == 0.0


def test_a_figure_whose_amount_cannot_be_read_is_skipped_not_fabricated() -> None:
    parsed = {
        "figures": [
            {"source_doc_id": "doc-1", "amount": "not disclosed", "period": "monthly"},
            {"source_doc_id": "doc-1", "amount": "6,500.00", "period": "monthly"},
        ]
    }

    figures = LoanDocService._build_figures(parsed, [_extract()])

    assert [f.amount for f in figures] == [6500.0]
