"""PII redaction adapter tests (the redact-before-everything boundary, R1, P-04).

Prove the jurisdiction-driven local redactor masks B5's APAC national identifiers (SG NRIC,
HK HKID, JP My Number, AU TFN) plus the universal email / phone / bank-account rows; that
the checksum-gated rows report only genuine identifiers; and that an unknown jurisdiction
degrades safely to the universal rows rather than raising. Same pattern source as the eval
gate, so what these tests mask is exactly what the gate detects.

The load-bearing test for THIS vertical is
``test_the_account_row_wins_over_the_tfn_row``, because it pins the one thing B5 does
differently from the trade-finance vertical this pack mirrors. That vertical's
account row is a bare-digit catch-all which subsumes the national-id shapes, so it must run
LAST. B5's is the specific hyphenated 3-6-1 shape a bank statement prints, and the AU TFN
row bites INTO it: the leading nine digits of ``123-456782-0`` pass the TFN checksum. Get
the order wrong and an applicant's account number is reported to a reviewer as a tax file
number. Both directions are asserted below.

The second thing pinned here is the consequence of having NO bare-digit catch-all: the
checksums genuinely decide whether an applicant's ordinary figures survive
(``test_ordinary_figures_survive_because_there_is_no_catch_all``), rather than merely
deciding a label as they do in the trade-finance pack. The residual over-matches that
remain are asserted rather than left to be rediscovered.
"""

from __future__ import annotations

from loan_doc_intel.adapters.local.redaction import LocalRegexRedactionAdapter
from loan_doc_intel.config import PiiSettings, Settings

# FICTIONAL identifiers. The JP My Number and AU TFN carry VALID check digits; the paired
# "_INVALID" values share the shape but fail the checksum.
_SG_NRIC = "S1234567A"
_HK_HKID = "A123456(3)"
_JP_MYNUMBER_VALID = "123456789018"
_JP_MYNUMBER_INVALID = "123456789012"
_AU_TFN_VALID = "123 456 782"
_AU_TFN_INVALID = "123 456 781"
_EMAIL = "ops@example.com"
_PHONE = "+81 90 1234 5678"
# The applicant's salary-crediting account. Its leading nine digits pass the TFN checksum,
# which is exactly why the row order below is load-bearing.
_ACCOUNT_TFN_SHAPED = "123-456782-0"


def _redactor(*jurisdictions: str) -> LocalRegexRedactionAdapter:
    return LocalRegexRedactionAdapter(Settings(pii=PiiSettings(jurisdictions=jurisdictions)))


def test_default_jurisdictions_are_the_apac_lending_markets() -> None:
    # The pack B5 ships with; the eval gate's golden cases mirror exactly these.
    assert Settings().pii.jurisdictions == ("SG", "HK", "JP", "AU")


def test_sg_nric_and_email_and_phone_masked() -> None:
    r = _redactor("SG", "HK", "JP", "AU")
    out = r.redact(f"NRIC {_SG_NRIC}, email {_EMAIL}, phone {_PHONE}")
    assert _SG_NRIC not in out.text
    assert _EMAIL not in out.text
    assert _PHONE not in out.text
    info = {f.info_type for f in out.findings}
    assert {"SG_NRIC_FIN", "EMAIL_ADDRESS", "PHONE_NUMBER"} <= info


def test_sg_nric_masked_case_insensitively() -> None:
    """A lower-cased NRIC is an NRIC; an upper-only row leaks it silently.

    The row B5 shipped before the pack (``\\b[STFGM]\\d{7}[A-Z]\\b``) was upper-case-only, so
    an NRIC typed into a free-text field in lower case was neither masked nor detected.
    """
    r = _redactor("SG")
    out = r.redact("nric s1234567a on file.")
    assert "s1234567a" not in out.text
    assert "SG_NRIC_FIN" in {f.info_type for f in out.findings}


