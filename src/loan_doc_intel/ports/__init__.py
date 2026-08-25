"""Ports : the abstract interfaces (the hexagon boundary).

Every port is a ``typing.Protocol`` so adapters need only structural conformance and
contract tests can verify any adapter (GCP, remote-platform, or on-prem placeholder)
satisfies the same contract.
"""

from .entitlements import EntitlementsPort
from .extraction import DocumentExtractionPort
from .generation import LLMPort
from .governance import AgentRegistryPort, ToolCatalogPort
from .identity import EndUserAuthUnavailableError, IdentityPort
from .observability import (
    AuditSinkPort,
    EvaluationGatePort,
    ObservabilityTracerPort,
    TokenUsage,
)
from .review_router import ReviewRouterPort
from .runtime import AgentRuntimePort, MemoryPort, SessionPort
from .safety import GuardrailPort, PIIRedactionPort

__all__ = [
    "DocumentExtractionPort",
    "LLMPort",
    "GuardrailPort",
    "PIIRedactionPort",
    "AgentRuntimePort",
    "SessionPort",
    "MemoryPort",
    "AuditSinkPort",
    "ObservabilityTracerPort",
    "TokenUsage",
    "EvaluationGatePort",
    "AgentRegistryPort",
    "ToolCatalogPort",
    "IdentityPort",
    "EndUserAuthUnavailableError",
    "EntitlementsPort",
    "ReviewRouterPort",
]
