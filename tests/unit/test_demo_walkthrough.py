"""Offline contract tests for the presenter-paced loan-document-intelligence walkthrough."""

from __future__ import annotations

import builtins
import importlib.util
import re
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "loan_doc_demo_playwright.py"
_SPEC = importlib.util.spec_from_file_location("loan_doc_demo_playwright", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
walkthrough = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = walkthrough
_SPEC.loader.exec_module(walkthrough)


class _Page:
    def set_default_timeout(self, value: int) -> None:
        pass

    def screenshot(self, **kwargs: object) -> None:
        pass


class _Context:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def new_page(self) -> _Page:
        return _Page()

    def close(self) -> None:
        self.events.append("context-close")


class _Browser:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def new_context(self, **kwargs: object) -> _Context:
        return _Context(self.events)

    def close(self) -> None:
        self.events.append("browser-close")


class _Chromium:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def launch(self, **kwargs: object) -> _Browser:
        return _Browser(self.events)


class _Playwright:
    def __init__(self, events: list[str]) -> None:
        self.chromium = _Chromium(events)


class _Manager:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> _Playwright:
        return _Playwright(self.events)

    def __exit__(self, *args: object) -> None:
        pass


def _playwright_modules(events: list[str]) -> dict[str, types.ModuleType]:
    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _Manager(events)  # type: ignore[attr-defined]
    return {"playwright": package, "playwright.sync_api": sync_api}


def test_selection_is_inclusive_and_unknown_ids_fail() -> None:
    assert [s.id for s in walkthrough.selected_steps("inconsistent-case")] == [
        "inconsistent-case",
        "human-review",
    ]
    with pytest.raises(ValueError, match="unknown step"):
        walkthrough.selected_steps("not-a-step")


def test_list_mode_needs_no_playwright() -> None:
    assert walkthrough.main(["--list"]) == 0


def test_every_from_step_quoted_in_demo_md_is_a_real_step_id() -> None:
    """A resume id in the run sheet has to be one the runner accepts.

    Matching is exact, so a shortened id is not a near miss: `selected_steps` raises and
    `main` returns 2, which lands mid-presentation. DEMO.md quoted `--from inconsistent`
    while the step is `inconsistent-case`, and nothing failed until a presenter typed it.
    """
    demo = (Path(__file__).parents[2] / "DEMO.md").read_text(encoding="utf-8")
    quoted = re.findall(r"--from\s+([A-Za-z0-9][-A-Za-z0-9]*)", demo)
    assert quoted, "DEMO.md documents no --from example, so this test guards nothing"
    known = {step.id for step in walkthrough.STEPS}
    assert set(quoted) <= known, f"DEMO.md names step ids the runner rejects: {set(quoted) - known}"


def test_actions_complete_before_pause_and_no_pause_never_reads_stdin() -> None:
    events: list[str] = []

    def action(page: object, base_url: str) -> None:
        events.append("proof")

    step = walkthrough.Step("proof", "Proof", "Business result is visibly proven.", action)
    modules = _playwright_modules(events)
    with (
        mock.patch.dict(sys.modules, modules),
        mock.patch.object(builtins, "input", side_effect=lambda prompt: events.append("pause")),
        mock.patch.object(walkthrough, "STEPS", (step,)),
    ):
        walkthrough.run(
            [step],
            base_url="http://example.test",
            slow_mo=0,
            pause=True,
            headless=True,
            screenshots=None,
        )
    assert events.index("proof") < events.index("pause")

    events.clear()
    with (
        mock.patch.dict(sys.modules, _playwright_modules(events)),
        mock.patch.object(builtins, "input", side_effect=AssertionError("stdin read")),
        mock.patch.object(walkthrough, "STEPS", (step,)),
    ):
        walkthrough.run(
            [step],
            base_url="http://example.test",
            slow_mo=0,
            pause=False,
            headless=True,
            screenshots=None,
        )


def test_browser_and_context_close_when_a_step_fails() -> None:
    events: list[str] = []

    def fail(page: object, base_url: str) -> None:
        raise RuntimeError("expected failure")

    step = walkthrough.Step("fail", "Fail", "A visible proof is attempted and refused.", fail)
    with (
        mock.patch.dict(sys.modules, _playwright_modules(events)),
        mock.patch.object(walkthrough, "STEPS", (step,)),
        pytest.raises(RuntimeError, match="expected failure"),
    ):
        walkthrough.run(
            [step],
            base_url="http://example.test",
            slow_mo=0,
            pause=False,
            headless=True,
            screenshots=None,
        )
    assert events[-2:] == ["context-close", "browser-close"]


def test_presenter_notes_are_spoken_prose_without_demo_mechanics() -> None:
    forbidden = ("localhost", "synthetic", "fictional", "selector", "iframe", "http")
    for step in walkthrough.STEPS:
        assert all(word not in step.presenter_notes.lower() for word in forbidden)
        assert len([part for part in step.presenter_notes.split(". ") if part.strip()]) >= 2
