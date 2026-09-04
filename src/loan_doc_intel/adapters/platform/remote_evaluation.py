"""Remote-platform evaluation adapter : thin HTTP client to model-quality-gate.

At promotion this vertical's quality is checked against the shared **model-quality-gate AI Quality /
model-risk** service (``model-quality-gate``). This adapter implements
:class:`EvaluationGatePort` against model-quality-gate's hardened contract:

* ``evaluate`` -> ``POST /v1/evaluations {target, dataset_id, bundle}`` -> EvalReport.
* ``gate``     -> ``POST /v1/gate {target, dataset_id, bundle}`` -> ``{passed}``.

**Sourced from the shared ``agent-eval-kit`` commons.** The HTTP contract
is ``agent_eval_kit.gate_client.PromotionGateClient``; this adapter configures it (the
registered ``doc5-loan-document-intelligence`` bundle, the reasoning model, and this repo's S2S auth
headers) and re-raises its errors as :class:`RemoteEvaluationError`. It does NOT re-map the
report: the domain ``EvalReport`` is now the same ``agent_eval_kit.report.EvalReport`` the client
returns, so the attested evidence (run id, dataset version and digest, evaluator, artifact refs)
reaches the caller instead of being dropped by a lossy local rebuild.
"""

from __future__ import annotations

from agent_eval_kit.gate_client import GateClientError, PromotionGateClient

from ...config import Settings
from ...domain.errors import LoanDocError
from ...domain.models import EvalReport
from ...envread import setting_or_default
from . import _s2s

_DEFAULT_URL = "http://localhost:8084"

#: The registered model-quality-gate metric bundle for this vertical (model-quality-gate owns the
#: metrics + bars).
_BUNDLE = "doc5-loan-document-intelligence"
#: Prompt/agent version tag; bump when the prompt corpus changes, or source it from a registry.
_PROMPT_VERSION = "v1"


class RemoteEvaluationError(LoanDocError):
    """Raised when the model-quality-gate quality service returns a non-2xx response."""


# A ``_to_domain`` mapper here, rebuilding a narrower local ``EvalReport`` from the gate
# client's report with three fields (dataset, results, n_examples), would be an identity
# function that loses data, because the domain type IS ``agent_eval_kit.report.EvalReport``:
# it silently drops the run id, dataset version and digest, evaluator, schema version,
# artifact refs and the attestation flag -- exactly the evidence the client had just
# validated, and the only thing that lets somebody re-derive a promotion decision months
# later. The client's report is returned unchanged.


class RemoteEvaluationAdapter:
    """HTTP client for the model-quality-gate ``model-quality-gate`` service (via
    PromotionGateClient).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = PromotionGateClient(
            setting_or_default("QUALITY_GATE_URL", _DEFAULT_URL),
            bundle=_BUNDLE,
            model=settings.models.reasoning,
            prompt_version=_PROMPT_VERSION,
            auth_headers=lambda: _s2s.headers(),
        )

    def evaluate(self, dataset_path: str) -> EvalReport:
        """Score ``dataset_path`` via model-quality-gate and return the report it attested,
        unmodified.
        """
        try:
            return self._client.evaluate(dataset_path)
        except GateClientError as exc:
            raise RemoteEvaluationError(str(exc)) from exc

    def gate(self, target: str) -> bool:
        """Promotion gate: True iff model-quality-gate reports ``target`` passes."""
        try:
            return self._client.gate(target)
        except GateClientError as exc:
            raise RemoteEvaluationError(str(exc)) from exc
