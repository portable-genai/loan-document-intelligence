"""On-prem placeholder for ``AgentRuntimePort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile this
port binds to the Agent Runtime adapter; switching ``profile`` to ``onprem`` rebinds it
here. The adapter constructs cleanly with **no external dependencies** and structurally
satisfies the same Protocol as the managed adapter, so the contract tests prove interface
parity. Porting the hosted agent runtime on-premise is *only* a matter of filling these
bodies in : the domain is unchanged.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import LoanApplicationCase, Session

_MESSAGE = (
    "On-prem AgentRuntimePort adapter is a migration placeholder; implement against your "
    "on-premise platform. Core domain logic is unchanged."
)


class OnPremAgentRuntimeAdapter:
    """Placeholder agent-runtime adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def query(self, session: Session, message: str) -> LoanApplicationCase:
        raise NotImplementedError(_MESSAGE)

    def health(self) -> bool:
        raise NotImplementedError(_MESSAGE)
