"""Domain exceptions for the Loan / Mortgage Document Intelligence service (system B5).

Pure-Python exception hierarchy raised by the orchestration services. The domain
layer never imports Google Cloud, ADK, or any framework : these errors let callers
(API, CLI, the Agent Runtime adapter) react to domain-level failures without coupling
to any vendor SDK error type.
"""

from __future__ import annotations


class LoanDocError(Exception):
    """Base class for all domain-level errors raised by B5 services."""


class GuardrailBlockedError(LoanDocError):
    """Raised/flagged when the A1 guardrail blocks an input or output.

    The processing pipeline generally degrades gracefully (returns a case flagged for
    human review) rather than raising, but the standalone extract/validate entry points
    raise this so a blocked unsafe request never yields a partial artifact.
    """


class ExtractionEmptyError(LoanDocError):
    """Raised when document extraction yields no usable fields for any document.

    A cross-validation run with nothing to validate is a hard error rather than a
    silently-passing empty result : the underwriter must not be shown a vacuous PASS.
    """


class NoDocumentsError(LoanDocError):
    """Raised when an application is submitted with no documents to process."""


class AccessDenied(LoanDocError):
    """Raised when a verified principal is not entitled to a requested object.

    Authentication proves WHO is calling; this proves WHAT they may act on. The owning
    tenant (and permitted roles) of an application or document is resolved SERVER-SIDE from
    an entitlements registry keyed by object id (see ``domain/entitlements.py`` and the
    ``EntitlementsPort`` adapters), never from the client-submitted request body. A failed,
    fail-closed entitlement check maps to HTTP 403 at the API layer, and the object is never
    extracted, validated, or audited.
    """
