"""The profile has ONE source of truth, and it fails closed on an unset variable.

Mirrors human-review-console (``human-review-console/tests/test_profile_single_source.py``) as the
standing gate for the absence-read-as-consent class. Any module that reads
``LOAN_DOC_PROFILE`` directly can reintroduce the whole class with its own permissive
fallback, so only ``config.resolve_profile`` may read it.

The rest of the file is this repo's own fail-closed proof: an unconsented run gets the
SDK-free adapters but no seeded persona, no localhost CORS grant, and still binds loopback.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from loan_doc_intel.adapters.local.identity import (
    LocalPersonaIdentityAdapter,
    LocalPersonaProfileError,
)
from loan_doc_intel.config import (
    RUNTIME_PROFILES,
    UNCONSENTED_PROFILE,
    ConfiguredEmptyError,
    Settings,
    resolve_profile,
)

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "loan_doc_intel"
_CONFIG = _SRC / "config.py"
_SETTINGS_YAML = _ROOT / "config" / "settings.yaml"


def _python_sources() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if p != _CONFIG)


# --------------------------------------------------------------------------- #
# The drift guard (the standing gate).
# --------------------------------------------------------------------------- #
def test_only_the_resolver_reads_the_profile_variable_from_the_environment() -> None:
    offenders = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"(os\.environ|os\.getenv)[^\n]*PROFILE", line):
                offenders.append(f"{path.relative_to(_SRC)}:{number}: {line.strip()}")
    assert not offenders, (
        "these modules re-derive the profile instead of calling config.resolve_profile, "
        "so an unset LOAN_DOC_PROFILE can again be read as consent:\n" + "\n".join(offenders)
    )


def test_the_settings_file_supplies_no_literal_profile_fallback() -> None:
    """``${LOAN_DOC_PROFILE:-local}`` in YAML is the same fail-open, one layer down.

    The file is interpolated before the resolver sees it, so a literal default there would
    manufacture consent for an unset variable and the three-state resolution could never
    observe the third state.
    """
    line = next(
        raw
        for raw in _SETTINGS_YAML.read_text(encoding="utf-8").splitlines()
        if raw.startswith("profile:")
    )
    assert "${LOAN_DOC_PROFILE:-}" in line, line


# --------------------------------------------------------------------------- #
# Three states: unset, set-and-blank, set-and-valid.
# --------------------------------------------------------------------------- #
def test_the_resolver_treats_an_absent_variable_as_no_choice() -> None:
    """UNSET is still no choice: nobody expressed an intent, so nothing was consented to."""
    assert resolve_profile({}).explicit is False


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_the_resolver_refuses_a_variable_that_was_deliberately_emptied(blank: str) -> None:
    """SET-AND-EMPTY is a THIRD state, not the first.

    Looping over ``{}``, ``{"LOAN_DOC_PROFILE": ""}`` and ``{"LOAN_DOC_PROFILE": "   "}``
    together and asserting they are all "no choice" is the defect stated as a claim, and
    stating it is how a suite stops being able to see it: an operator who blanks the profile
    HAS expressed an intent, and it names no profile. It must fail loudly rather than inherit
    the unconsented posture indistinguishably from a variable nobody ever set.
    """
    with pytest.raises(ConfiguredEmptyError, match="LOAN_DOC_PROFILE"):
        resolve_profile({"LOAN_DOC_PROFILE": blank})


def test_an_unconsented_run_is_not_the_local_profile_for_any_relaxation() -> None:
    choice = resolve_profile({})
    assert choice.exposure_profile == UNCONSENTED_PROFILE
    assert choice.exposure_profile != "local"
    assert UNCONSENTED_PROFILE not in RUNTIME_PROFILES


def test_an_unconsented_run_still_binds_loopback() -> None:
    """The bind guard fails closed in the opposite direction: local is the restrictive case."""
    assert resolve_profile({}).bind_profile == "local"


def test_a_deliberate_profile_is_carried_through_unchanged() -> None:
    choice = resolve_profile({"LOAN_DOC_PROFILE": "gcp"})
    assert (choice.profile, choice.explicit) == ("gcp", True)
    assert choice.exposure_profile == "gcp"
    assert choice.bind_profile == "gcp"


@pytest.mark.parametrize("value", ["bogus", "Local", "GCP", "LOCAL"])
def test_an_unknown_or_miscapitalised_profile_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="LOAN_DOC_PROFILE"):
        resolve_profile({"LOAN_DOC_PROFILE": value})


# --------------------------------------------------------------------------- #
# What the third state actually costs a running process.
# --------------------------------------------------------------------------- #
def test_load_reports_an_inherited_profile_as_not_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAN_DOC_PROFILE", raising=False)
    settings = Settings.load(_SETTINGS_YAML)
    assert settings.profile == "local"  # the SDK-free adapters still bind
    assert settings.profile_explicit is False
    assert settings.profile_choice.exposure_profile == UNCONSENTED_PROFILE


def test_load_reports_a_named_profile_as_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAN_DOC_PROFILE", "local")
    settings = Settings.load(_SETTINGS_YAML)
    assert (settings.profile, settings.profile_explicit) == ("local", True)
    assert settings.profile_choice.exposure_profile == "local"


def test_seeded_personas_are_refused_under_an_inherited_profile() -> None:
    """The no-auth loan-approver persona needs consent, not a missing env var."""
    inherited = Settings(profile="local", profile_explicit=False)
    with pytest.raises(LocalPersonaProfileError, match="LOAN_DOC_PROFILE"):
        LocalPersonaIdentityAdapter(inherited)


def test_seeded_personas_are_refused_outside_the_local_profile() -> None:
    with pytest.raises(LocalPersonaProfileError, match="gcp"):
        LocalPersonaIdentityAdapter(Settings(profile="gcp"))


def test_seeded_personas_serve_a_deliberate_local_profile() -> None:
    adapter = LocalPersonaIdentityAdapter(Settings(profile="local"))
    assert adapter.personas()
