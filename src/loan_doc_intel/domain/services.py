"""Aggregator re-exporting the B5 orchestration services + the deterministic core.

The orchestrator (``loan_doc_service``), the deterministic validator
(``cross_validator``) and the income summariser (``income_service``) live one-per-module
so each stays focused. This module is the single import surface the wiring layers
(``api.deps``, ``agent.tools``, ``cli``) use, so they never need to know which module a
service lives in.
"""

from __future__ import annotations

from .cross_validator import CrossValidator
from .income_service import IncomeVerificationService
from .loan_doc_service import LoanDocService

__all__ = [
    "LoanDocService",
    "CrossValidator",
    "IncomeVerificationService",
]
