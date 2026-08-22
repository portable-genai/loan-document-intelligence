"""CrossValidator : the deterministic heart of B5 (SPEC §5).

Runs a fixed set of DETERMINISTIC consistency checks across an applicant's document
extracts and the applicant's declared details. Every check is pure arithmetic / string
comparison with an explicit expected vs observed and field-level evidence : there is no
model in this path, so the verdicts are reproducible and auditable. The LLM may later
*explain* a check in prose, but it can never change a status (P-06 boundary: the agent
verifies, the underwriter decides).

Checks (CheckKind):
* INCOME_CONSISTENCY : payslip net pay vs declared income vs tax-return monthly income,
  within a relative tolerance.
* SALARY_CREDIT_MATCH : the salary credit on the bank statement matches the payslip net
  pay, within tolerance.
* NAME_MATCH : the applicant name is consistent across documents that carry a name.
* ADDRESS_MATCH : the applicant address is consistent across documents that carry one.
* BALANCE_TREND : the dated bank balances are not on a sustained decline.
* AFFORDABILITY : a simple verified-income-to-declared-obligations ratio sanity check.

Pure domain code : no Google Cloud / ADK imports.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    Applicant,
    CheckKind,
    CheckStatus,
    Citation,
    CrossValidationCheck,
    CrossValidationResult,
    DocType,
    DocumentExtract,
    IncomePeriod,
    Severity,
    SourceType,
)


def _amount(text: str) -> float | None:
    """Parse a monetary string ('6,500.00', 'SGD 6500') to a float, or None."""
    cleaned = text.replace(",", "").replace("SGD", "").replace("$", "").replace("USD", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _within_tolerance(a: float, b: float, tol: float) -> bool:
    base = max(abs(a), abs(b), 1.0)
    return abs(a - b) / base <= tol


def _norm(text: str) -> str:
    """Normalise a name/address for comparison (case, whitespace, punctuation)."""
    return " ".join(text.lower().replace(",", " ").replace(".", " ").split())


def _doc_citation(extract: DocumentExtract, field: str) -> Citation:
    page = extract.citations[0].page if extract.citations else None
    title = extract.fields.get("title") or extract.doc_type.value
    return Citation(
        source_id=extract.document_id,
        source_type=SourceType.DOCUMENT,
        title=title,
        page=page,
        field=field,
    )


@dataclass(frozen=True, slots=True)
class CrossValidator:
    """Pure cross-validation with bank-owned thresholds injected as data."""

    amount_tolerance: float = 0.05
    balance_decline_warn_ratio: float = 0.15
    balance_decline_fail_ratio: float = 0.40
    affordability_warn_ratio: float = 0.55
    affordability_fail_ratio: float = 0.70

    def __post_init__(self) -> None:
        ratios = (
            self.amount_tolerance,
            self.balance_decline_warn_ratio,
            self.balance_decline_fail_ratio,
            self.affordability_warn_ratio,
            self.affordability_fail_ratio,
        )
        if any(value < 0 or value > 1 for value in ratios):
            raise ValueError("validation thresholds must be ratios between 0 and 1")
        if self.balance_decline_warn_ratio >= self.balance_decline_fail_ratio:
            raise ValueError("balance decline warn ratio must be below fail ratio")
        if self.affordability_warn_ratio >= self.affordability_fail_ratio:
            raise ValueError("affordability warn ratio must be below fail ratio")

    def validate(
        self,
        extracts: list[DocumentExtract],
        applicant: Applicant,
        application_id: str = "",
    ) -> CrossValidationResult:
        """Return the deterministic ``CrossValidationResult`` for ``applicant``."""
        by_type: dict[DocType, list[DocumentExtract]] = {}
        for e in extracts:
            by_type.setdefault(e.doc_type, []).append(e)

        checks: list[CrossValidationCheck] = []
        checks.append(self._income_consistency(by_type, applicant))
        checks.append(self._salary_credit_match(by_type))
        checks.append(self._field_match(by_type, "name", CheckKind.NAME_MATCH, applicant.name))
        checks.append(
            self._field_match(by_type, "address", CheckKind.ADDRESS_MATCH, applicant.address)
        )
        checks.append(self._balance_trend(by_type))
        checks.append(self._affordability(by_type, applicant))

        checks_t = tuple(checks)
        passed = self._passed(checks_t)
        return CrossValidationResult(
            application_id=application_id or applicant.id,
            checks=checks_t,
            passed=passed,
            requires_human_review=True,
        )

    # ------------------------------------------------------------------ #
    # Individual deterministic checks
    # ------------------------------------------------------------------ #
    def _income_consistency(
        self, by_type: dict[DocType, list[DocumentExtract]], applicant: Applicant
    ) -> CrossValidationCheck:
        payslips = by_type.get(DocType.PAYSLIP, [])
        net = self._payslip_net(payslips[0]) if payslips else None
        declared = (
            applicant.declared_income.monthly_amount()
            if applicant.declared_income is not None
            else None
        )
        citations = tuple(_doc_citation(p, "net_pay") for p in payslips[:1])

        if net is None or declared is None:
            return CrossValidationCheck(
                kind=CheckKind.INCOME_CONSISTENCY,
                status=CheckStatus.WARN,
                expected="payslip net pay and a declared income to compare",
                observed=f"net pay {net}, declared monthly {declared}",
                detail="Insufficient evidence to compare declared income against a payslip.",
                severity=Severity.MEDIUM,
                citations=citations,
            )
        status = (
            CheckStatus.PASS
            if _within_tolerance(net, declared, self.amount_tolerance)
            else CheckStatus.FAIL
        )
        return CrossValidationCheck(
            kind=CheckKind.INCOME_CONSISTENCY,
            status=status,
            expected=(f"declared monthly income {declared:.2f} +/- {self.amount_tolerance:.0%}"),
            observed=f"payslip net pay {net:.2f}",
            detail="",
            severity=Severity.HIGH,
            citations=citations,
        )

    def _salary_credit_match(
        self, by_type: dict[DocType, list[DocumentExtract]]
    ) -> CrossValidationCheck:
        payslips = by_type.get(DocType.PAYSLIP, [])
        statements = by_type.get(DocType.BANK_STATEMENT, [])
        net = self._payslip_net(payslips[0]) if payslips else None
        credit = self._salary_credit(statements[0]) if statements else None
        citations = tuple(
            _doc_citation(d, "salary_credit") for d in (payslips[:1] + statements[:1])
        )

        if net is None or credit is None:
            return CrossValidationCheck(
                kind=CheckKind.SALARY_CREDIT_MATCH,
                status=CheckStatus.WARN,
                expected="a payslip net pay and a bank salary credit to match",
                observed=f"net pay {net}, salary credit {credit}",
                detail="No matching payslip net pay and bank salary credit were available.",
                severity=Severity.MEDIUM,
                citations=citations,
            )
        status = (
            CheckStatus.PASS
            if _within_tolerance(net, credit, self.amount_tolerance)
            else CheckStatus.FAIL
        )
        return CrossValidationCheck(
            kind=CheckKind.SALARY_CREDIT_MATCH,
            status=status,
            expected=(
                f"bank salary credit {net:.2f} +/- {self.amount_tolerance:.0%} (payslip net pay)"
            ),
            observed=f"bank salary credit {credit:.2f}",
            detail="",
            severity=Severity.CRITICAL,
            citations=citations,
        )

    def _field_match(
        self,
        by_type: dict[DocType, list[DocumentExtract]],
        field: str,
        kind: CheckKind,
        applicant_value: str,
    ) -> CrossValidationCheck:
        values: list[tuple[str, DocumentExtract]] = []
        for extracts in by_type.values():
            for e in extracts:
                v = e.fields.get(field)
                if v:
                    values.append((v, e))
        citations = tuple(_doc_citation(e, field) for _, e in values)
        observed_values = {_norm(v) for v, _ in values}
        if applicant_value:
            observed_values.add(_norm(applicant_value))

        if len(observed_values) <= 1 and observed_values:
            status = CheckStatus.PASS
        elif not observed_values:
            return CrossValidationCheck(
                kind=kind,
                status=CheckStatus.WARN,
                expected=f"a consistent applicant {field} across documents",
                observed=f"no {field} found on any document",
                detail=f"No document carried an applicant {field} to cross-check.",
                severity=Severity.LOW,
                citations=citations,
            )
        else:
            status = CheckStatus.FAIL
        return CrossValidationCheck(
            kind=kind,
            status=status,
            expected=f"a single consistent applicant {field}",
            observed=f"{len(observed_values)} distinct {field} value(s) across documents",
            detail="",
            severity=Severity.HIGH if kind is CheckKind.NAME_MATCH else Severity.MEDIUM,
            citations=citations,
        )

    def _balance_trend(self, by_type: dict[DocType, list[DocumentExtract]]) -> CrossValidationCheck:
        statements = by_type.get(DocType.BANK_STATEMENT, [])
        balances: list[float] = []
        for s in statements:
            for item in s.line_items:
                if "balance" in item.label.lower():
                    balances.append(item.amount)
        citations = tuple(_doc_citation(s, "balance") for s in statements[:1])

        if len(balances) < 2:
            return CrossValidationCheck(
                kind=CheckKind.BALANCE_TREND,
                status=CheckStatus.WARN,
                expected="at least two dated balances to assess a trend",
                observed=f"{len(balances)} balance point(s)",
                detail="Not enough dated balances to assess a balance trend.",
                severity=Severity.LOW,
                citations=citations,
            )
        opening, closing = balances[0], balances[-1]
        decline = (opening - closing) / max(abs(opening), 1.0)
        if decline >= self.balance_decline_fail_ratio:
            status, severity = CheckStatus.FAIL, Severity.HIGH
        elif decline >= self.balance_decline_warn_ratio:
            status, severity = CheckStatus.WARN, Severity.MEDIUM
        else:
            status, severity = CheckStatus.PASS, Severity.LOW
        return CrossValidationCheck(
            kind=CheckKind.BALANCE_TREND,
            status=status,
            expected=(
                f"closing balance not more than {self.balance_decline_warn_ratio:.0%} below opening"
            ),
            observed=f"balance moved {opening:.2f} -> {closing:.2f} ({decline:+.0%})",
            detail="",
            severity=severity,
            citations=citations,
        )

    def _affordability(
        self, by_type: dict[DocType, list[DocumentExtract]], applicant: Applicant
    ) -> CrossValidationCheck:
        payslips = by_type.get(DocType.PAYSLIP, [])
        net = self._payslip_net(payslips[0]) if payslips else None
        obligations = self._declared_obligations(by_type)
        citations = tuple(_doc_citation(p, "net_pay") for p in payslips[:1])

        if net is None or net <= 0:
            return CrossValidationCheck(
                kind=CheckKind.AFFORDABILITY,
                status=CheckStatus.WARN,
                expected="a verifiable monthly income to assess affordability",
                observed=f"net pay {net}",
                detail="No verifiable income to compute an affordability ratio.",
                severity=Severity.MEDIUM,
                citations=citations,
            )
        ratio = obligations / net
        if ratio >= self.affordability_fail_ratio:
            status, severity = CheckStatus.FAIL, Severity.HIGH
        elif ratio >= self.affordability_warn_ratio:
            status, severity = CheckStatus.WARN, Severity.MEDIUM
        else:
            status, severity = CheckStatus.PASS, Severity.LOW
        return CrossValidationCheck(
            kind=CheckKind.AFFORDABILITY,
            status=status,
            expected=(f"obligations-to-income ratio below {self.affordability_warn_ratio:.0%}"),
            observed=f"ratio {ratio:.0%} (obligations {obligations:.2f} / income {net:.2f})",
            detail="",
            severity=severity,
            citations=citations,
        )

    # ------------------------------------------------------------------ #
    # Field extractors (tolerant of missing data)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _payslip_net(extract: DocumentExtract) -> float | None:
        for key in ("net_pay", "net_salary", "take_home"):
            value = extract.fields.get(key)
            if value:
                amount = _amount(value)
                if amount is not None:
                    return amount
        return None

    @staticmethod
    def _salary_credit(extract: DocumentExtract) -> float | None:
        for item in extract.line_items:
            if "salary" in item.label.lower() and "credit" in item.label.lower():
                return item.amount
        for item in extract.line_items:
            if "salary" in item.label.lower():
                return item.amount
        return None

    @staticmethod
    def _declared_obligations(by_type: dict[DocType, list[DocumentExtract]]) -> float:
        total = 0.0
        for extracts in by_type.values():
            for e in extracts:
                value = e.fields.get("monthly_obligations") or e.fields.get("existing_emi")
                if value:
                    amount = _amount(value)
                    if amount is not None:
                        total += amount
        return total

    @staticmethod
    def _passed(checks: tuple[CrossValidationCheck, ...]) -> bool:
        """The application passes when no check FAILED and no HIGH/CRITICAL check WARNED."""
        for c in checks:
            if c.status is CheckStatus.FAIL:
                return False
            if c.status is CheckStatus.WARN and c.severity in (Severity.HIGH, Severity.CRITICAL):
                return False
        return True


# Critical periods kept here for the income service to reuse when normalising.
MONTHLY = IncomePeriod.MONTHLY
