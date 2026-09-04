"""Contract test: the platform eval adapter speaks model-quality-gate's hardened /v1 contract.

model-quality-gate (``model-quality-gate``, A4) hardened its API, and this pins the client to it
(mocking the sibling service with respx, the same way ``test_behavioral_parity`` does):

* ``POST /v1/evaluations`` and ``POST /v1/gate`` take a structured ``target`` plus a top-level
  ``dataset_id`` that MUST equal ``target.dataset_id`` (model-quality-gate 422s otherwise); *
  metrics are selected server-side by the registered ``bundle`` : sending a metric name is now
  rejected, so the client must send none; * the evaluations response carries a ``results`` list (not
  ``metrics``), plus the evidence that lets somebody re-derive those scores later: a positive
  example count, a run id, a dataset version and digest, an evaluator, artifact refs and an
  attestation flag; * ``gate`` returns a verdict RE-DERIVED from a complete promotion decision,
  never the aggregate boolean the service reports.

The response fixtures below are large on purpose. The hardened ``agent-eval-kit`` client
recomputes every verdict from the evidence and raises on any contradiction, so a body cannot
simply assert that a promotion passed: each metric row's ``passed`` has to equal
``score >= threshold``, the red-team aggregate has to equal the AND of its rows, and the
top-level verdict has to equal (quality AND attested AND red team). The refusal tests are as
much the contract as the happy path, because the shape they reject, a verdict with nothing
behind it, is a promotion certified by nothing.
"""

from __future__ import annotations

import json

import pytest
import respx

from loan_doc_intel.adapters.platform.remote_evaluation import (
    RemoteEvaluationAdapter,
    RemoteEvaluationError,
)
from loan_doc_intel.config import Settings
from loan_doc_intel.domain.models import EvalReport

CONFIG_PATH = "config/settings.yaml"
BASE_URL = "http://localhost:8084"
BUNDLE = "doc5-loan-document-intelligence"
DATASET_PATH = "eval/datasets/golden_cases.jsonl"
DATASET_ID = "golden_cases"

#: Obviously fictional durable identifiers. Every one is REQUIRED by the hardened parse: a
#: score naming no run, no dataset state and no evaluator cannot be reproduced by anyone
#: reading the promotion record later, so it is a number rather than evidence.
_DIGEST = "sha256:feedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface"
_EVALUATOR = "hrz4-ai-quality (FICTIONAL)"
_DATASET_VERSION = "golden_cases@2026-08-01"
_MODEL_CARD_REF = "gs://fictional-hrz4-evidence/model-cards/doc5-loan-document-intelligence.md"
_MRM_REF = "gs://fictional-hrz4-evidence/mrm/doc5-loan-document-intelligence-2026-08.json"

# A representative model-quality-gate /v1/evaluations metric set (the "results" key, not "metrics").
# Every
# row is internally CONSISTENT: ``passed`` equals ``score >= threshold``.
_RESULTS = [
    {"metric": "extraction_accuracy", "score": 0.91, "threshold": 0.80, "passed": True},
    {"metric": "validation_recall", "score": 0.95, "threshold": 0.90, "passed": True},
    {"metric": "validation_precision", "score": 0.93, "threshold": 0.90, "passed": True},
    {"metric": "pii_safety", "score": 1.0, "threshold": 0.99, "passed": True},
]

#: The same suite with one genuine miss, so a FAIL can be reached through evidence.
_FAILING_RESULTS = [
    {"metric": "extraction_accuracy", "score": 0.61, "threshold": 0.80, "passed": False},
    {"metric": "validation_recall", "score": 0.95, "threshold": 0.90, "passed": True},
    {"metric": "validation_precision", "score": 0.93, "threshold": 0.90, "passed": True},
    {"metric": "pii_safety", "score": 1.0, "threshold": 0.99, "passed": True},
]

#: Red-team rows: ``passed`` and ``blocked`` must AGREE (an attack that was not blocked did
#: not pass), and the aggregate must equal the AND of the rows.
_REDTEAM_PASSING = {
    "passed": True,
    "results": [
        {"case": "prompt-injection-01", "passed": True, "blocked": True},
        {"case": "pii-exfil-01", "passed": True, "blocked": True},
    ],
}


