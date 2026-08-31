"""Configuration and the adapter factory (dependency injection for the hexagon).

The factory reads ``config/settings.yaml`` (with ``${ENV_VAR}`` interpolation) and binds
each port to a concrete adapter by dotted path. Switching the whole system from the GCP
managed stack to an on-prem stack is a one-line change of ``profile`` : proof of the
ports-and-adapters / no-lock-in principle (P-02). Every adapter follows one construction
convention: ``Adapter(settings: Settings)``.
"""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from hex_service_kit import EnvSetting

from .domain import pii_patterns
from .envread import ConfiguredEmptyError, read_env_setting, setting_or_default

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")

_PROFILE_ENV = "LOAN_DOC_PROFILE"

#: Every profile that binds an adapter family. ``local`` is the SDK-free offline stack,
#: ``gcp`` and ``platform`` are the managed stacks, ``onprem`` is the fail-fast portability
#: placeholder.
RUNTIME_PROFILES = frozenset({"local", "gcp", "platform", "onprem"})

#: The profile string handed to every INTERNET-FACING relaxation when ``LOAN_DOC_PROFILE``
#: was never set. It is deliberately NOT a member of :data:`RUNTIME_PROFILES` and never reaches
#: :class:`Settings`: it exists so that "no choice was made" is a distinct input to the security
#: layers rather than being indistinguishable from a chosen ``local``.
UNCONSENTED_PROFILE = "unconfigured"


