"""Live, click-through demo server for the B5 loan-doc flow (stdlib only).

Holds a real :class:`LoanDocService` over the SDK-free ``local`` adapter stack and runs the
*actual* ``process`` pipeline one step per click : pick the clean application, run it
(VERIFIED), then pick the planted-inconsistency application and run it (INCONSISTENT) :
rendering the audit-first income-verification UI at each step. No Google Cloud, no API key,
no extra dependencies; the demo port (8093) is distinct from the API port (8092).

    LOAN_DOC_PROFILE=local PYTHONPATH=src python scripts/loan_doc_demo_server.py [--port 8093]

Then open http://localhost:8093 and click "Next ▶" / "Restart", or drive it with
``scripts/loan_doc_demo_playwright.py`` for a presenter-controlled walkthrough.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("LOAN_DOC_PROFILE", "local")

import loan_doc_demo as demo  # noqa: E402  sibling script: the synthetic scenarios
import render_loan_doc_ui as r  # noqa: E402  sibling script: reuse the audit-first rendering

from loan_doc_intel.api import deps  # noqa: E402
from loan_doc_intel.config import Settings, build_container  # noqa: E402
from loan_doc_intel.domain.serialization import to_jsonable  # noqa: E402

# The scripted demo steps. Each "Next" performs the action described by ``next``.
STEPS = [
    {
        "key": "intro",
        "label": "Two synthetic applications ready to verify",
        "next": "Process the consistent application (Jordan Tester Fictional)",
    },
    {
        "key": "clean",
        "label": "Consistent application processed — verdict VERIFIED",
        "next": "Process the planted-inconsistency application (Sam Two Fictional)",
    },
    {
        "key": "inconsistent",
        "label": "Inconsistent application processed — verdict INCONSISTENT",
        "next": None,
    },
]

_CONTROL_CSS = """
.democtl{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:12px;
  margin:-24px -18px 16px;padding:12px 18px;background:#0b101a;color:#fff}
.democtl .lbl{font-size:13px}.democtl .lbl b{color:#90b2ff}
.democtl .spacer{flex:1}
.democtl form{margin:0}
.democtl button{font:inherit;font-size:13px;font-weight:600;border:0;border-radius:7px;
  padding:7px 14px;cursor:pointer}
.democtl .next{background:#3a60f0;color:#fff}.democtl .next:disabled{opacity:.4;cursor:default}
.democtl .restart{background:transparent;color:#a6b6cc;border:1px solid #33445b}
.democtl .vd{font-variant-numeric:tabular-nums;color:#cdd7e4;font-size:12px}
"""


class DemoSession:
    """Drives the real loan-doc pipeline forward one scripted step at a time."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.idx = 0
        self.results: dict[str, dict] = {}

    @property
    def at_end(self) -> bool:
        return self.idx >= len(STEPS) - 1

    def _process(self, key: str) -> dict:
        builder = {"clean": demo._clean_scenario, "inconsistent": demo._inconsistent_scenario}[key]
        applicant, documents, extracts = builder()
        container = build_container(Settings.load())
        service = deps.build_loan_doc_service(container)
        container.extraction.seed(extracts)
        case = service.process(applicant, documents, demo.PRINCIPAL)
        validation = case.validation
        counts = {
            s: (sum(1 for c in validation.checks if c.status.value == s) if validation else 0)
            for s in ("pass", "warn", "fail")
        }
        return {
            "id": applicant.id,
            "label": key,
            "verdict": case.income.verdict.value if case.income else "unknown",
            "passed": bool(validation.passed) if validation else False,
            "counts": counts,
            "case": to_jsonable(case),
        }

    def advance(self) -> None:
        if self.at_end:
            return
        self.idx += 1
        key = STEPS[self.idx]["key"]
        if key in ("clean", "inconsistent"):
            self.results[key] = self._process(key)

    # -- rendering --------------------------------------------------------- #
    def render(self) -> str:
        key = STEPS[self.idx]["key"]
        if key == "intro":
            html = r.page("Loan doc demo — applications", self._intro_body())
        else:
            html = r.render_applicant(self.results[key])
        return self._inject_controls(html)

    def _intro_body(self) -> str:
        cards = []
        for label, name, desc in (
            (
                "clean",
                "Jordan Tester Fictional",
                "Payslip net pay, declared income and the bank salary credit all reconcile.",
            ),
            (
                "inconsistent",
                "Sam Two Fictional",
                "Payslip net pay contradicts the declared income and the bank credit; the "
                "name differs across documents and the balance halves.",
            ),
        ):
            cards.append(
                f"<div class='ex'><div class='eh'><span class='tag'>{r.esc(label)}</span>"
                f"<span class='mono'>{r.esc(name)}</span></div>"
                f"<div class='fieldrow'>{r.esc(desc)}</div></div>"
            )
        return (
            "<h1>Loan / Mortgage Document Intelligence — live demo</h1>"
            "<p class='sub'>The same SDK-free <b>local</b> pipeline that powers "
            "<span class='mono'>POST /v1/process</span> and the React console : redact PII, "
            "extract each document, normalise income, then run the deterministic "
            "cross-validation. Two synthetic, clearly-fictional applications:</p>"
            + r.steps_bar("verified")
            + "<section class='panel'><h2>Applications to process</h2>"
            f"<div class='body'>{''.join(cards)}</div></section>"
            "<p class='foot'>Audit-first income-verification view · synthetic fictional data · "
            "deterministic cross-validation engine</p>"
        )

    def _inject_controls(self, html: str) -> str:
        step = STEPS[self.idx]
        nxt = step["next"]
        vd = ""
        key = step["key"]
        if key in self.results:
            res = self.results[key]
            cnt = res["counts"]
            vd = (
                f"<span class='vd'>{r.esc(res['verdict'].upper())} · "
                f"{cnt['pass']}P/{cnt['warn']}W/{cnt['fail']}F</span>"
            )
        next_btn = (
            f"<form method='post' action='/advance'><button class='next' type='submit'>"
            f"Next ▶ &nbsp;·&nbsp; {r.esc(nxt)}</button></form>"
            if nxt
            else "<button class='next' disabled>Demo complete ✓</button>"
        )
        bar = (
            "<div class='democtl'>"
            f"<span class='lbl'>Step {self.idx + 1}/{len(STEPS)} — <b>{r.esc(step['label'])}</b></span>"
            f"{vd}<span class='spacer'></span>{next_btn}"
            "<form method='post' action='/restart'><button class='restart' type='submit'>Restart</button></form>"
            "</div>"
        )
        html = html.replace("</style>", _CONTROL_CSS + "</style>", 1)
        return html.replace("<div class='wrap'>", "<div class='wrap'>" + bar, 1)


class Handler(BaseHTTPRequestHandler):
    session: DemoSession  # set on the server instance below

    def _send(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, to: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    @property
    def _sess(self) -> DemoSession:
        return self.server.session  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/":
                self._send(self._sess.render())
            elif path == "/state":
                self._send(json.dumps({"step": self._sess.idx}), 200)
            elif path == "/restart":
                # Allowed over GET so the walkthrough can reset with a plain navigation.
                self._sess.reset()
                self._redirect("/")
            else:
                self._send("<h1>404</h1>", 404)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/advance":
                self._sess.advance()
            elif path == "/restart":
                self._sess.reset()
        self._redirect("/")

    def log_message(self, *args: object) -> None:  # quiet console
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Live loan-doc demo server")
    parser.add_argument("--port", type=int, default=8093)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.session = DemoSession()  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    print(f"loan-doc demo server on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
