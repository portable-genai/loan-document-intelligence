"""Contract tests: the ``onprem`` and ``local`` adapters are structural parity of the ports.

For every port the catalog declares, this iterates the adapter map and, for both the
``onprem`` and ``local`` profiles, imports + constructs the bound class (which must build
cleanly with **no Google Cloud SDK** installed), then asserts:

  1. the constructed instance satisfies its runtime_checkable Protocol (isinstance), and
  2. every method/property the Protocol declares actually exists on the instance.

It additionally proves the two profiles' distinct contracts:

* ``onprem`` is the fail-fast Google Distributed Cloud migration target: every method
  raises ``NotImplementedError`` (proven on a representative port), and
* ``local`` is a WORKING offline stack: the same ports construct and extract in-process.

This is the proof of the ports-and-adapters / no-lock-in promise (P-02): the on-prem
migration target and the offline local stack implement the exact same interface as the
managed GCP stack.
"""

from __future__ import annotations

from typing import Protocol, get_type_hints

import pytest

from loan_doc_intel import config, ports
from loan_doc_intel.config import LocalSettings, Settings, instantiate

CONFIG_PATH = "config/settings.yaml"

# Every port name in settings.adapters mapped to its Protocol.
PORT_PROTOCOLS: dict[str, type] = {
    "extraction": ports.DocumentExtractionPort,
    "llm": ports.LLMPort,
    "guardrail": ports.GuardrailPort,
    "redaction": ports.PIIRedactionPort,
    "agent_runtime": ports.AgentRuntimePort,
    "session": ports.SessionPort,
    "memory": ports.MemoryPort,
    "audit": ports.AuditSinkPort,
    "tracer": ports.ObservabilityTracerPort,
    "evaluation": ports.EvaluationGatePort,
    "registry": ports.AgentRegistryPort,
    "tool_catalog": ports.ToolCatalogPort,
    "identity": ports.IdentityPort,
    "entitlements": ports.EntitlementsPort,
    "review_router": ports.ReviewRouterPort,
}

# Profiles whose adapters must construct + satisfy the Protocols with no GCP SDK.
SDK_FREE_PROFILES = ("onprem", "local")


def _settings(profile: str) -> Settings:
    base = Settings.load(CONFIG_PATH)
    # Point the local audit store at in-memory SQLite so the contract test stays ephemeral.
    return Settings(
        project_id=base.project_id,
        region=base.region,
        profile=profile,
        kms_key=base.kms_key,
        models=base.models,
        document_ai=base.document_ai,
        model_armor=base.model_armor,
        dlp=base.dlp,
        logging=base.logging,
        agent_engine=base.agent_engine,
        validation=base.validation,
        local=LocalSettings(audit_path=":memory:"),
        adapters=base.adapters,
    )


def _protocol_members(protocol: type) -> set[str]:
    """The attribute names a Protocol declares (methods + properties), no dunders."""
    members = set(getattr(protocol, "__protocol_attrs__", set()))
    if not members:
        # Fallback for older typing internals: union of annotations + callables.
        members |= set(get_type_hints(protocol).keys())
        for name in dir(protocol):
            if name.startswith("_"):
                continue
            members.add(name)
    return {m for m in members if not m.startswith("_")}


def test_port_protocols_matches_settings_adapters():
    """The hand-maintained PORT_PROTOCOLS map must EQUAL the ports bound in settings.

    ``test_every_port_has_onprem_and_local_bindings`` only iterates ``PORT_PROTOCOLS``, so it
    is blind to a settings binding for a port that is *not* in the map: a fork that adds a
    port Protocol and binds it in ``config/settings.yaml`` under ``adapters:`` but forgets the
    ``PORT_PROTOCOLS`` entry would get ZERO parity / constructor / onprem-binding enforcement
    with a still-green CI (silent drift). This set-equality assertion closes that gap and
    fails loudly on drift in BOTH directions:

    * a port bound in ``settings.adapters`` but absent from ``PORT_PROTOCOLS`` (untested), and
    * a port declared in ``PORT_PROTOCOLS`` with no ``settings.adapters`` binding (dangling).
    """
    settings = Settings.load(CONFIG_PATH)
    bound = set(settings.adapters)
    declared = set(PORT_PROTOCOLS)
    missing_from_map = bound - declared
    missing_from_settings = declared - bound
    assert not missing_from_map, (
        f"ports bound in settings.adapters but absent from PORT_PROTOCOLS "
        f"(so untested): {sorted(missing_from_map)}. Add them to the parity map."
    )
    assert not missing_from_settings, (
        f"ports in PORT_PROTOCOLS with no settings.adapters binding: "
        f"{sorted(missing_from_settings)}."
    )


