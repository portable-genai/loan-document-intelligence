#!/usr/bin/env python3
"""Bounded executable portability proof for loan-document-intelligence.

This proof executes the local decision path, audit chain, profile map, managed construction
seam, and fail-fast on-premises boundary. It does not claim a live cloud deployment or a
completed sovereign adapter.
"""

from __future__ import annotations

from dataclasses import replace

import loan_doc_demo as demo

from loan_doc_intel.api.deps import build_loan_doc_service
from loan_doc_intel.config import LocalSettings, Settings, build_container

_BASELINE_PROFILES = {"local", "gcp", "onprem"}


def _settings(profile: str) -> Settings:
    base = Settings.load()
    return replace(
        base,
        profile=profile,
        profile_explicit=True,
        local=LocalSettings(audit_path=":memory:"),
    )


def _local_result() -> tuple[str, tuple[tuple[str, str], ...], bool, int]:
    applicant, documents, extracts = demo._inconsistent_scenario()
    container = build_container(_settings("local"))
    container.extraction.seed(extracts)
    case = build_loan_doc_service(container).process(applicant, documents, demo.PRINCIPAL)
    assert case.income is not None and case.validation is not None
    chain = container.audit.verify_chain()
    return (
        case.income.verdict.value,
        tuple((check.kind.value, check.status.value) for check in case.validation.checks),
        chain.ok,
        chain.chained,
    )


def main() -> int:
    base = Settings.load()
    assert all(set(bindings) >= _BASELINE_PROFILES for bindings in base.adapters.values())
    platform_ports = {name for name, bindings in base.adapters.items() if "platform" in bindings}
    assert {"guardrail", "redaction", "audit", "evaluation", "registry"} <= platform_ports
    print("PASS profile map: every port declares local, gcp, and onprem behavior")
    print("PASS platform seam: shared-horizontal ports declare explicit HTTP delegates")

    first = _local_result()
    second = _local_result()
    assert first == second
    assert first[0] == "inconsistent" and first[2] and first[3] == 1
    print("PASS local replay: identical inputs and policy produce identical cited decisions")
    print("PASS audit seam: the local run writes and verifies one hash-chained audit event")

    managed = build_container(_settings("gcp"))
    for port_name in ("extraction", "llm", "guardrail", "redaction"):
        getattr(managed, port_name)
    print("PASS managed seam: core GCP adapters construct without executing cloud calls")

    onprem = build_container(_settings("onprem"))
    try:
        onprem.extraction.extract(demo._clean_scenario()[1][0], b"", "application/pdf")
    except NotImplementedError:
        print("PASS exit boundary: an unimplemented on-premises adapter fails closed")
    else:
        raise AssertionError("on-premises extraction did not fail closed")

    print(
        "LIMITS not proved here: live GCP behavior, quality parity across models, completed "
        "on-premises adapters, cross-issuer identity, or managed WORM enforcement"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
