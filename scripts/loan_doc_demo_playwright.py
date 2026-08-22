#!/usr/bin/env python3
"""Presenter-paced walkthrough for the Doc5 audit-first UI.

The same runner targets the local demo server or a reviewed hosted demo URL. Presenter notes
are printed only to the terminal, after which each action proves its visible business result
before the runner pauses.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PageAction = Callable[[Any, str], None]


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    title: str
    presenter_notes: str
    action: PageAction


def _restart(page: Any, base_url: str) -> None:
    page.goto(f"{base_url.rstrip('/')}/restart", wait_until="domcontentloaded")
    page.get_by_role("heading", name="Loan / Mortgage Document Intelligence").wait_for()


def _advance(page: Any) -> None:
    button = page.get_by_role("button", name="Next", exact=False)
    if button.count() != 1:
        raise RuntimeError(f"expected exactly one enabled Next action, found {button.count()}")
    button.click()
    page.wait_for_load_state("domcontentloaded")


def open_queue(page: Any, base_url: str) -> None:
    _restart(page, base_url)
    page.get_by_text("Jordan Tester Fictional", exact=True).wait_for()
    page.get_by_text("Sam Two Fictional", exact=True).wait_for()


def process_consistent(page: Any, base_url: str) -> None:
    _advance(page)
    page.get_by_text("VERIFIED", exact=True).first.wait_for()
    page.get_by_text("HUMAN REVIEW REQUIRED", exact=False).wait_for()
    checks = page.locator('[data-demo="deterministic-check"]')
    if checks.count() != 6:
        raise RuntimeError(f"expected six deterministic checks, found {checks.count()}")


def process_inconsistent(page: Any, base_url: str) -> None:
    _advance(page)
    page.get_by_text("INCONSISTENT", exact=True).first.wait_for()
    page.get_by_text("HUMAN REVIEW REQUIRED", exact=False).wait_for()
    if page.locator('[data-demo="deterministic-check"][data-status="fail"]').count() < 1:
        raise RuntimeError("the planted inconsistency produced no failed deterministic check")


def close_review(page: Any, base_url: str) -> None:
    page.get_by_text("INCONSISTENT", exact=True).first.wait_for()
    page.get_by_text("The agent verifies; the underwriter decides.", exact=False).wait_for()
    page.bring_to_front()


STEPS: tuple[Step, ...] = (
    Step(
        id="open-queue",
        title="Open the underwriting queue",
        presenter_notes=(
            "The underwriter opens two retail-lending applications and starts with a clear view "
            "of the evidence that must be reconciled. The local and managed deployments present "
            "the same journey and the same decision contract, while the selected adapters change "
            "the extraction, safety, model, and audit services behind it."
        ),
        action=open_queue,
    ),
    Step(
        id="consistent-case",
        title="Verify a consistent application",
        presenter_notes=(
            "The underwriter processes a consistent application and receives a verified income "
            "summary supported by six explicit checks. Every figure points back to document "
            "evidence, and the deterministic engine produces the same result wherever it runs; "
            "the model can normalize and explain the evidence but cannot change a verdict."
        ),
        action=process_consistent,
    ),
    Step(
        id="inconsistent-case",
        title="Detect a planted inconsistency",
        presenter_notes=(
            "The underwriter now processes an application whose payslip, declared income, bank "
            "credit, identity fields, and balance trend do not reconcile. The same checks decide "
            "the opposite way, expose the expected and observed values, and preserve their source "
            "citations so a reviewer can verify why the case needs attention."
        ),
        action=process_inconsistent,
    ),
    Step(
        id="human-review",
        title="Hand the decision to a human reviewer",
        presenter_notes=(
            "The underwriter closes on the maker-checker boundary: the system verifies and "
            "escalates, while an accountable person decides. That invariant survives a move from "
            "local adapters to Google Cloud services, and the redacted evidence and audit record "
            "remain available for review rather than being hidden inside a model response."
        ),
        action=close_review,
    ),
)


def selected_steps(from_step: str | None = None) -> tuple[Step, ...]:
    if from_step is None:
        return STEPS
    for index, step in enumerate(STEPS):
        if step.id == from_step:
            return STEPS[index:]
    choices = ", ".join(step.id for step in STEPS)
    raise ValueError(f"unknown step {from_step!r}; choose one of: {choices}")


def _prepare_resume(page: Any, base_url: str, first_step: str) -> None:
    """Rebuild only the demo-owned prerequisite state for an inclusive resume."""
    _restart(page, base_url)
    index = next(i for i, step in enumerate(STEPS) if step.id == first_step)
    completed_actions = max(0, index - 1)
    for _ in range(completed_actions):
        _advance(page)


def print_script(steps: Sequence[Step]) -> None:
    for number, step in enumerate(steps, start=1):
        print(f"{number:02d}. {step.id}: {step.title}")
        print(f"    Notes: {step.presenter_notes}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8093")
    result.add_argument("--from", dest="from_step", metavar="STEP-ID")
    result.add_argument("--slow-mo", type=int, default=0, metavar="MS")
    result.add_argument("--list", action="store_true")
    result.add_argument("--no-pause", action="store_true")
    result.add_argument("--headless", action="store_true", help="CI and screenshot capture")
    result.add_argument("--screenshots", type=Path, metavar="DIR")
    return result


def run(
    steps: Sequence[Step],
    *,
    base_url: str,
    slow_mo: int,
    pause: bool,
    headless: bool,
    screenshots: Path | None,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Install the demo dependency with: pip install playwright && "
            "playwright install chromium"
        ) from error

    if screenshots is not None:
        screenshots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        try:
            page = context.new_page()
            page.set_default_timeout(30_000)
            if steps and steps[0].id != STEPS[0].id:
                _prepare_resume(page, base_url, steps[0].id)
            for number, step in enumerate(steps, start=1):
                print(f"\n{'=' * 72}\nSTEP {number:02d}: {step.title}\nID: {step.id}\n")
                print(f"PRESENTER NOTES: {step.presenter_notes}", flush=True)
                step.action(page, base_url)
                if screenshots is not None:
                    page.screenshot(
                        path=str(screenshots / f"{number:02d}-{step.id}.png"),
                        full_page=True,
                    )
                if pause:
                    input("Enter for next step...")
        finally:
            context.close()
            browser.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        steps = selected_steps(args.from_step)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if args.list:
        print_script(steps)
        return 0
    try:
        run(
            steps,
            base_url=args.base_url,
            slow_mo=args.slow_mo,
            pause=not args.no_pause,
            headless=args.headless,
            screenshots=args.screenshots,
        )
    except (RuntimeError, KeyboardInterrupt) as error:
        print(f"demo walkthrough failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