def _eval_body(*, run_id: str, results: list[dict], attested: bool = True) -> dict:
    """A complete evaluation response in the hardened shape.

    ``passed`` is deliberately absent: the client derives the aggregate from the rows, and a
    value that disagrees with them is a hard error rather than an override.
    """
    return {
        "results": results,
        "n_examples": 6,
        "run_id": run_id,
        "dataset_version": _DATASET_VERSION,
        "dataset_digest": _DIGEST,
        "evaluator": _EVALUATOR,
        "schema_version": "v1",
        "artifact_refs": [f"gs://fictional-hrz4-evidence/{run_id}/report.json"],
        "attested": attested,
    }


def _gate_body(*, passed: bool, results: list[dict], attested: bool = True) -> dict:
    """The full promotion decision, at every layer the client re-derives."""
    return {
        "passed": passed,
        "eval_report": _eval_body(run_id="run-fictional-0001", results=results, attested=attested),
        "redteam_report": _REDTEAM_PASSING,
        "model_card_ref": _MODEL_CARD_REF,
        "mrm_evidence_ref": _MRM_REF,
    }


def _adapter() -> RemoteEvaluationAdapter:
    return RemoteEvaluationAdapter(Settings.load(CONFIG_PATH))


def _assert_valid_request_body(body: dict) -> None:
    """Assert the request body matches model-quality-gate's hardened contract."""
    # The target is a structured object carrying the pinned reasoning model + dataset id.
    target = body["target"]
    assert isinstance(target, dict)
    assert set(target) == {"model", "prompt_version", "dataset_id", "system"}
    assert target["model"] == Settings.load(CONFIG_PATH).models.reasoning
    assert target["dataset_id"] == DATASET_ID

    # Top-level dataset_id must mirror target.dataset_id (model-quality-gate 422s on divergence).
    assert body["dataset_id"] == target["dataset_id"] == DATASET_ID

    # Metrics are selected by the registered bundle, never by name.
    assert body["bundle"] == BUNDLE
    assert "metrics" not in body
    serialized = json.dumps(body)
    for item in _RESULTS:
        assert item["metric"] not in serialized, f"client leaked a metric name: {item['metric']}"


def test_evaluate_sends_hardened_contract_and_parses_results() -> None:
    with respx.mock:
        route = respx.post(f"{BASE_URL}/v1/evaluations").respond(
            200, json=_eval_body(run_id="run-fictional-0002", results=_RESULTS)
        )
        report = _adapter().evaluate(DATASET_PATH)
        body = json.loads(route.calls.last.request.content)

    assert route.called
    _assert_valid_request_body(body)

    # The "results" list is parsed into a domain EvalReport (not the old "metrics" key).
    assert isinstance(report, EvalReport)
    assert report.dataset == DATASET_PATH
    assert report.n_examples == 6
    assert report.passed is True
    assert {r.metric for r in report.results} == {item["metric"] for item in _RESULTS}
    accuracy = next(r for r in report.results if r.metric == "extraction_accuracy")
    assert accuracy.score == 0.91
    assert accuracy.threshold == 0.80
    assert accuracy.passed is True

    # The attested evidence SURVIVES the adapter. Red before: the adapter rebuilt a narrower
    # local EvalReport from three fields (dataset, results, n_examples), so everything below
    # arrived at its default and the caller held four numbers naming no run, no dataset state
    # and no evaluator -- the exact evidence the client had just finished validating.
    assert report.run_id == "run-fictional-0002"
    assert report.dataset_version == _DATASET_VERSION
    assert report.dataset_digest == _DIGEST
    assert report.evaluator == _EVALUATOR
    assert report.schema_version == "v1"
    assert report.artifact_refs == ("gs://fictional-hrz4-evidence/run-fictional-0002/report.json",)
    assert report.attested is True


def test_evaluate_REFUSES_the_legacy_metrics_key() -> None:
    """The legacy key must not be parsed, and an empty report is not the safe answer.

    Returning ``results == ()`` for a body the client did not understand looks harmless until
    something downstream reads it as "nothing failed". Refusing is the only reading that
    cannot be mistaken for a pass.
    """
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/evaluations").respond(200, json={"metrics": _RESULTS})
        with pytest.raises(RemoteEvaluationError):
            _adapter().evaluate(DATASET_PATH)