def test_every_port_has_an_explicit_binding_for_every_profile():
    settings = Settings.load(CONFIG_PATH)
    for port_name in PORT_PROTOCOLS:
        binding = settings.adapters.get(port_name, {})
        missing = set(config.RUNTIME_PROFILES) - set(binding)
        assert not missing, f"port '{port_name}' has no explicit bindings for {sorted(missing)}"


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_satisfies_protocol(profile: str, port_name: str):
    settings = _settings(profile)
    protocol = PORT_PROTOCOLS[port_name]
    dotted = settings.adapters[port_name][profile]

    # Import + construct with only Settings (the adapter convention), no GCP SDK.
    adapter = instantiate(dotted, settings)

    # 1. Structural conformance via runtime_checkable Protocol.
    assert isinstance(adapter, protocol), (
        f"{dotted} does not structurally satisfy {protocol.__name__}"
    )

    # 2. Every declared Protocol member exists. Check on the *class* (via the MRO), not the
    #    instance: a placeholder property getter may raise, so ``hasattr`` would wrongly
    #    report it missing. Looking the name up on the type tests for declaration without
    #    invoking the getter.
    members = _protocol_members(protocol)
    declared = set().union(*(vars(klass) for klass in type(adapter).__mro__))
    for member in members:
        assert member in declared, (
            f"{dotted} is missing port method/attr '{member}' of {protocol.__name__}"
        )


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_constructs_with_single_settings_arg(profile: str, port_name: str):
    """The build contract: every adapter is ``Adapter(settings: Settings)``."""
    settings = _settings(profile)
    dotted = settings.adapters[port_name][profile]
    module_path, _, class_name = dotted.partition(":")
    import importlib

    cls = getattr(importlib.import_module(module_path), class_name)
    instance = cls(settings)
    assert instance is not None


def test_onprem_extraction_fails_fast():
    """The on-prem stubs are fail-fast: a representative port raises NotImplementedError."""
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["extraction"]["onprem"], settings)
    from loan_doc_intel.domain.models import ApplicantDocument, DocType

    with pytest.raises(NotImplementedError):
        adapter.extract(
            ApplicantDocument(id="doc-1", doc_type=DocType.PAYSLIP, uri=""),
            b"",
            "application/pdf",
        )


def test_local_extraction_returns_real_extract():
    """The local stack is WORKING: extraction returns real, structured fields offline."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["extraction"]["local"], settings)
    from loan_doc_intel.domain.models import ApplicantDocument, DocType

    extract = adapter.extract(
        ApplicantDocument(id="doc-payslip-2026-04", doc_type=DocType.PAYSLIP, uri=""),
        b"",
        "application/pdf",
    )
    assert extract.fields, "local extraction returned no fields for the seeded document"
    assert extract.confidence > 0.0
    assert extract.citations and extract.citations[0].source_id == "doc-payslip-2026-04"


def test_the_shared_types_ARE_the_commons_objects_not_look_alikes():
    """Object identity, which is the only assertion a hand-copied redeclaration cannot satisfy.

    Every other check in this file passes against a copy. ``isinstance`` against a
    ``runtime_checkable`` Protocol is structural, so a locally redeclared
    ``ObservabilityTracerPort``
    with the same two method names satisfies it; a frozen dataclass with the same three int fields
    satisfies every assertion anybody thought to write about ``TokenUsage``. That is precisely how
    sixteen hand-copied copies of these types drifted apart while every suite stayed green.

    ``is`` cannot be satisfied by a look-alike. If someone reintroduces a local declaration, this
    test is the one that notices.
    """
    import agent_eval_kit
    import hex_service_kit.identity
    import hex_service_kit.observability

    from loan_doc_intel.domain import identity as domain_identity
    from loan_doc_intel.domain import models

    assert ports.ObservabilityTracerPort is hex_service_kit.observability.ObservabilityTracerPort
    assert ports.TokenUsage is hex_service_kit.observability.TokenUsage
    assert models.TokenUsage is hex_service_kit.observability.TokenUsage

    assert ports.EvaluationGatePort is agent_eval_kit.EvaluationGatePort
    assert models.EvalReport is agent_eval_kit.EvalReport
    assert models.EvalMetricResult is agent_eval_kit.EvalMetricResult

    assert ports.IdentityPort is hex_service_kit.identity.IdentityPort
    assert domain_identity.Principal is hex_service_kit.identity.Principal
    assert domain_identity.RequestContext is hex_service_kit.identity.RequestContext
    assert domain_identity.IdentityError is hex_service_kit.identity.IdentityError


def test_all_protocols_are_runtime_checkable():
    for protocol in PORT_PROTOCOLS.values():
        assert issubclass(protocol, Protocol)  # type: ignore[arg-type]
        assert getattr(protocol, "_is_runtime_protocol", False), (
            f"{protocol.__name__} must be @runtime_checkable"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