def test_hk_hkid_masked_in_both_written_forms() -> None:
    """The parenthesised form on shape, the bare keyed form on its checksum.

    The bare form is the one a database or a keyed field carries, and an upstream pack that
    required the parens masked neither it nor (since the eval reads the same rows) detected
    it. It cannot simply be added on shape though: it collides exactly with a loan document
    reference, which is what the checksum separates.
    """
    r = _redactor("HK")
    for form in (_HK_HKID, "A1234563"):
        out = r.redact(f"HKID {form} on file.")
        assert form not in out.text, form
        assert "HK_HKID" in {f.info_type for f in out.findings}, form


def test_payslip_reference_is_not_an_hkid() -> None:
    """`PS1234567` has the bare HKID shape; only the checksum keeps it out of the mask."""
    r = _redactor("HK")
    out = r.redact("Payslip PS1234567 and statement BS7654321 attached.")
    assert "PS1234567" in out.text
    assert "HK_HKID" not in {f.info_type for f in out.findings}


def test_jp_my_number_grouped_form_needs_the_jp_row() -> None:
    """The 4-4-4 form a My Number card is printed in.

    The regression behind porting the ``onprem-dlp`` regex verbatim instead of the
    ``\\b\\d{12}\\b`` the sibling packs narrowed it to: that row cannot see a spaced My
    Number, so it is masked by nothing and (since the eval leak check reads these same rows)
    detected by nothing either.
    """
    r = _redactor("SG", "HK", "JP", "AU")
    for grouped in ("1234 5678 9018", "1234-5678-9018"):
        out = r.redact(f"My Number {grouped} on file.")
        assert grouped not in out.text, grouped
        assert "[JP_MY_NUMBER]" in out.text, grouped
        assert "JP_MY_NUMBER" in {f.info_type for f in out.findings}, grouped


def test_jp_row_ignores_a_twelve_digit_prefix_of_a_longer_run() -> None:
    """A 16-digit card PAN is not a My Number, even when its first 12 digits check out.

    The lookarounds carry this, and it is why the row is not simply ``\\d{4}[- ]?...``.
    Unlike the trade-finance vertical there is no bare-digit catch-all to mask the PAN
    afterwards, so the row's precision is the whole answer here: the managed profile's
    built-in ``CREDIT_CARD_NUMBER`` info type is what covers a real card.
    """
    r = _redactor("JP")
    out = r.redact("Card 1234567890181234 on file.")
    assert not out.findings


def test_au_tfn_spaced_form_and_its_checksum() -> None:
    """The AU row is checksum-gated, and the checksum decides whether anything is masked.

    In the trade-finance pack a failing 9-digit run is masked anyway (as an account), so the
    checksum only picks the label. Here nothing else claims a bare digit run, so a run that
    fails the check survives untouched.
    """
    r = _redactor("AU")
    valid = r.redact(f"TFN {_AU_TFN_VALID} recorded.")
    assert _AU_TFN_VALID not in valid.text
    assert "AU_TFN" in {f.info_type for f in valid.findings}

    invalid = r.redact(f"Invoice {_AU_TFN_INVALID} settled.")
    assert _AU_TFN_INVALID in invalid.text
    assert not invalid.findings


def test_tfn_separated_by_a_non_breaking_space_is_still_masked() -> None:
    """The regex admits any whitespace, so the validator must strip any whitespace.

    A seam between the two is a silent leak, and the realistic one: PDF text extraction
    emits U+00A0 for spaces, and the redactor runs over that parser output
    (``_redact_extract``). A TFN the regex matched but the validator could not normalise
    would be neither masked nor detected.
    """
    r = _redactor("AU")
    for sep in (" ", "\t", " "):
        out = r.redact(f"TFN 123{sep}456{sep}782 recorded.")
        assert "782" not in out.text.split("recorded")[0], repr(sep)
        assert "AU_TFN" in {f.info_type for f in out.findings}, repr(sep)