def test_evaluate_REFUSES_scores_with_no_durable_run_identity() -> None:
    """Metric rows on their own are numbers, not promotion evidence.

    The client enforces the durable identifiers on the plain evaluations path too, not
    only inside ``gate()``. Without a run id, a dataset digest, an evaluator and an artifact
    ref, nobody can later reproduce the score or say which corpus produced it.
    """
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/evaluations").respond(
            200, json={"results": _RESULTS, "n_examples": 6}
        )
        with pytest.raises(RemoteEvaluationError):
            _adapter().evaluate(DATASET_PATH)


def test_evaluate_REFUSES_a_row_whose_verdict_contradicts_its_score() -> None:
    """A row claiming PASS below its own bar is the failure a trusted flag always hides."""
    rows = [{"metric": "extraction_accuracy", "score": 0.41, "threshold": 0.80, "passed": True}]
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/evaluations").respond(
            200, json=_eval_body(run_id="run-fictional-0003", results=rows)
        )
        with pytest.raises(RemoteEvaluationError):
            _adapter().evaluate(DATASET_PATH)


def test_gate_posts_to_v1_gate_and_returns_true_on_a_full_decision() -> None:
    with respx.mock:
        route = respx.post(f"{BASE_URL}/v1/gate").respond(
            200, json=_gate_body(passed=True, results=_RESULTS)
        )
        passed = _adapter().gate(DATASET_PATH)
        request = route.calls.last.request
        body = json.loads(request.content)

    assert passed is True
    assert route.called
    assert request.method == "POST"  # POST, not GET.
    _assert_valid_request_body(body)


def test_gate_returns_false_through_evidence_that_actually_failed() -> None:
    """A FAIL has to be reached the honest way: a metric that genuinely missed its bar.

    A body claiming ``passed: false`` over evidence where everything passed is a
    contradiction and raises, so this fixture fails the extraction-accuracy row instead.
    """
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/gate").respond(
            200, json=_gate_body(passed=False, results=_FAILING_RESULTS)
        )
        assert _adapter().gate(DATASET_PATH) is False


def test_gate_REFUSES_a_naked_boolean_with_no_evidence() -> None:
    """The shape this file must never accept: a verdict with nothing behind it.

    An upstream that answers ``{"passed": true}`` for every target is indistinguishable from
    one that evaluated nothing at all, so the refusal is the contract, not an inconvenience.
    """
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/gate").respond(200, json={"passed": True})
        with pytest.raises(RemoteEvaluationError):
            _adapter().gate(DATASET_PATH)


def test_gate_REFUSES_an_unattested_report_even_when_every_metric_passes() -> None:
    """Unattested scores are a draft run, not sign-off, however good the numbers look."""
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/gate").respond(
            200, json=_gate_body(passed=True, results=_RESULTS, attested=False)
        )
        with pytest.raises(RemoteEvaluationError):
            _adapter().gate(DATASET_PATH)


def test_gate_REFUSES_a_redteam_aggregate_that_contradicts_its_rows() -> None:
    """A red-team summary reporting PASS over a case that was not blocked is a rubber stamp."""
    body = _gate_body(passed=True, results=_RESULTS)
    body["redteam_report"] = {
        "passed": True,
        "results": [
            {"case": "prompt-injection-01", "passed": True, "blocked": True},
            {"case": "pii-exfil-01", "passed": False, "blocked": False},
        ],
    }
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/gate").respond(200, json=body)
        with pytest.raises(RemoteEvaluationError):
            _adapter().gate(DATASET_PATH)


def test_gate_REFUSES_a_decision_with_no_model_card_reference() -> None:
    """Model-risk sign-off has to point at something durable, or it points at nothing."""
    body = _gate_body(passed=True, results=_RESULTS)
    body["model_card_ref"] = ""
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/gate").respond(200, json=body)
        with pytest.raises(RemoteEvaluationError):
            _adapter().gate(DATASET_PATH)


def test_non_2xx_raises_remote_evaluation_error() -> None:
    with respx.mock:
        respx.post(f"{BASE_URL}/v1/evaluations").respond(422, json={"detail": "bad dataset_id"})
        with pytest.raises(RemoteEvaluationError):
            _adapter().evaluate(DATASET_PATH)
