"""Built-in synthetic applicant + document extracts for the ``local`` profile.

A tiny, clearly-fictional applicant with a payslip and a bank statement whose figures are
internally consistent, so the local extraction adapter has structured fields to feed the
deterministic cross-validation out of the box, and the end-to-end CLI smoke run returns a
real cited income verification with no external corpus. Every name, NRIC, account number
and amount is invented and CLEARLY FICTIONAL : it must never be treated as real applicant
data.

This mirrors ``tests/fixtures/sample_docs`` so the local adapters and the unit-test
fixtures share one deterministic dataset, but it lives under ``src`` (not ``tests``) so
the shipped package can seed itself without importing the test tree.
"""

from __future__ import annotations

from ...domain.models import (
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


# A clearly-fictional applicant carrying PII (NRIC + email) to prove redaction.
SEED_APPLICANT: Applicant = Applicant(
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

# Documents submitted with the application (pointers; content is fetched out of band).
SEED_DOCUMENTS: tuple[ApplicantDocument, ...] = (
    ApplicantDocument(
        id="doc-payslip-2026-04",
        doc_type=DocType.PAYSLIP,
        uri="local://fictional-loan-docs/app-0001/payslip-2026-04.txt",
    ),
    ApplicantDocument(
        id="doc-bank-2026-04",
        doc_type=DocType.BANK_STATEMENT,
        uri="local://fictional-loan-docs/app-0001/bank-2026-04.txt",
    ),
)


def seed_extracts() -> list[DocumentExtract]:
    """Consistent extracts : payslip net pay matches declared income and salary credit."""
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
