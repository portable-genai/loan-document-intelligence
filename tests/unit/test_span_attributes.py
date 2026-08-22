"""Span ATTRIBUTES carry structure, never applicant content, and this is the test that sees it.

The conftest ``RecordingTracer`` records span NAMES (``self.spans.append(name)``), which is
the right shape for the test asserting the pipeline opened its span, and structurally blind
to the one defect that matters here: it discards ``**attributes``, so a span that started
carrying the applicant's name, address, or a document uri would keep every existing test
green.

A trace backend is not the WORM audit trail. It has no redaction stage, a far wider read
audience and no retention rule written against a regulator's requirement, so a span
attribute is OUTSIDE the boundary that redact-before-everything (R1 / P-04) holds. Note the
ordering that makes this sharp: the span opens BEFORE ``_process_inner`` redacts anything, so
an attribute built from the request would carry raw applicant PII by construction.

The recorder below keeps ``dict(attributes)``, and the content case drives both request paths
with an applicant whose name embeds a planted NRIC and email (the same literals the redaction
tests use), so a leak fails on a planted literal rather than on a subtlety.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest
from tests.fixtures import sample_docs

from loan_doc_intel.domain.identity import Principal

_ACTOR = "underwriter@bank.test"

#: A verified demo-bank underwriter entitled to the seeded fixture application, matching
#: tests/unit/test_loan_doc_service.py: the object-level authorization runs before the span.
_PRINCIPAL = Principal(
    subject=_ACTOR,
    principals=("group:loan-analyst", "group:underwriting"),
    tenant="demo-bank",
    source="test",
)

#: The complete attribute key set a loan-doc span may carry, per span name. Widening one of
#: these is a decision about what leaves the trust boundary, so it is made here deliberately
#: rather than by adding a keyword argument at a call site.
_ALLOWED: dict[str, set[str]] = {
    "loan_doc.process": {"action", "actor"},
    "loan_doc.extract": {"action", "actor"},
}

#: Planted into the applicant's name below. A content-shaped attribute would carry one.
_PLANTED_NRIC = "S1234567A"
_PLANTED_EMAIL = "jordan.fictional@example.com"

#: The applicant id is preserved so the entitlements check still resolves the same owner;
#: only the free-text name changes, which is exactly where a leak would come from.
_PII_APPLICANT = replace(
    sample_docs.APPLICANT,
    name=f"Jordan Tester Fictional, NRIC {_PLANTED_NRIC}, email {_PLANTED_EMAIL}",
)


class _AttributeRecordingTracer:
    """Keeps (name, attributes) per span, unlike the name-only conftest recorder."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, str]]] = []

    @contextmanager
    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.spans.append((name, dict(attributes)))
        yield

    def record_token_usage(self, usage, model):  # type: ignore[no-untyped-def]
        return None


@pytest.fixture
def tracer() -> _AttributeRecordingTracer:  # type: ignore[override]
    """Override the conftest tracer so ``loan_doc_service`` assembles with THIS recorder."""
    return _AttributeRecordingTracer()


def _drive_both_paths(service, applicant) -> None:
    service.process(applicant, list(sample_docs.DOCUMENTS), _PRINCIPAL)
    service.extract_only(sample_docs.DOCUMENTS[0], b"", "application/pdf", _PRINCIPAL)


def test_both_request_paths_open_their_named_spans_with_allowlisted_keys_only(
    loan_doc_service, tracer
) -> None:
    _drive_both_paths(loan_doc_service, sample_docs.APPLICANT)
    assert [name for name, _ in tracer.spans] == ["loan_doc.process", "loan_doc.extract"]
    for name, attributes in tracer.spans:
        assert set(attributes) == _ALLOWED[name], (
            f"span {name!r} attribute keys changed; widening the set is a trust-boundary "
            "decision, so update _ALLOWED here deliberately"
        )


def test_no_span_attribute_carries_the_planted_identifiers(loan_doc_service, tracer) -> None:
    """The applicant's name embeds an NRIC and an email; neither may reach a span."""
    _drive_both_paths(loan_doc_service, _PII_APPLICANT)
    emitted = " ".join(
        str(value) for _, attributes in tracer.spans for value in attributes.values()
    )
    for planted in (_PLANTED_NRIC, _PLANTED_EMAIL, "Jordan Tester Fictional"):
        assert planted not in emitted, f"{planted!r} reached a span attribute"
        assert planted.lower() not in emitted.lower()


def test_no_span_attribute_carries_an_applicant_or_document_identifier(
    loan_doc_service, tracer
) -> None:
    """Case and document ids are content here too: they name a real person's file."""
    _drive_both_paths(loan_doc_service, sample_docs.APPLICANT)
    emitted = " ".join(
        str(value) for _, attributes in tracer.spans for value in attributes.values()
    )
    assert sample_docs.APPLICANT.id not in emitted
    assert sample_docs.APPLICANT.address not in emitted
    for document in sample_docs.DOCUMENTS:
        assert document.id not in emitted
        assert document.uri not in emitted


def test_every_attribute_value_is_a_string(loan_doc_service, tracer) -> None:
    """The port declares str values; a structured object smuggles content past a grep."""
    _drive_both_paths(loan_doc_service, sample_docs.APPLICANT)
    for name, attributes in tracer.spans:
        for key, value in attributes.items():
            assert isinstance(value, str), f"span {name!r} attribute {key!r} is not a str"