def test_the_account_row_wins_over_the_tfn_row() -> None:
    """Row order is load-bearing, and B5's order is the REVERSE of the trade-finance pack.

    B5's account row is the specific hyphenated 3-6-1 shape a bank statement prints, not a
    bare-digit catch-all, so it does not subsume the national ids and does not need to run
    last. It must run FIRST, because the AU TFN row matches the leading nine digits of the
    account shape and some of those pass the TFN checksum by arithmetic, not by coincidence
    of this fixture: ``123-000007-0`` does it too, so round account numbers make this
    routine rather than a corner case.

    With the national ids first, ``123-456782-0`` is masked as ``[AU_TFN]-0``: an
    applicant's account is reported to a reviewer under the wrong info type, the account row
    never fires, and the trailing check digit survives.
    """
    r = _redactor("SG", "HK", "JP", "AU")
    for account in (_ACCOUNT_TFN_SHAPED, "123-000007-0", "123-456789-0"):
        out = r.redact(f"Salary credited to account {account} monthly.")
        assert account not in out.text, account
        assert "[BANK_ACCOUNT_NUMBER]" in out.text, account
        info = {f.info_type for f in out.findings}
        assert info == {"BANK_ACCOUNT_NUMBER"}, f"{account}: {info}"


def test_an_account_and_a_tfn_together_are_each_labelled_correctly() -> None:
    """The account row running first must not eat a genuine TFN elsewhere in the text."""
    r = _redactor("AU")
    out = r.redact(f"TFN {_AU_TFN_VALID}; salary to account {_ACCOUNT_TFN_SHAPED}.")
    assert _AU_TFN_VALID not in out.text
    assert _ACCOUNT_TFN_SHAPED not in out.text
    assert {f.info_type for f in out.findings} == {"BANK_ACCOUNT_NUMBER", "AU_TFN"}


def test_ordinary_figures_survive_because_there_is_no_catch_all() -> None:
    """B5's checksums buy what the trade-finance pack's cannot: figures that are not masked.

    That pack masks every bare 9-17 digit run as an account, so its checksums only decide a
    label. B5 has no such row, so an applicant's ordinary figures (a property price, a
    payslip net pay, a dated balance) reach the model intact and only genuine identifiers
    are masked. This is the ``credit-memo-drafting`` reasoning, and it is why the rows
    stay checksum-gated even though B5's own account shape is specific.
    """
    r = _redactor("SG", "HK", "JP", "AU")
    figures = "Property price 850000, net pay 6500.00, balance 12000.0, ratio 0.55."
    out = r.redact(figures)
    assert out.text == figures
    assert not out.findings

    # A 12-digit reference that fails the My Number checksum is not an identifier, and
    # (unlike the trade-finance pack) nothing else claims it.
    ref = r.redact(f"Remittance reference {_JP_MYNUMBER_INVALID} quoted.")
    assert _JP_MYNUMBER_INVALID in ref.text
    assert not ref.findings


def test_the_known_over_matches_are_pinned_not_silent() -> None:
    """Two residuals the pack cannot remove without context words. Stated, not discovered.

    ``agent/callbacks.py`` redacts the prose the model is about to read, so a figure there
    with an identifier's shape is masked. Neither residual can move a verdict:
    ``_redact_extract`` redacts only the free-text identity fields, never the numeric fields
    the CrossValidator reads. The honest fix is ``require_context`` hotwords on the SHARED
    pack, which belongs in one cross-repo change (see ``domain/pii_patterns.py``).
    """
    r = _redactor("SG", "HK", "JP", "AU")
    # An 8-digit price starting 6/8/9 has the SG local phone shape.
    price = r.redact("Property price 85000000 at settlement.")
    assert "[SG_PHONE]" in price.text

    # A round 9-digit figure passes the TFN checksum by arithmetic coincidence, so it is
    # masked under a label a reader would not expect. The surprising label is the point:
    # a reviewer sees it rather than a comfortable one.
    facility = r.redact("Facility drawn to 250000000 at year end.")
    assert "[AU_TFN]" in facility.text
    assert {f.info_type for f in facility.findings} == {"AU_TFN"}


def test_iso_dates_are_never_masked() -> None:
    """Dates must survive: the balance-trend and period reasoning is dated."""
    r = _redactor("SG", "HK", "JP", "AU")
    dated = "Opening 2026-04-01, salary credit 2026-04-25, closing 2026-04-30."
    out = r.redact(dated)
    assert out.text == dated
    assert not out.findings


