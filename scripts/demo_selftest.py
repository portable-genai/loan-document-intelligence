#!/usr/bin/env python3
"""Credential-free anti-rot check for the real Doc5 demo pipeline and HTML proof hooks."""

from __future__ import annotations

from loan_doc_demo_server import DemoSession


def main() -> int:
    session = DemoSession()
    opening = session.render()
    assert "Jordan Tester Fictional" in opening and "Sam Two Fictional" in opening

    session.advance()
    clean = session.render()
    assert session.results["clean"]["verdict"] == "verified"
    assert clean.count('data-demo="deterministic-check"') == 6
    assert 'data-status="fail"' not in clean
    assert "HUMAN REVIEW REQUIRED" in clean

    session.advance()
    inconsistent = session.render()
    assert session.results["inconsistent"]["verdict"] == "inconsistent"
    assert inconsistent.count('data-demo="deterministic-check"') == 6
    assert 'data-status="fail"' in inconsistent
    assert "HUMAN REVIEW REQUIRED" in inconsistent

    print("PASS demo: opening, verified, inconsistent, evidence, and review states rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
