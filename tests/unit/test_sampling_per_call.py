"""Sampling is decided per call: pinned where the output is extracted, free where it is prose.

This file used to assert the opposite default. On 2026-08-26 two runs of one identical case
against the `cdd-sow-research` deployment returned different scores minutes apart, because the
shared request builder defaulted to `temperature=0.2` and every grounded call sampled. The fix
pinned 0.0 on the type, which made every call reproducible and every drafted narrative flat.

The owner's rule (2026-09-23) replaces it: temperature is PINNED at 0.0 only where
reproducibility matters (extraction, classification, scoring, anything compared against a
deterministic check) and FREE elsewhere, where free means the parameter is not sent at all,
because Opus 5 and Fable 5 reject it. So the type and the builder default to `None`, and each
call site that needs a pin says so where it is made.

This is a document-extraction service, so the pin is the common case here. Income normalisation
turns extracts into the figures the deterministic cross-validator compares, so it is pinned;
the triage `classify` is pinned in both model adapters. Only the hosted agent's own turn, which
narrates what its tools returned, is free.

**Temperature 0 is not a promise of determinism, and nothing here asserts one.**
"""

from __future__ import annotations

import inspect

from tests.fixtures import sample_docs

from loan_doc_intel.domain import _grounded
from loan_doc_intel.domain.identity import Principal
from loan_doc_intel.domain.kernel import LlmRequest

PRINCIPAL = Principal(
    subject="demo.analyst@bank.example",
    principals=("group:loan-analyst", "group:underwriting"),
    tenant="demo-bank",
    source="test",
)


def test_the_request_type_and_builder_leave_sampling_free_by_default() -> None:
    assert LlmRequest.__dataclass_fields__["temperature"].default is None
    signature = inspect.signature(_grounded.build_llm_request)
    assert signature.parameters["temperature"].default is None


def test_income_normalisation_is_pinned(loan_doc_service, llm) -> None:  # type: ignore[no-untyped-def]
    """Extraction: the figures feed the deterministic cross-validator, so runs must compare."""
    loan_doc_service.process(sample_docs.APPLICANT, list(sample_docs.DOCUMENTS), PRINCIPAL)
    assert llm.requests, "the case normalised nothing, so this asserts nothing"
    assert all(request.temperature == 0.0 for request in llm.requests)


def test_classification_stays_pinned_in_both_model_adapters() -> None:
    """Triage labels are compared against a fixed set, so they are pinned at 0.0."""
    from loan_doc_intel.adapters.gcp import gemini_llm
    from loan_doc_intel.adapters.live import llm as live_llm

    assert "temperature=0.0" in inspect.getsource(gemini_llm.GeminiLLMAdapter.classify)
    assert "temperature=0.0" in inspect.getsource(live_llm.LocalModelLLMAdapter.classify)


def test_the_hosted_agent_turn_sends_no_temperature() -> None:
    """The agent narrates its tools' results; the figures and verdicts are the tools'.

    Read from source because the agent is built with the ADK, which the offline gate does not
    install. A pin here would be sent to whatever reasoning model is configured, and some reject
    the parameter outright.
    """
    from loan_doc_intel.agent import root_agent

    assert "temperature=" not in inspect.getsource(root_agent.build_root_agent)
