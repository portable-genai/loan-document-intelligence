"""Prove every eval metric can go RED: a degraded case must score below its threshold.

A metric that cannot fail proves nothing. Each scorer in ``eval/run_eval.py`` is fed the SAME
case result twice: once as the service produced it (green) and once carrying exactly the defect
the metric exists to catch (red). The scorers are imported rather than re-implemented, so a
scorer that silently became a constant 1.0 breaks this build.

Recall and precision are measured on different case shapes by design (recall needs planted
inconsistencies, precision needs a consistent case), so each proof uses the shape its metric
applies to; the other returns ``None`` and is not scored at all.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from agent_eval_kit import assert_can_go_red
from eval.run_eval import (
    DEFAULT_DATASET,
    THRESHOLDS,
    _CaseResult,
    _run_case,
    load_golden,
    score_extraction_accuracy,
    score_pii_safety,
    score_validation_precision,
    score_validation_recall,
)

_GOLDEN = load_golden(DEFAULT_DATASET)
#: Planted inconsistencies, so validation_recall has something to catch.
_INCONSISTENT = next(c for c in _GOLDEN if c.expected_failed_checks)
#: No expected failures, so validation_precision can detect a false positive.
_CONSISTENT = next(c for c in _GOLDEN if not c.expected_failed_checks)
#: A planted applicant identifier, so pii_safety has a target to miss.
_WITH_PII = next(c for c in _GOLDEN if c.pii_in_inputs)


@pytest.fixture(scope="module")
def inconsistent() -> _CaseResult:
    result = _run_case(_INCONSISTENT)
    assert result.failed_kinds, "the proof needs a case whose validator actually failed a check"
    return result


def test_extraction_accuracy_can_go_red() -> None:
    empty = replace(
        _INCONSISTENT,
        extracts=[{**e, "fields": {}, "line_items": []} for e in _INCONSISTENT.extracts],
    )
    assert_can_go_red(
        score_extraction_accuracy,
        green=_INCONSISTENT,
        red=empty,  # every document extracted to nothing
        threshold=THRESHOLDS["extraction_accuracy"],
        metric="extraction_accuracy",
    )


def test_validation_recall_can_go_red(inconsistent: _CaseResult) -> None:
    assert_can_go_red(
        lambda result: score_validation_recall(result) or 0.0,
        green=inconsistent,
        red=replace(inconsistent, failed_kinds=set()),  # the validator stopped validating
        threshold=THRESHOLDS["validation_recall"],
        metric="validation_recall",
    )


def test_validation_precision_can_go_red() -> None:
    consistent = _run_case(_CONSISTENT)
    assert_can_go_red(
        lambda result: score_validation_precision(result) or 0.0,
        green=consistent,
        red=replace(
            consistent, failed_kinds={"salary_credit_match"}
        ),  # a check failed on a consistent file
        threshold=THRESHOLDS["validation_precision"],
        metric="validation_precision",
    )


def test_pii_safety_can_go_red() -> None:
    """The red case re-introduces a raw identifier into the audit trail AFTER redaction ran."""
    result = _run_case(_WITH_PII)
    events = list(result.audit_events)
    leaked = copy.copy(events[0])
    object.__setattr__(
        leaked,
        "redacted_prompt",
        f"{getattr(leaked, 'redacted_prompt', '') or ''} applicant NRIC S1234567D",
    )
    assert_can_go_red(
        score_pii_safety,
        green=result,
        red=replace(result, audit_events=[leaked, *events[1:]]),
        threshold=THRESHOLDS["pii_safety"],
        metric="pii_safety",
    )