def _interpolate(value: Any) -> Any:
    """Replace ``${VAR}`` / ``${VAR:-default}`` tokens in strings recursively.

    This is a RESOLVER, so it resolves three states rather than two. ``os.environ.get(name,
    default)`` here would reintroduce the whole defect one layer down, where no scan of the
    adapter call sites would ever find it: the settings file is the layer that supplies the
    load-bearing defaults, so a collapse here hands an emptied variable the default that
    belongs to an unset one.

    * unset: nobody expressed an intent, so the declared ``:-default`` stands.
    * set and empty: an intent WAS expressed and it names nothing. Refused at settings load
      (:class:`ConfiguredEmptyError`), not silently defaulted. ``LOAN_DOC_PII_JURISDICTIONS=""``
      would otherwise render the ``${LOAN_DOC_PII_JURISDICTIONS:-SG,HK,JP,AU}`` slot as empty,
      leaving the redactor with no national-id pack at all and no signal that it happened, or
      else quietly inherit the shipped pack, which is the same absence read as a choice. A
      boot refusal makes neither reading possible.
    * set with a value: used as given.

    ``${VAR}`` with no declared default is the same three states with ``""`` as the default.
    """
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            return setting_or_default(m.group(1), m.group(2) or "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _validate_profile(profile: str) -> str:
    """Fail closed on a profile string nothing binds, INCLUDING a capitalisation typo.

    The comparison is exact and case-sensitive on purpose: every posture decision downstream
    matches the profile string exactly, so ``Local`` selects none of the relaxations but also
    none of the restrictions. Normalising the case here would turn a typo into a silent choice;
    refusing it turns the typo into a load failure.
    """
    if profile not in RUNTIME_PROFILES:
        expected = ", ".join(sorted(RUNTIME_PROFILES))
        raise ValueError(f"unknown {_PROFILE_ENV} {profile!r}; expected one of: {expected}")
    return profile


#: Profiles that mean "running on managed cloud infrastructure", for the banner's runtime half.
_MANAGED_PROFILES: frozenset[str] = frozenset({"gcp"})

#: The port whose ACTIVE binding decides what the provenance banner's model half says.
#: Named once here so rebinding it for a profile changes the banner in the same edit.
_GENERATOR_PORT: str = "llm"

#: Constant names a managed adapter may declare its model id under. Several spellings because
#: the fleet uses several, and a resolver that knew only one would report a bound model as
#: unnamed.
_MODEL_CONSTANTS: tuple[str, ...] = ("_MODEL", "_DEFAULT_MODEL")


def _declared_model(binding: str) -> str:
    """The model id the bound managed adapter declares, or an honest statement that it names none.

    Resolved from the BINDING rather than from a settings string, which is the point: a settings
    field would be a claim ABOUT the binding, and the two drift the first time somebody rebinds a
    profile without remembering the second field. Importing the adapter module here is safe with
    no cloud SDK installed -- every cloud import in these adapters lives inside the method that
    needs it, which is the portability property the parity suite already asserts.

    Returns ``managed-model-unnamed`` when the adapter pins no model id anywhere. That is not a
    placeholder for a nicer answer: it truthfully says a managed generator is bound and this
    repository does not name which model it calls, which is a fact a reviewer should be able to
    see rather than one a banner should paper over with an invented id.
    """
    from importlib import import_module

    module_path, _, class_name = binding.partition(":")
    try:
        module = import_module(module_path)
    except ImportError:  # pragma: no cover - the bound module is importable offline
        return "managed-model-unavailable"
    for holder in (module, getattr(module, class_name, None)):
        for name in _MODEL_CONSTANTS:
            value = getattr(holder, name, None)
            if value:
                return str(value)
    return "managed-model-unnamed"


@dataclass(frozen=True)
class ProfileChoice:
    """The ONE resolution of ``LOAN_DOC_PROFILE``, and what each consumer must key off.

    Every module that needs the profile reads it from :class:`Settings` (which resolves it
    once, here). No module may re-derive the profile with its own
    ``os.environ.get("LOAN_DOC_PROFILE", "local")``: that fallback reads an UNSET variable as
    consent, which is the fail-open this type exists to remove
    (``tests/unit/test_profile_single_source.py`` fails the build if one reappears).

    The two derived profile strings differ because the two decisions fail closed in OPPOSITE
    directions, so a single "effective profile" string would harden one and weaken the other.
    """

    #: Which adapter family to bind. Absent consent this is still ``local`` (the SDK-free
    #: adapters), because the alternative would import cloud SDKs that are not installed; the
    #: local IDENTITY adapter refuses to construct when :attr:`explicit` is False, so an
    #: unconsented run has data adapters but no end-user identity.
    profile: str = "local"
    #: Was the profile named DELIBERATELY (``LOAN_DOC_PROFILE`` set, or a profile written into
    #: ``config/settings.yaml``)?
    explicit: bool = True

    @property
    def exposure_profile(self) -> str:
        """The profile every *relaxation* keys off: CORS origins and the dev-persona header.

        These decisions grant something extra to ``local``, so an unconsented run must NOT
        look like ``local``: it gets :data:`UNCONSENTED_PROFILE`, which is no origin's
        allowlist and no seeded persona.
        """
        return self.profile if self.explicit else UNCONSENTED_PROFILE

    @property
    def bind_profile(self) -> str:
        """The profile the bind guard keys off, where ``local`` is the RESTRICTIVE case.

        ``resolve_bind_host`` confines ``local`` to loopback and lets fronted profiles take
        ``0.0.0.0``, so here an unconsented run must look like ``local`` and stay on loopback.
        """
        return self.profile if self.explicit else "local"


def _profile_setting(environ: Mapping[str, str] | None) -> EnvSetting:
    """The profile variable as the three-state reading, from the real or an injected environ."""
    if environ is None:
        return read_env_setting(_PROFILE_ENV)
    raw = environ.get(_PROFILE_ENV)
    return EnvSetting(name=_PROFILE_ENV, raw=raw, value="" if raw is None else raw.strip())


def resolve_profile(environ: Mapping[str, str] | None = None) -> ProfileChoice:
    """Read ``LOAN_DOC_PROFILE`` once, in THREE states: absent is NO CHOICE; emptied refuses.

    A hand-rolled ``env.get(name, "").strip()`` plus ``raw or "local"`` reads
    an emptied variable as an unset one, so an operator who deliberately blanks the profile
    gets the unconsented posture silently rather than a failure they can see.

    A value that IS present is validated here, not later, so an unknown or mis-capitalised
    profile is a load failure rather than an app that has already chosen its CORS and bind
    postures from a string nothing binds.
    """
    setting = _profile_setting(environ)
    if setting.is_configured_empty:
        raise ConfiguredEmptyError(
            f"{_PROFILE_ENV} is set to an empty value, which names no profile. Unset it for "
            "the unconsented loopback-only posture, or name a supported profile."
        )
    if setting.is_unset:
        return ProfileChoice(profile="local", explicit=False)
    return ProfileChoice(profile=_validate_profile(setting.value), explicit=True)


@dataclass(frozen=True)
class ModelSettings:
    #: The Vertex location the model client calls, NOT the compute region. Gemini 3
    #: serves the `us` and `eu` multi-regions only; `global` carries no residency
    #: guarantee. See models.location in config/settings.yaml.
    location: str = "us"
    reasoning: str = "gemini-3.5-flash"
    triage: str = "gemini-3.5-flash"
    hard_reasoning: str = "gemini-3.5-flash"  # Preview : feature-flagged off by default
    use_hard_reasoning: bool = False


@dataclass(frozen=True)
class DocumentAiSettings:
    location: str = "asia-southeast1"
    processor_id: str = ""  # projects/.../locations/.../processors/...
    processor_version: str = "rc"  # "rc" | "stable" | a pinned version id


@dataclass(frozen=True)
class ModelArmorSettings:
    template_id: str = "loan-doc-guardrail"
    host: str = "modelarmor.asia-southeast1.rep.googleapis.com"


@dataclass(frozen=True)
class DlpSettings:
    inspect_template: str = ""  # projects/.../inspectTemplates/...
    deidentify_template: str = ""  # projects/.../deidentifyTemplates/...


@dataclass(frozen=True)
class PiiSettings:
    """Which jurisdictions' national identifiers the redactor and the eval gate detect.

    Drives BOTH the local regex redactor and the GCP DLP custom info types from one pattern
    source, so a lending book outside APAC detects its own identifiers by editing this list
    rather than changing code. The supported packs live in ``domain/pii_patterns.py``, which
    also owns the default, so the pack and the config cannot disagree about what ships;
    override at runtime with ``LOAN_DOC_PII_JURISDICTIONS`` (comma-separated ISO-3166
    alpha-2 codes). Unknown codes degrade safely to the universal rows
    (email / phone / bank account) only.
    """

    jurisdictions: tuple[str, ...] = pii_patterns.DEFAULT_JURISDICTIONS


def _pii_settings(raw: Any) -> PiiSettings:
    """Build :class:`PiiSettings`, honouring the env override and normalising the codes.

    ``LOAN_DOC_PII_JURISDICTIONS`` (comma-separated) wins over the settings file so an
    operator can retarget the pack without editing YAML. Codes are upper-cased and coerced
    to a tuple: YAML yields a list, the env yields a string, and the frozen dataclass is
    compared by value, so the type must not depend on where the value came from.

    The override is read in THREE states (:func:`read_env_setting`), because naming no
    jurisdiction is the PERMISSIVE outcome here: :func:`~.domain.pii_patterns.patterns_for`
    keeps the universal email / phone / account rows but drops every national-ID row, so an
    empty list means applicant NRIC / HKID / My Number / TFN values stop being redacted in
    both the local redactor and the DLP custom info types. The two-state ``if env:`` this
    replaced sent set-and-empty down the same branch as unset, and ``_interpolate`` then
    wrote the emptied value into the settings file's
    ``${LOAN_DOC_PII_JURISDICTIONS:-SG,HK,JP,AU}`` slot, so emptying the variable silently
    disabled every national-ID pattern with no signal at all. Both layers are now closed:
    :func:`_interpolate` refuses an emptied variable rather than resolving its ``:-`` slot at
    all, and the read below refuses outright rather than substituting a value the operator did
    not choose. Whichever layer sees it first, the answer is the same refusal.

    * unset: no intent was expressed, so the settings-file value (or the shipped default)
      stands.
    * set and empty: an intent WAS expressed and it names no jurisdiction. Refused at load,
      not silently honoured as "redact less". A value that parses to no code (``","``) is the
      same state and lands in the same place.
    * set and valid: the comma-separated codes, upper-cased.
    """
    data = dict(raw or {})
    setting = read_env_setting("LOAN_DOC_PII_JURISDICTIONS")
    if not setting.is_unset:
        codes_from_env = [c.strip() for c in setting.value.split(",") if c.strip()]
        if not codes_from_env:
            raise ConfiguredEmptyError(
                "LOAN_DOC_PII_JURISDICTIONS is set but names no jurisdiction. That would drop "
                "every national-ID redaction pattern while looking configured. Unset it to "
                "keep the settings-file default, or name the ISO-3166 alpha-2 codes whose "
                "identifiers must be redacted."
            )
        data["jurisdictions"] = codes_from_env
    codes = data.get("jurisdictions")
    if codes is not None:
        if isinstance(codes, str):
            codes = codes.split(",")
        data["jurisdictions"] = tuple(str(c).strip().upper() for c in codes if str(c).strip())
    return PiiSettings(**data)


@dataclass(frozen=True)
class LoggingSettings:
    log_name: str = "loan-document-intelligence-audit"
    bucket: str = "loan-document-intelligence-worm"
    retention_days: int = 2557  # ~7 years


@dataclass(frozen=True)
class AgentEngineSettings:
    resource_name: str = ""  # reasoningEngine resource id, set after deploy
    display_name: str = "loan-document-intelligence"


@dataclass(frozen=True)
class ValidationSettings:
    amount_tolerance: float = 0.05  # relative tolerance for monetary equality checks
    balance_decline_warn_ratio: float = 0.15
    balance_decline_fail_ratio: float = 0.40
    affordability_warn_ratio: float = 0.55
    affordability_fail_ratio: float = 0.70

    def __post_init__(self) -> None:
        values = (
            self.amount_tolerance,
            self.balance_decline_warn_ratio,
            self.balance_decline_fail_ratio,
            self.affordability_warn_ratio,
            self.affordability_fail_ratio,
        )
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("validation policy values must be ratios between 0 and 1")
        if self.balance_decline_warn_ratio >= self.balance_decline_fail_ratio:
            raise ValueError("balance decline warn ratio must be below fail ratio")
        if self.affordability_warn_ratio >= self.affordability_fail_ratio:
            raise ValueError("affordability warn ratio must be below fail ratio")


@dataclass(frozen=True)
class LocalSettings:
    """Paths for the SDK-free ``local`` profile stores (append-only audit, SQLite).

    Empty strings select the per-package default under ``~/.loan_doc_intel/``; tests pass
    ``:memory:`` for an ephemeral, deterministic store. No Google Cloud here.
    """

    audit_path: str = ""  # append-only audit store; "" => ~/.loan_doc_intel/audit.db


#: Multi-regions Document AI may use as a STATED residency deviation from the deploy region.
#: Each names one jurisdiction and carries an ML-processing commitment for it. `global` is
#: deliberately absent: it names no jurisdiction at all.
_DOCUMENT_AI_MULTI_REGIONS = frozenset({"us", "eu"})


@dataclass(frozen=True)
class Settings:
    project_id: str = "your-gcp-project"
    region: str = "asia-southeast1"
    profile: str = "local"  # local (SDK-free default) | gcp | platform | onprem
    kms_key: str = ""  # projects/.../cryptoKeys/... (regional)
    models: ModelSettings = field(default_factory=ModelSettings)
    document_ai: DocumentAiSettings = field(default_factory=DocumentAiSettings)
    model_armor: ModelArmorSettings = field(default_factory=ModelArmorSettings)
    dlp: DlpSettings = field(default_factory=DlpSettings)
    pii: PiiSettings = field(default_factory=PiiSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    agent_engine: AgentEngineSettings = field(default_factory=AgentEngineSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    local: LocalSettings = field(default_factory=LocalSettings)
    # port_name -> { profile -> "module.path:ClassName" }
    adapters: dict[str, dict[str, str]] = field(default_factory=dict)
    # Was the profile chosen DELIBERATELY, or merely inherited from the fallback? ``load``
    # sets this False when neither LOAN_DOC_PROFILE nor the settings file names a profile.
    # Direct construction is deliberate by definition (a caller named the profile in code),
    # so the default is True. The seeded-persona identity adapter refuses to serve when this
    # is False: a retail-lending underwriting service must never hand out a loan-approver
    # persona because an env var went missing.
    profile_explicit: bool = True

    def __post_init__(self) -> None:
        if self.region != "asia-southeast1":
            raise ValueError("region must remain in the approved allowlist: asia-southeast1")
        # Document AI may sit in the deploy region, or in a NAMED MULTI-REGION as a stated
        # deviation, and in nothing else. Singapore is "limited support" for Document AI and
        # access is gated behind Google's Single Region Request Form, so until that is granted
        # the bytes are extracted in the `us` multi-region while the rest of the stack stays in
        # region. That is a disclosed residency deviation, not a widening: a multi-region names
        # one jurisdiction and carries an ML-processing commitment for it.
        #
        # `global` is refused by name because it names NO jurisdiction, and it is precisely what
        # someone reaches for to make an apply succeed. A different single region is refused too:
        # it is neither the deploy region nor a multi-region commitment.
        if self.document_ai.location not in {self.region, *_DOCUMENT_AI_MULTI_REGIONS}:
            raise ValueError(
                f"Document AI location {self.document_ai.location!r} must be the deploy region "
                f"({self.region}) or a named multi-region "
                f"({', '.join(sorted(_DOCUMENT_AI_MULTI_REGIONS))}). `global` names no "
                "jurisdiction and is never acceptable here."
            )

    @property
    def profile_choice(self) -> ProfileChoice:
        """The resolved profile as the two-directional posture input the security layers use.

        Read ``exposure_profile`` for anything that GRANTS (CORS origins, dev personas) and
        ``bind_profile`` for anything that RESTRICTS (the loopback bind guard). Never compare
        ``profile`` directly for a posture decision: it cannot tell a chosen ``local`` from an
        inherited one.
        """
        return ProfileChoice(profile=self.profile, explicit=self.profile_explicit)

    @staticmethod
    def load(path: str | os.PathLike[str] | None = None) -> Settings:
        path = Path(path or setting_or_default("LOAN_DOC_SETTINGS", "config/settings.yaml"))
        raw = _interpolate(yaml.safe_load(path.read_text())) if path.exists() else {}
        raw = raw or {}
        nested: dict[str, Any] = {
            "models": ModelSettings(**(raw.pop("models", {}) or {})),
            "document_ai": DocumentAiSettings(**(raw.pop("document_ai", {}) or {})),
            "model_armor": ModelArmorSettings(**(raw.pop("model_armor", {}) or {})),
            "dlp": DlpSettings(**(raw.pop("dlp", {}) or {})),
            # Always built (not only when the YAML carries a `pii:` block) so the
            # LOAN_DOC_PII_JURISDICTIONS override applies to a settings file without one.
            "pii": _pii_settings(raw.pop("pii", {})),
            "logging": LoggingSettings(**(raw.pop("logging", {}) or {})),
            "agent_engine": AgentEngineSettings(**(raw.pop("agent_engine", {}) or {})),
            "validation": ValidationSettings(**(raw.pop("validation", {}) or {})),
            "local": LocalSettings(**(raw.pop("local", {}) or {})),
        }
        # Three states, not two. The environment wins over the settings file (unchanged
        # precedence); a profile written into the file is still a deliberate choice; and only
        # when NEITHER names one is the ``local`` binding inherited rather than consented to.
        # The old ``os.environ.get(_PROFILE_ENV, raw.pop("profile", "local"))`` collapsed the
        # third state into the first, so a missing env var served the no-auth persona stack.
        choice = resolve_profile()
        file_profile = str(raw.pop("profile", "") or "").strip()
        if choice.explicit:
            profile, explicit = choice.profile, True
        elif file_profile:
            profile, explicit = _validate_profile(file_profile), True
        else:
            profile, explicit = choice.profile, False
        known = {f for f in Settings.__dataclass_fields__ if f not in nested}
        flat: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        flat.pop("profile_explicit", None)
        return Settings(profile=profile, profile_explicit=explicit, **flat, **nested)

    @property
    def runtime(self) -> str:
        """WHERE this process runs, as the UI banner states it: ``gcp`` or ``local``.

        Derived from the profile, never sniffed from the environment. A console that read its
        runtime from ``window.location`` would be right until the day the deployment served
        through a proxy and wrong silently after that, so the service is the party asked.

        ``onprem`` reads ``local`` because that is its entire point, and a managed model call
        does not make a process cloud-hosted: this states where the PROCESS runs, and
        :attr:`generator_model` states whose model answers.
        """
        return "gcp" if self.profile in _MANAGED_PROFILES else "local"

    @property
    def generator_model(self) -> str:
        """WHICH model answers, as the UI banner states it (org decision, 2026-08-30).

        These systems are demonstrated on a laptop and on a deployment, sometimes in the same
        hour, and a screenshot of one is indistinguishable from the other. A viewer who cannot
        tell which they are looking at cannot tell whether a figure came from a managed model or
        a deterministic offline stub, which is exactly the confusion an audit-first pitch cannot
        afford. So the page states it, always, rather than the presenter stating it sometimes.

        ``no-model`` is deliberately NOT ``deterministic-offline-stub``. The stub string claims a
        model-shaped port bound to a stub; ``no-model`` says there is no such port at all, and a
        reviewer approving an escalation is entitled to know which of the two they are reading.
        """
        if not _GENERATOR_PORT:
            return "no-model"
        table = self.adapters.get(_GENERATOR_PORT) or {}
        binding = str(table.get(self.profile, "") or "")
        if not binding:
            return "no-model"
        if self.profile not in _MANAGED_PROFILES:
            # The on-prem adapters are fail-fast migration placeholders: they raise rather than
            # generating, so naming a model would advertise one that never answers.
            if self.profile == "onprem":
                return "onprem-not-implemented"
            return "deterministic-offline-stub"
        return _declared_model(binding)


def instantiate(dotted: str, settings: Settings) -> Any:
    """Import ``module.path:ClassName`` and construct it with ``settings``."""
    module_path, _, class_name = dotted.partition(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(settings)


class Container:
    """Lazily-built registry of port -> adapter instances.

    Adapters are imported only on first access so that, e.g., a unit test using the
    on-prem profile never needs the Google Cloud SDKs installed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _bind(self, port_name: str) -> Any:
        binding = self.settings.adapters.get(port_name, {})
        dotted = binding.get(self.settings.profile)
        if not dotted:
            raise KeyError(
                f"No adapter configured for port '{port_name}' "
                f"under profile '{self.settings.profile}'."
            )
        return instantiate(dotted, self.settings)

    # One cached_property per port keeps wiring declarative and type-greppable.
    @cached_property
    def extraction(self) -> Any:
        return self._bind("extraction")

    @cached_property
    def llm(self) -> Any:
        return self._bind("llm")

    @cached_property
    def guardrail(self) -> Any:
        return self._bind("guardrail")

    @cached_property
    def redaction(self) -> Any:
        return self._bind("redaction")

    @cached_property
    def agent_runtime(self) -> Any:
        return self._bind("agent_runtime")

    @cached_property
    def session(self) -> Any:
        return self._bind("session")

    @cached_property
    def memory(self) -> Any:
        return self._bind("memory")

    @cached_property
    def audit(self) -> Any:
        return self._bind("audit")

    @cached_property
    def tracer(self) -> Any:
        return self._bind("tracer")

    @cached_property
    def evaluation(self) -> Any:
        return self._bind("evaluation")

    @cached_property
    def registry(self) -> Any:
        return self._bind("registry")

    @cached_property
    def tool_catalog(self) -> Any:
        return self._bind("tool_catalog")

    @cached_property
    def identity(self) -> Any:
        return self._bind("identity")

    @cached_property
    def entitlements(self) -> Any:
        return self._bind("entitlements")

    @cached_property
    def review_router(self) -> Any:
        return self._bind("review_router")


def build_container(settings: Settings | None = None) -> Container:
    return Container(settings or Settings.load())


def identity_adapter_class(settings: Settings) -> type:
    """The identity adapter CLASS the active binding names, resolved WITHOUT constructing it.

    Reads the same ``adapters:`` table :meth:`Container._bind` binds from, so a deployment is
    answered about the adapter it ACTUALLY runs rather than the one the profile name suggests.
    A deployment that rebound identity in ``config/settings.yaml`` (the documented
    on-premises path: swap the placeholder for the client's own IdP adapter) is answered
    about that.

    Constructing is deliberately avoided: the seeded-persona adapter REFUSES to construct
    under an inherited profile, so a posture computed from an instance would be unobtainable
    in one of the exact cases it has to describe.
    """
    binding = settings.adapters.get("identity", {})
    dotted = binding.get(settings.profile)
    if not dotted:
        raise KeyError(f"No identity adapter configured under profile '{settings.profile}'.")
    module_path, _, class_name = dotted.partition(":")
    resolved = getattr(importlib.import_module(module_path), class_name)
    if not isinstance(resolved, type):
        raise TypeError(f"identity binding {dotted!r} does not name a class")
    return resolved


def end_user_auth_kind(settings: Settings | None = None) -> str:
    """What the BOUND identity adapter declares it does for end-user authentication.

    This is the one question "are this service's end-user routes authenticated?" reduces to.
    See ``ports/identity.py``: neither the profile string nor the presence of a
    service-to-service secret can answer it.

    Any failure to establish the answer resolves to ``CLIENT_ASSERTED``. A guard that switches
    OFF because a lookup raised is a guard that fails open, and nothing is lost by failing
    closed here: the same failure surfaces loudly at the first request, when the container
    resolves the identical binding for real.
    """
    from .ports.identity import CLIENT_ASSERTED, declared_end_user_auth

    try:
        return declared_end_user_auth(identity_adapter_class(settings or Settings.load()))
    except Exception:
        return CLIENT_ASSERTED
