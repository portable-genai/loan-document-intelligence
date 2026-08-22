"""Synthetic applicant documents + extracts for deterministic tests.

Nothing here touches Google Cloud. The data mimics the shape of real payslips, bank
statements and tax returns (with field-level citations, as B5 requires) so unit tests can
assert the full pipeline without any network or SDK. All names, NRIC ids, account numbers
and amounts are invented and CLEARLY FICTIONAL : they must never be treated as real
applicant data.
"""

from __future__ import annotations

from loan_doc_intel.domain.models import (
    Applicant,
    ApplicantDocument,
    Citation,
    DocType,
    DocumentExtract,
    IncomeFigure,
    IncomeKind,
    IncomePeriod,
    LineItem,
    SourceType,
)


def _cite(doc_id: str, title: str, field: str, page: int = 1) -> Citation:
    return Citation(
        source_id=doc_id,
        source_type=SourceType.DOCUMENT,
        title=title,
        page=page,
        field=field,
    )


# --------------------------------------------------------------------------- #
# A clearly-fictional applicant carrying PII (NRIC + email) to prove redaction.
# --------------------------------------------------------------------------- #
APPLICANT: Applicant = Applicant(
    id="app-fictional-0001",
    name="Jordan Tester Fictional",
    address="123 Imaginary Road, Singapore 000000",
    declared_income=IncomeFigure(
        source_doc_id="declared",
        amount=6500.0,
        currency="SGD",
        period=IncomePeriod.MONTHLY,
        kind=IncomeKind.SALARY,
    ),
)

# A raw input string that carries applicant PII, for the redaction tests.
PII_INPUT: str = (
    "Process loan application for Jordan Tester Fictional, NRIC S1234567A, "
    "email jordan.fictional@example.com, account 123-456789-0."
)

# --------------------------------------------------------------------------- #
# Documents submitted with the application.
# --------------------------------------------------------------------------- #
DOCUMENTS: tuple[ApplicantDocument, ...] = (
    ApplicantDocument(
        id="doc-payslip-2026-04",
        doc_type=DocType.PAYSLIP,
        uri="gs://fictional-loan-docs/app-0001/payslip-2026-04.pdf",
    ),
    ApplicantDocument(
        id="doc-bank-2026-04",
        doc_type=DocType.BANK_STATEMENT,
        uri="gs://fictional-loan-docs/app-0001/bank-2026-04.pdf",
    ),
)


# --------------------------------------------------------------------------- #
# Consistent extracts : payslip net pay matches declared income and salary credit.
# --------------------------------------------------------------------------- #
def consistent_extracts() -> list[DocumentExtract]:
    return [
        DocumentExtract(
            document_id="doc-payslip-2026-04",
            doc_type=DocType.PAYSLIP,
            fields={
                "title": "Payslip 2026-04",
                "name": "Jordan Tester Fictional",
                "address": "123 Imaginary Road, Singapore 000000",
                "employer": "Fictional Holdings Pte Ltd",
                "net_pay": "6500.00",
                "gross_pay": "7800.00",
            },
            period="2026-04",
            confidence=0.96,
            citations=(_cite("doc-payslip-2026-04", "Payslip 2026-04", "net_pay"),),
        ),
        DocumentExtract(
            document_id="doc-bank-2026-04",
            doc_type=DocType.BANK_STATEMENT,
            fields={
                "title": "Bank Statement 2026-04",
                "name": "Jordan Tester Fictional",
                "address": "123 Imaginary Road, Singapore 000000",
            },
            line_items=(
                LineItem(label="Opening balance", amount=12000.0, date="2026-04-01"),
                LineItem(label="Salary credit", amount=6500.0, date="2026-04-25"),
                LineItem(label="Closing balance", amount=15800.0, date="2026-04-30"),
            ),
            period="2026-04",
            confidence=0.93,
            citations=(_cite("doc-bank-2026-04", "Bank Statement 2026-04", "salary_credit"),),
        ),
    ]


# --------------------------------------------------------------------------- #
# Inconsistent extracts : salary credit does NOT match payslip net pay, name
# differs across documents, and the balance is on a steep decline.
# --------------------------------------------------------------------------- #
def inconsistent_extracts() -> list[DocumentExtract]:
    return [
        DocumentExtract(
            document_id="doc-payslip-2026-04",
            doc_type=DocType.PAYSLIP,
            fields={
                "title": "Payslip 2026-04",
                "name": "Jordan Tester Fictional",
                "address": "123 Imaginary Road, Singapore 000000",
                "employer": "Fictional Holdings Pte Ltd",
                "net_pay": "6500.00",
            },
            period="2026-04",
            confidence=0.95,
            citations=(_cite("doc-payslip-2026-04", "Payslip 2026-04", "net_pay"),),
        ),
        DocumentExtract(
            document_id="doc-bank-2026-04",
            doc_type=DocType.BANK_STATEMENT,
            fields={
                "title": "Bank Statement 2026-04",
                "name": "Jordann T Fiktional",  # name mismatch (planted)
                "address": "999 Other Avenue, Singapore 111111",  # address mismatch (planted)
            },
            line_items=(
                LineItem(label="Opening balance", amount=20000.0, date="2026-04-01"),
                LineItem(label="Salary credit", amount=3200.0, date="2026-04-25"),  # mismatch
                LineItem(label="Closing balance", amount=9000.0, date="2026-04-30"),  # decline
            ),
            period="2026-04",
            confidence=0.91,
            citations=(_cite("doc-bank-2026-04", "Bank Statement 2026-04", "salary_credit"),),
        ),
    ]
