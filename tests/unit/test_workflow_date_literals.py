"""No CI step may gate itself on a hardcoded calendar date.

This pins the class of defect that produced the temporary ``npm audit`` exception in
``.github/workflows/ci.yaml``: a step that compared ``$(date -u +%F)`` against a literal
``2026-08-06`` and, until that day, waved through advisories from an allowlist. Two things
make the shape indefensible rather than merely untidy.

1. **It rots silently.** Nothing fails on the expiry date except the build, at a moment
   nobody chose, with a message about an exception whose original justification (a sharp
   advisory reached transitively through Next) had already vanished from the report.
2. **It gates on the wrong axis.** The finding that actually failed that step was nanoid
   GHSA-2v37-7h3g-55p8, which was never on the allowlist. The date was decoration; the
   block would have failed on any date.

A suppression worth having is one that names the advisory and fails when the advisory is
gone. A date literal names nothing. The remedy in every case is the plain hard gate the
rest of the supply-chain job already uses, so this test forbids the shape outright rather
than trying to distinguish a healthy expiry from a rotten one.

Only ``date``-comparison shapes are rejected. A YYYY-MM-DD that appears in prose, in an
action pin comment, or in a cron expression is untouched: the defect is the *conditional*,
not the date.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"

# A literal calendar date on either side of a shell/YAML comparison, or fed to `date`.
# Deliberately broad: `[[ "$(date -u +%F)" > "2026-08-06" ]]`, `if [ "$TODAY" \> 2026-08-06 ]`,
# and `date +%s -d 2026-08-06` all read as the same rot.
_DATE_LITERAL = re.compile(r"\d{4}-\d{2}-\d{2}")
_COMPARISON = re.compile(r"(?:[<>]=?|-(?:gt|lt|ge|le|eq|ne)\b|==|!=)")
_DATE_CALL = re.compile(r"\bdate\b\s*(?:-u\b|\+%|--date|-d\b)")


def _workflow_files() -> list[Path]:
    if not _WORKFLOWS.is_dir():
        return []
    return sorted(p for p in _WORKFLOWS.rglob("*") if p.suffix in {".yaml", ".yml"})


def test_workflow_directory_is_present() -> None:
    """Guard the guard: an empty sweep must not read as a pass."""
    assert _workflow_files(), f"no workflow files found under {_WORKFLOWS}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_no_date_literal_conditional(path: Path) -> None:
    offenders: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not _DATE_LITERAL.search(line):
            continue
        if not _DATE_CALL.search(line):
            continue
        if not _COMPARISON.search(line):
            continue
        offenders.append(f"{path.relative_to(_ROOT)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "CI step gated on a hardcoded calendar date; such a gate expires silently and "
        "usually stops matching the finding it was written for. Replace it with a hard "
        "gate, or with a suppression keyed to the advisory id:\n" + "\n".join(offenders)
    )
