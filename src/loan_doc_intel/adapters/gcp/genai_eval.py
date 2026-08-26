"""Gen AI evaluation gate adapter : the A4 promotion gate for system B5.

Backs the domain ``EvaluationGatePort`` with the **Gen AI evaluation service**, accessed
through ``vertexai.Client(project, location).evals``. Over a golden dataset it scores B5
on the metrics that matter for a document-intelligence system : extraction accuracy,
validation recall (catches planted inconsistencies), validation precision (no false flags)
and PII safety : and maps the result onto an ``EvalReport`` whose ``passed`` flag gates
promotion in CI/CD.

The Vertex AI SDK import is lazy so the on-prem and test profiles import without it.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import EvalMetricResult, EvalReport

# Promotion thresholds (0..1). Mirror eval/run_eval.py and eval/rubrics/*.yaml.
_THRESHOLDS: dict[str, float] = {
    "extraction_accuracy": 0.80,
    "validation_recall": 0.90,
    "validation_precision": 0.90,
    "pii_safety": 0.99,
}


class GenAiEvalAdapter:
    """Run the Gen AI evaluation service and map results to a domain ``EvalReport``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    def _evals(self) -> Any:
        """Return (and cache) the ``evals`` surface of the Vertex AI client."""
        if self._client is None:
            import vertexai  # lazy

            # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/models/evaluation
            self._client = vertexai.Client(
                project=self._settings.project_id,
                # MODEL location, not the compute region.
                location=self._settings.models.location,
            )
        return self._client.evals

    def gate(self, target: str) -> bool:
        """Promotion gate: True iff ``target`` clears every threshold."""
        return self.evaluate(target).passed

    def evaluate(self, dataset_path: str) -> EvalReport:
        """Score the golden dataset at ``dataset_path`` and return a pass/fail report."""
        evals = self._evals()
        metrics = self._metrics()
        # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/models/run-evaluation
        inference = evals.run_inference(
            model=self._settings.models.reasoning,
            src=dataset_path,
        )
        result = evals.evaluate(dataset=inference, metrics=metrics)
        return self._to_report(dataset_path, result)

    def _metrics(self) -> list[Any]:
        """Build the metric objects from the eval metric library (string fallback)."""
        try:
            from vertexai import types as eval_types  # lazy
        except Exception:  # noqa: BLE001
            return list(_THRESHOLDS.keys())
        prebuilt = getattr(eval_types, "PrebuiltMetric", None)
        if prebuilt is None:
            return list(_THRESHOLDS.keys())
        names = {
            "extraction_accuracy": "EXTRACTION_ACCURACY",
            "validation_recall": "RECALL",
            "validation_precision": "PRECISION",
            "pii_safety": "SAFETY",
        }
        metrics: list[Any] = []
        for key, attr in names.items():
            metric = getattr(prebuilt, attr, None)
            metrics.append(metric if metric is not None else key)
        return metrics

    def _to_report(self, dataset_path: str, result: Any) -> EvalReport:
        scores = _extract_summary_scores(result)
        n_examples = _extract_n_examples(result)
        rows: list[EvalMetricResult] = []
        for metric, threshold in _THRESHOLDS.items():
            score = float(scores.get(metric, 0.0))
            rows.append(
                EvalMetricResult(
                    metric=metric,
                    score=score,
                    threshold=threshold,
                    passed=score >= threshold,
                )
            )
        return EvalReport(dataset=dataset_path, results=tuple(rows), n_examples=n_examples)


def _extract_summary_scores(result: Any) -> dict[str, float]:
    """Normalise the eval result's summary metrics into a ``{metric: score}`` dict."""
    raw = getattr(result, "summary_metrics", None)
    if raw is None:
        raw = getattr(result, "metrics", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("summary_metrics") or result.get("metrics")

    scores: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            scores[_norm_metric(key)] = _coerce_score(value)
        return scores
    for entry in raw or []:
        name = getattr(entry, "name", None) or (
            entry.get("name") if isinstance(entry, dict) else None
        )
        if name is None:
            continue
        value = getattr(entry, "score", None) if not isinstance(entry, dict) else entry.get("score")
        if value is None:
            value = (
                getattr(entry, "mean_score", None)
                if not isinstance(entry, dict)
                else entry.get("mean_score")
            )
        scores[_norm_metric(name)] = _coerce_score(value)
    return scores


def _norm_metric(name: str) -> str:
    key = str(name).lower()
    for suffix in ("/mean", "_mean", "/score", "_score"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    return key


def _coerce_score(value: Any) -> float:
    if isinstance(value, dict):
        value = value.get("mean") or value.get("score") or value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract_n_examples(result: Any) -> int:
    for attr in ("n_examples", "row_count", "num_examples"):
        value = getattr(result, attr, None)
        if value is None and isinstance(result, dict):
            value = result.get(attr)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    metrics_table = getattr(result, "metrics_table", None)
    if metrics_table is not None:
        try:
            return int(len(metrics_table))
        except TypeError:
            return 0
    return 0
