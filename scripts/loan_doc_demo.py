"""Runnable demo of the B5 loan-document-intelligence flow (synthetic, fictional data).

Drives the *real* ``LoanDocService.process`` pipeline offline over two clearly-fictional
applicants and writes an audit-view JSON for the UI renderer:

* **app-fictional-0001 (Jordan Tester Fictional)** : the built-in synthetic application :
  payslip net pay, declared income and the bank salary credit all reconcile, so every
  deterministic check PASSes and the verdict is VERIFIED.
* **app-fictional-0002 (Sam Two Fictional)** : a planted-inconsistency application : the
  payslip net pay (9000) contradicts the declared income (6500) and the bank salary credit
  (3000), the name differs across documents and the balance halves : multiple checks FAIL,
  so the verdict is INCONSISTENT.

It is the exact same path the API ``/v1/process`` endpoint and the React console use : the
LLM only normalises figures, while the deterministic ``CrossValidator`` owns every verdict,
and the case is always ``requires_human_review`` (P-06). Runs fully offline on the ``local``
profile : no Google Cloud, no API key, no SDK.

Run it::

    LOAN_DOC_PROFILE=local PYTHONPATH=src python scripts/loan_doc_demo.py [out.json]

It prints a per-applicant summary and writes the full audit-view JSON (one entry per
applicant) for ``scripts/render_loan_doc_ui.py`` to render.
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("LOAN_DOC_PROFILE", "local")

from loan_doc_intel.api import deps  # noqa: E402
from loan_doc_intel.config import Settings, build_container, resolve_profile  # noqa: E402
from loan_doc_intel.domain.identity import Principal  # noqa: E402
from loan_doc_intel.domain.models import (  # noqa: E402
    Applicant,
    ApplicantDocument,
    Citation,
    DocType,
    DocumentExtract,
    IncomeFigure,
    IncomeKind,
    IncomePeriod,
    LineItem,
    LoanApplicationCase,
    SourceType,
)
from loan_doc_intel.domain.serialization import to_jsonable  # noqa: E402

ACTOR = "rm.demo@bank.test"
# The synthetic applications are owned by tenant demo-bank (see the local entitlements
# registry); the demo acts as a demo-bank underwriter so object-level authorization passes.
PRINCIPAL = Principal(
    subject=ACTOR,
    principals=("group:loan-analyst", "group:underwriting"),
    tenant="demo-bank",
    source="demo",
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
# Scenario 1 : the clean, built-in synthetic application (VERIFIED).
# --------------------------------------------------------------------------- #
def _clean_scenario() -> tuple[Applicant, list[ApplicantDocument], list[DocumentExtract]]:
    applicant = Applicant(
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
    documents = [
        ApplicantDocument(id="doc-payslip-2026-04", doc_type=DocType.PAYSLIP, uri="local://p1"),
        ApplicantDocument(id="doc-bank-2026-04", doc_type=DocType.BANK_STATEMENT, uri="local://b1"),
    ]
    extracts = [
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
    return applicant, documents, extracts


# --------------------------------------------------------------------------- #
# Scenario 2 : a planted-inconsistency application (INCONSISTENT).
# --------------------------------------------------------------------------- #
def _inconsistent_scenario() -> tuple[Applicant, list[ApplicantDocument], list[DocumentExtract]]:
    applicant = Applicant(
        id="app-fictional-0002",
        name="Sam Two Fictional",
        address="9 Sample Avenue, Singapore 000000",
        declared_income=IncomeFigure(
            source_doc_id="declared",
            amount=6500.0,
            currency="SGD",
            period=IncomePeriod.MONTHLY,
            kind=IncomeKind.SALARY,
        ),
    )
    documents = [
        ApplicantDocument(id="doc-payslip-0002", doc_type=DocType.PAYSLIP, uri="local://p2"),
        ApplicantDocument(id="doc-bank-0002", doc_type=DocType.BANK_STATEMENT, uri="local://b2"),
    ]
    extracts = [
        DocumentExtract(
            document_id="doc-payslip-0002",
            doc_type=DocType.PAYSLIP,
            fields={
                "title": "Payslip 2026-04",
                "name": "Sam Two Fictional",
                "address": "9 Sample Avenue, Singapore 000000",
                "employer": "Globex Pte Ltd",
                # Net pay contradicts the declared income (6500) by far more than 5%.
                "net_pay": "9000.00",
                "gross_pay": "11000.00",
            },
            period="2026-04",
            confidence=0.88,
            citations=(_cite("doc-payslip-0002", "Payslip 2026-04", "net_pay"),),
        ),
        DocumentExtract(
            document_id="doc-bank-0002",
            doc_type=DocType.BANK_STATEMENT,
            fields={
                "title": "Bank Statement 2026-04",
                # Name differs from the payslip / applicant : NAME_MATCH should FAIL.
                "name": "Samuel Two",
                "address": "9 Sample Avenue, Singapore 000000",
            },
            line_items=(
                LineItem(label="Opening balance", amount=20000.0, date="2026-04-01"),
                # Salary credit (3000) does not match the payslip net pay (9000).
                LineItem(label="Salary credit", amount=3000.0, date="2026-04-25"),
                # Balance more than halves over the month : BALANCE_TREND should FAIL.
                LineItem(label="Closing balance", amount=8000.0, date="2026-04-30"),
            ),
            period="2026-04",
            confidence=0.84,
            citations=(_cite("doc-bank-0002", "Bank Statement 2026-04", "salary_credit"),),
        ),
    ]
    return applicant, documents, extracts


SCENARIOS = [
    ("clean", "Consistent application : every check reconciles", _clean_scenario),
    ("inconsistent", "Planted inconsistencies : several checks fail", _inconsistent_scenario),
]


def run_scenario(
    applicant: Applicant,
    documents: list[ApplicantDocument],
    extracts: list[DocumentExtract],
    label: str,
) -> dict:
    """Process one application through the real service offline and project to JSON.

    Seeding the local extraction adapter is how a caller plants a deterministic scenario
    (mirrors the test fixtures): the rest of the pipeline : redaction, guardrail, the LLM
    normalisation, the cross-validation and the audit write : runs exactly as in
    production, offline, on the ``local`` profile.
    """
    container = build_container(Settings.load())
    service = deps.build_loan_doc_service(container)
    container.extraction.seed(extracts)
    case: LoanApplicationCase = service.process(applicant, documents, PRINCIPAL)

    income = case.income
    validation = case.validation
    n_pass = sum(1 for c in validation.checks if c.status.value == "pass") if validation else 0
    n_warn = sum(1 for c in validation.checks if c.status.value == "warn") if validation else 0
    n_fail = sum(1 for c in validation.checks if c.status.value == "fail") if validation else 0

    print(f"-- Applicant {applicant.id}  ({label})")
    if income is not None:
        vi = income.verified_income
        print(f"   verdict: {income.verdict.value.upper()}")
        print(f"   verified income: {vi.amount} {vi.currency} per {vi.period.value}")
    if validation is not None:
        print(
            f"   checks: {len(validation.checks)} ({n_pass} pass / {n_warn} warn / {n_fail} fail)"
        )
    if income is not None and income.red_flags:
        for flag in income.red_flags:
            print(f"   red flag: {flag}")
    print(f"   human review required: {str(case.requires_human_review).lower()}\n")

    return {
        "id": applicant.id,
        "label": label,
        "verdict": income.verdict.value if income else "unknown",
        "passed": bool(validation.passed) if validation else False,
        "counts": {"pass": n_pass, "warn": n_warn, "fail": n_fail},
        "case": to_jsonable(case),
    }


def main(out_path: str) -> None:
    print("B5 Loan / Mortgage Document Intelligence : offline demo (local profile)\n")
    payload: dict = {
        "actor": ACTOR,
        # Through the single resolver, not a second read of the variable: the demo
        # reports the profile the app actually resolved, in all three states.
        "profile": resolve_profile().profile,
        "applicants": [],
    }
    for key, _desc, builder in SCENARIOS:
        applicant, documents, extracts = builder()
        payload["applicants"].append(run_scenario(applicant, documents, extracts, key))

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"Wrote audit-view JSON -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "loan_doc_demo.json")
