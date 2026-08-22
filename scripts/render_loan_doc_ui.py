"""Render the B5 audit-first UI from the demo JSON into static HTML pages.

Server-side, dependency-free rendering of the ``LoanApplicationCase`` artifacts (one page
per applicant plus a verdict summary), faithful to the React console's audit-first design
(the ink / regblue palette, Panel cards, the verdict StatusBadge, the deterministic checks
with expected-vs-observed and citation chips). Used to produce screenshots for slides with
the synthetic, clearly-fictional testing data.

    LOAN_DOC_PROFILE=local PYTHONPATH=src python scripts/render_loan_doc_ui.py \\
        loan_doc_demo.json out_dir/
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any

CHECK_LABEL = {
    "income_consistency": "Income consistency",
    "salary_credit_match": "Salary-credit match",
    "name_match": "Name match",
    "address_match": "Address match",
    "balance_trend": "Balance trend",
    "affordability": "Affordability",
}
DOC_LABEL = {
    "payslip": "Payslip",
    "bank_statement": "Bank statement",
    "tax_return": "Tax return",
    "employment_letter": "Employment letter",
    "id": "ID",
}
STATUS_COLOR = {
    "pass": ("#ecfdf5", "#059669"),
    "warn": ("#fffbeb", "#d97706"),
    "fail": ("#fee2e2", "#b91c1c"),
}
VERDICT_COLOR = {
    "verified": ("#ecfdf5", "#059669"),
    "needs_review": ("#fffbeb", "#d97706"),
    "inconsistent": ("#fee2e2", "#b91c1c"),
}
VERDICT_LABEL = {
    "verified": "VERIFIED",
    "needs_review": "NEEDS REVIEW",
    "inconsistent": "INCONSISTENT",
}

CSS = """
:root{--ink-50:#f5f7fa;--ink-100:#e6ebf2;--ink-200:#cdd7e4;--ink-300:#a6b6cc;
--ink-400:#7790ae;--ink-500:#546b8b;--ink-600:#3f5470;--ink-700:#33445b;--ink-800:#1f2a3a;
--ink-900:#0f1726;--regblue-50:#eef4ff;--regblue-100:#dbe7ff;--regblue-600:#2945d6;
--regblue-700:#2237ad;--ok:#059669;--ok-bg:#ecfdf5;--warn:#d97706;--warn-bg:#fffbeb;
--fail:#b91c1c;--fail-bg:#fee2e2;
--shadow:0 1px 2px rgba(11,16,26,.06),0 8px 24px rgba(11,16,26,.06);}
*{box-sizing:border-box}
body{margin:0;background:var(--ink-50);color:var(--ink-800);
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
font-size:14px;line-height:1.5;padding:24px 18px}
.wrap{max-width:880px;margin:0 auto}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h1{font-size:18px;margin:0 0 2px;color:var(--ink-900)}
.sub{color:var(--ink-500);font-size:13px;margin:0 0 16px}
.sub b{color:var(--ink-800)}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
.step{font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid var(--ink-200);background:#fff;color:var(--ink-500)}
.step.cur{background:var(--regblue-600);border-color:var(--regblue-600);color:#fff;font-weight:600}
.step.done{background:var(--regblue-50);border-color:var(--regblue-100);color:var(--regblue-700)}
.panel{border:1px solid var(--ink-200);background:#fff;border-radius:10px;box-shadow:var(--shadow);margin-bottom:16px}
.panel>h2{border-bottom:1px solid var(--ink-100);padding:11px 16px;margin:0;font-size:13px;font-weight:600;color:var(--ink-800);
display:flex;justify-content:space-between;align-items:center}
.panel>.body{padding:16px}
.badge{font-size:11px;font-weight:700;padding:2px 9px;border-radius:999px;text-transform:uppercase;letter-spacing:.02em}
.review{border:1px solid #fcd34d;background:var(--warn-bg);color:#92400e;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:14px}
.income .amt{font-size:26px;font-weight:700;color:var(--ink-900);font-variant-numeric:tabular-nums}
.income .per{font-size:13px;font-weight:400;color:var(--ink-400)}
.income .note{margin:6px 0 0;font-size:13px;color:var(--ink-600)}
.flags{margin:12px 0 0;padding:0 0 0 18px;font-size:12px;color:var(--fail)}
.flags li{margin-top:3px}
.evhead{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-500);margin:12px 0 4px}
.chiprow{display:flex;gap:5px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--ink-200);background:var(--ink-50);border-radius:6px;padding:1px 7px;font-size:11px;color:var(--ink-700)}
.chip .ty{font-family:ui-monospace,Menlo,monospace;font-weight:600;color:var(--regblue-700)}
.chip .sr{font-family:ui-monospace,Menlo,monospace}
/* checks */
.check{border:1px solid var(--ink-100);border-radius:9px;padding:11px 12px;margin-bottom:10px}
.check:last-child{margin-bottom:0}
.check .ch{display:flex;align-items:center;justify-content:space-between;gap:8px}
.check .name{font-weight:600;font-size:13px;color:var(--ink-800)}
.check .sev{font-size:11px;color:var(--ink-400)}
.check .rr{display:flex;align-items:center;gap:8px}
.kv{margin:8px 0 0;display:grid;grid-template-columns:5.5rem 1fr;gap:2px 12px;font-size:12px;color:var(--ink-600)}
.kv dt{color:var(--ink-400)}
.kv dd{margin:0}
/* extracts */
.ex{border:1px solid var(--ink-100);border-radius:9px;padding:11px 12px;margin-bottom:10px}
.ex .eh{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.tag{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--regblue-700);background:var(--regblue-50);border:1px solid var(--regblue-100);border-radius:5px;padding:1px 6px}
.muted{color:var(--ink-400);font-size:12px}
.fieldrow{font-size:12px;color:var(--ink-600);font-family:ui-monospace,Menlo,monospace;margin-top:2px}
.fieldrow .k{color:var(--ink-400)}
.foot{font-size:11px;color:var(--ink-400);text-align:center;margin-top:18px}
/* summary table */
table.tl{width:100%;border-collapse:collapse;font-size:13px}
table.tl th,table.tl td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--ink-100)}
table.tl th{font-size:11px;text-transform:uppercase;color:var(--ink-400);font-weight:600}
table.tl td.num{font-variant-numeric:tabular-nums}
a{color:var(--regblue-700)}
"""


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def badge(text: str, palette: tuple[str, str]) -> str:
    bg, fg = palette
    return f'<span class="badge" style="background:{bg};color:{fg}">{esc(text)}</span>'


def status_badge(status: str) -> str:
    return badge(status, STATUS_COLOR.get(status, STATUS_COLOR["warn"]))


def verdict_badge(verdict: str) -> str:
    return badge(
        VERDICT_LABEL.get(verdict, verdict.upper()),
        VERDICT_COLOR.get(verdict, VERDICT_COLOR["needs_review"]),
    )


def chip(c: dict) -> str:
    page = f" p.{c['page']}" if c.get("page") is not None else ""
    field = f".{c['field']}" if c.get("field") else ""
    return (
        f'<span class="chip"><span class="ty">DOC</span>'
        f'<span class="sr">{esc(c.get("source_id"))}{esc(field)}{esc(page)}</span></span>'
    )


def chiprow(citations: list[dict]) -> str:
    if not citations:
        return '<span class="muted">no citation</span>'
    return '<div class="chiprow">' + "".join(chip(c) for c in citations) + "</div>"


def steps_bar(verdict: str) -> str:
    labels = ["Redact PII", "Extract", "Cross-validate", "Verdict", "Underwriter review"]
    out = []
    for i, lab in enumerate(labels):
        cls = "step done" if i < 3 else ("step cur" if i == 3 else "step")
        out.append(f'<span class="{cls}">{esc(lab)}</span>')
    return '<div class="steps">' + "".join(out) + "</div>"


def render_income(income: dict) -> str:
    vi = income["verified_income"]
    flags = income.get("red_flags", [])
    flag_html = (
        '<ul class="flags">' + "".join(f"<li>{esc(f)}</li>" for f in flags) + "</ul>"
        if flags
        else ""
    )
    note = f'<p class="note">{esc(income["stability"])}</p>' if income.get("stability") else ""
    amount = f"{vi['amount']:,.0f}" if isinstance(vi["amount"], (int, float)) else esc(vi["amount"])
    return f"""
    <section class="panel"><h2>Income verification{verdict_badge(income["verdict"])}</h2>
    <div class="body income">
      <div class="amt">{amount} {esc(vi["currency"])}<span class="per"> / {esc(vi["period"])}</span></div>
      {note}{flag_html}
      <div class="evhead">Evidence</div>{chiprow(income.get("citations", []))}
    </div></section>"""


def render_checks(validation: dict) -> str:
    rows = []
    for c in validation.get("checks", []):
        rows.append(
            f"""<div class="check" data-demo="deterministic-check" data-status="{esc(c["status"])}">
              <div class="ch"><span class="name">{esc(CHECK_LABEL.get(c["kind"], c["kind"]))}</span>
                <span class="rr"><span class="sev">{esc(c["severity"])}</span>{status_badge(c["status"])}</span></div>
              <dl class="kv">
                <dt>expected</dt><dd>{esc(c["expected"])}</dd>
                <dt>observed</dt><dd>{esc(c["observed"])}</dd>
                {f"<dt>detail</dt><dd>{esc(c['detail'])}</dd>" if c.get("detail") else ""}
                <dt>evidence</dt><dd>{chiprow(c.get("citations", []))}</dd>
              </dl>
            </div>"""
        )
    pill = status_badge("pass" if validation.get("passed") else "fail")
    return (
        f'<section class="panel"><h2>Cross-validation checks '
        f'<span class="muted">deterministic, the verdict authority</span>{pill}</h2>'
        f'<div class="body">{"".join(rows)}</div></section>'
    )


def render_extracts(extracts: list[dict]) -> str:
    rows = []
    for e in extracts:
        fields = "".join(
            f'<div class="fieldrow"><span class="k">{esc(k)}:</span> {esc(v)}</div>'
            for k, v in e.get("fields", {}).items()
        )
        lines = "".join(
            f'<div class="fieldrow"><span class="k">line:</span> {esc(li["label"])} '
            f"{esc(li['amount'])} {esc(li['currency'])}"
            f"{(' on ' + esc(li['date'])) if li.get('date') else ''}</div>"
            for li in e.get("line_items", [])
        )
        rows.append(
            f"""<div class="ex">
              <div class="eh"><span class="tag">{esc(DOC_LABEL.get(e["doc_type"], e["doc_type"]))}</span>
                <span class="mono">{esc(e["document_id"])}</span>
                <span class="muted">confidence {float(e.get("confidence", 0)):.2f}</span></div>
              {fields}{lines}
            </div>"""
        )
    return (
        '<section class="panel"><h2>Document extracts '
        '<span class="muted">PII redacted before model + audit (P-04)</span></h2>'
        f'<div class="body">{"".join(rows)}</div></section>'
    )


def page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>"
        f"<style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def render_applicant(entry: dict) -> str:
    case = entry["case"]
    applicant = case["applicant"]
    income = case.get("income") or {}
    validation = case.get("validation") or {}
    verdict = entry["verdict"]
    header = (
        f"<h1>Income verification — {esc(applicant['name'])}</h1>"
        f"<p class='sub'>Application <b class='mono'>{esc(case['id'])}</b> · "
        f"{len(case.get('extracts', []))} documents · "
        f"verdict <b>{esc(VERDICT_LABEL.get(verdict, verdict))}</b></p>"
        + steps_bar(verdict)
        + (
            '<div class="review">HUMAN REVIEW REQUIRED — maker-checker gate (P-06). '
            "The agent verifies; the underwriter decides.</div>"
            if case.get("requires_human_review")
            else ""
        )
    )
    body = (
        header
        + render_income(income)
        + render_checks(validation)
        + render_extracts(case.get("extracts", []))
        + "<p class='foot'>Audit-first income-verification view · synthetic fictional data · "
        "deterministic cross-validation engine</p>"
    )
    return page(f"Income verification {case['id']}", body)


def render_summary(data: dict) -> str:
    rows = []
    for e in data["applicants"]:
        cnt = e["counts"]
        rows.append(
            f"<tr><td><a href='loan-doc-{esc(e['id'])}.html'>{esc(e['case']['applicant']['name'])}</a></td>"
            f"<td class='mono'>{esc(e['id'])}</td>"
            f"<td>{verdict_badge(e['verdict'])}</td>"
            f"<td class='num'>{cnt['pass']}</td><td class='num'>{cnt['warn']}</td>"
            f"<td class='num'>{cnt['fail']}</td></tr>"
        )
    body = (
        "<h1>Loan document intelligence — demo summary</h1>"
        f"<p class='sub'>Profile <b>{esc(data.get('profile'))}</b> · actor "
        f"<b class='mono'>{esc(data.get('actor'))}</b> · the same deterministic engine over "
        "two synthetic applications. Every case is maker-checker gated.</p>"
        '<section class="panel"><h2>Applications</h2><div class="body">'
        "<table class='tl'><tr><th>Applicant</th><th>Application</th><th>Verdict</th>"
        "<th>Pass</th><th>Warn</th><th>Fail</th></tr>" + "".join(rows) + "</table></div></section>"
        "<p class='foot'>Audit-first income-verification view · synthetic fictional data</p>"
    )
    return page("Loan doc intelligence — summary", body)


def main(json_path: str, out_dir: str) -> None:
    data = json.loads(Path(json_path).read_text())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    written = []
    for entry in data["applicants"]:
        p = out / f"loan-doc-{entry['id']}.html"
        p.write_text(render_applicant(entry))
        written.append(p)
    summary = out / "loan-doc-summary.html"
    summary.write_text(render_summary(data))
    written.append(summary)
    for p in written:
        print("wrote", p)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