def test_all_market_ids_masked_together() -> None:
    r = _redactor("SG", "HK", "JP", "AU")
    out = r.redact(f"{_SG_NRIC} / {_HK_HKID} / {_JP_MYNUMBER_VALID} / {_AU_TFN_VALID} / {_EMAIL}")
    for raw in (_SG_NRIC, _HK_HKID, _JP_MYNUMBER_VALID, _AU_TFN_VALID, _EMAIL):
        assert raw not in out.text


def test_every_dlp_pattern_is_re2_compatible() -> None:
    """DLP matches custom info types with RE2, which has no lookaround.

    A row shipped with a lookaround makes DLP reject the whole inspect config
    (INVALID_ARGUMENT), so the managed profile fails on every call instead of degrading, and
    no SDK-free test would see it. B5 ships a real DLP adapter, so this is not hypothetical.
    The pack keeps an RE2-safe form per affected row; this asserts the DLP adapter only ever
    emits those. Checked structurally (RE2 rejects exactly the Perl operators `(?=`, `(?!`,
    `(?<=`, `(?<!`) so the test needs no RE2 dependency.
    """
    from loan_doc_intel.adapters.gcp.dlp_redaction import DlpRedactionAdapter

    forbidden = ("(?=", "(?!", "(?<=", "(?<!")
    for market in (*Settings().pii.jurisdictions, "IN", "GB"):
        adapter = DlpRedactionAdapter(Settings(pii=PiiSettings(jurisdictions=(market,))))
        for custom in adapter._custom_info_types():
            pattern = custom["regex"]["pattern"]
            name = custom["info_type"]["name"]
            assert not any(op in pattern for op in forbidden), f"{market}/{name}: {pattern}"


def test_dlp_custom_info_types_come_from_the_pack() -> None:
    """The managed profile must detect the same identifiers as the local one.

    An adapter carrying private Singapore-only regexes masks strictly less than the pack the
    eval gate proves, and nothing can see the difference. Email and phone are left to DLP's
    built-in types rather than duplicated.
    """
    from loan_doc_intel.adapters.gcp.dlp_redaction import DlpRedactionAdapter

    adapter = DlpRedactionAdapter(Settings(pii=PiiSettings(jurisdictions=("SG", "HK", "JP", "AU"))))
    names = {c["info_type"]["name"] for c in adapter._custom_info_types()}
    assert {"SG_NRIC_FIN", "HK_HKID", "JP_MY_NUMBER", "AU_TFN", "BANK_ACCOUNT_NUMBER"} <= names
    assert "EMAIL_ADDRESS" not in names  # built-in; not duplicated as a custom type


def test_re2_pattern_falls_back_to_the_row_itself() -> None:
    """Only rows that need an override carry one, so the two forms cannot drift silently."""
    from loan_doc_intel.domain.pii_patterns import NATIONAL_ID_PATTERNS, re2_pattern_for

    info_type, pattern, _ = NATIONAL_ID_PATTERNS["SG"][0]
    assert re2_pattern_for(info_type, pattern) == pattern.pattern

    jp_type, jp_pattern, _ = NATIONAL_ID_PATTERNS["JP"][0]
    assert re2_pattern_for(jp_type, jp_pattern) != jp_pattern.pattern  # lookarounds dropped


def test_unknown_jurisdiction_degrades_to_the_universal_rows_only() -> None:
    r = _redactor("XX")  # unknown ISO code: no national-id pack, universal PII still applies
    out = r.redact(f"NRIC {_SG_NRIC}, email {_EMAIL}")
    # The national id survives (its pack was not configured) ...
    assert _SG_NRIC in out.text
    # ... but the universal email is still masked, and the adapter never raises.
    assert _EMAIL not in out.text
    assert {f.info_type for f in out.findings} == {"EMAIL_ADDRESS"}


def test_env_override_retargets_the_pack() -> None:
    """``LOAN_DOC_PII_JURISDICTIONS`` retargets the pack without editing YAML."""
    import os
    from unittest import mock

    from loan_doc_intel.config import _pii_settings

    with mock.patch.dict(os.environ, {"LOAN_DOC_PII_JURISDICTIONS": "gb, in "}):
        assert _pii_settings({}).jurisdictions == ("GB", "IN")
