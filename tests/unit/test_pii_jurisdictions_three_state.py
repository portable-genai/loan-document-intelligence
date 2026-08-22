"""``LOAN_DOC_PII_JURISDICTIONS`` resolved in THREE states, never two.

The jurisdiction list is an allowlist whose EMPTY value is the permissive one:
``patterns_for`` keeps the universal email / phone / bank-account rows but contributes no
national-ID row for a jurisdiction that is not listed, so an empty list means an applicant's
NRIC, HKID, My Number and TFN stop being redacted, in the local redactor and in the DLP
custom info types alike.

Red before the fix, proven by execution against this repo's real ``config/settings.yaml``:

* ``LOAN_DOC_PII_JURISDICTIONS=""`` resolved to ``()``. ``_interpolate`` rendered the
  ``${LOAN_DOC_PII_JURISDICTIONS:-SG,HK,JP,AU}`` slot as empty (its ``:-`` handling took the
  default only when the variable was ABSENT, unlike the shell syntax it borrows), and the
  two-state ``if env:`` in ``_pii_settings`` then sent the same emptied value down the unset
  branch. Every national-ID pattern disappeared, silently, from a deployment that looked
  configured.
* ``LOAN_DOC_PII_JURISDICTIONS=","`` also resolved to ``()``: a second spelling of "names
  nothing" reaching the same fail-open by a different route.

Green after: unset keeps the shipped pack, anything that names no jurisdiction is REFUSED at
settings load, and a named list is used.
"""

from __future__ import annotations

import pytest
from hex_service_kit import ConfiguredEmptyError

from loan_doc_intel.config import Settings, _interpolate, _pii_settings

_ENV = "LOAN_DOC_PII_JURISDICTIONS"


def test_unset_keeps_the_shipped_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    """No intent was expressed, so the settings-file default stands."""
    monkeypatch.delenv(_ENV, raising=False)
    assert Settings.load().pii.jurisdictions == ("SG", "HK", "JP", "AU")


@pytest.mark.parametrize("names_nothing", ["", "   ", "\t", ",", " , , "])
def test_a_value_naming_no_jurisdiction_is_refused(
    monkeypatch: pytest.MonkeyPatch, names_nothing: str
) -> None:
    """The load-bearing assertion: no spelling of "nothing" may silently disable the pack.

    Red before: every one of these resolved to ``()``, dropping the national-ID rows with no
    signal. Green after: settings load refuses, so the service cannot come up believing it is
    redacting when it is not.
    """
    monkeypatch.setenv(_ENV, names_nothing)
    with pytest.raises(ConfiguredEmptyError) as excinfo:
        Settings.load()
    assert _ENV in str(excinfo.value)


def test_a_named_list_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "gb, in ")
    assert Settings.load().pii.jurisdictions == ("GB", "IN")
    assert _pii_settings({}).jurisdictions == ("GB", "IN")


def test_the_settings_file_default_stands_for_a_variable_nobody_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interpolation layer under the refusal above, in its FIRST state.

    ``${VAR:-default}`` is a resolver, so it resolves three states. Unset is the one where
    the declared default legitimately stands: nobody expressed an intent, so there is nothing
    to override.
    """
    monkeypatch.delenv("LOAN_DOC_TEST_VAR", raising=False)
    assert _interpolate("${LOAN_DOC_TEST_VAR:-fallback}") == "fallback"
    # No default declared: unset resolves to the empty string, there being nothing to inherit.
    assert _interpolate("${LOAN_DOC_TEST_VAR}") == ""

    monkeypatch.setenv("LOAN_DOC_TEST_VAR", "real")
    assert _interpolate("${LOAN_DOC_TEST_VAR:-fallback}") == "real"


@pytest.mark.parametrize("slot", ["${LOAN_DOC_TEST_VAR:-fallback}", "${LOAN_DOC_TEST_VAR}"])
def test_the_interpolator_refuses_a_variable_that_was_deliberately_emptied(
    monkeypatch: pytest.MonkeyPatch, slot: str
) -> None:
    """The SPLIT half of the assertion this file makes about ``:-``.

    Asserting that an emptied variable takes the declared default, on the grounds that ``:-``
    borrows the shell's semantics, is the two-state collapse restated one layer below the
    adapters, where no scan of the call sites would find it: the settings file is precisely
    where the load-bearing defaults live, so silently handing an emptied variable the default
    that belongs to an unset one is the whole defect, in the one place it costs the most.
    Absent takes the default (above); emptied refuses.

    A set-but-empty variable is the ordinary result of ``export VAR=$SOMETHING_UNSET`` or an
    empty CI secret, not an exotic input, which is why the refusal has to be loud.
    """
    monkeypatch.setenv("LOAN_DOC_TEST_VAR", "")
    with pytest.raises(ConfiguredEmptyError, match="LOAN_DOC_TEST_VAR"):
        _interpolate(slot)


def test_an_unlisted_jurisdiction_still_degrades_safely() -> None:
    """Refusing an EMPTY list does not change how an unknown CODE behaves.

    A code with no pack contributes no national-ID row and raises nothing; only naming no
    jurisdiction at all is a refusal.
    """
    assert _pii_settings({"jurisdictions": ["zz"]}).jurisdictions == ("ZZ",)
