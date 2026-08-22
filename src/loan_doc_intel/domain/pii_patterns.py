"""Jurisdiction-driven PII pattern packs (the compliance-pack PII axis).

The redact-before-everything boundary (R1, P-04) and the eval gate's ``pii_safety`` metric
both need to know what a loan applicant's identifier LOOKS like, and that is
jurisdiction-specific: an applicant in Japan carries a My Number, one in Australia a TFN, one
in Singapore an NRIC, one in Hong Kong an HKID. Keeping the patterns here (pure stdlib) and
selecting by jurisdiction means a fork changes a config list, not code, and its
``pii_safety`` gate actually exercises its own identifiers instead of being falsely green.
B5's retail-lending book is APAC, so the SG / HK / JP / AU packs are the default.

Add a jurisdiction by adding a ``(info_type, regex, validator)`` row to
:data:`NATIONAL_ID_PATTERNS`. ``EMAIL``, ``PHONE`` and ``BANK_ACCOUNT_NUMBER`` are universal
and always applied: B5's PII is the applicant themselves (name, address, national id) and
the **bank accounts** their salary is credited to, so an account number is first-class PII
for this vertical rather than an extra (see ``ports/safety.py`` and the ``redactable`` field
set in ``domain/loan_doc_service.py``).

Ordering is load-bearing, and it INVERTS the trade-finance vertical's rule
-----------------------------------------------------------------------
:func:`patterns_for` returns the rows in the order the redactor applies them, and the
``BANK_ACCOUNT_NUMBER`` row is deliberately FIRST, ahead of the national ids. That is the
opposite of ``trade-finance-checker``, whose pack this one mirrors, and the
difference is not cosmetic.

That vertical's account row is a bare-digit catch-all (``\\b\\d{9,17}\\b``) which SUBSUMES the
contiguous national-id shapes, so it must run LAST or it masks a My Number as an account.
B5's account row is not a catch-all: it is the specific hyphenated 3-6-1 shape a bank
statement prints (``123-456789-0``). Nothing about a national id subsumes it, but it
subsumes nothing either, and the AU TFN row bites INTO it: ``123-456782-0`` has leading nine
digits (``123-456782``) that match the TFN row's ``\\d{3}[\\s-]?\\d{3}[\\s-]?\\d{3}`` and PASS
the TFN checksum, so with the national ids first an applicant's account number is reported
to a reviewer as an ``AU_TFN``, the account row never fires, and the trailing check digit
survives. ``123-000007-0`` does the same, so this is not a corner case: round account
numbers are common. Running the account row first takes the whole account under its true
info type and leaves the national-id rows the text that is actually left over.
``tests/unit/test_redaction_service.py`` pins both directions.

Because there is no bare-digit catch-all here, the checksum validators buy something
DIFFERENT than they do in the trade-finance pack. There, a 9-digit run that fails the TFN
check is masked anyway (as an account), so the checksum only decides the LABEL. Here nothing
else claims a bare digit run, so the checksums genuinely keep an applicant's ordinary
figures (a payslip reference, a property price, a policy number) out of the mask while still
catching the GROUPED forms (``123 456 782``, ``1234 5678 9018``) that the account row cannot
see at all. That is the ``credit-memo-drafting`` reasoning rather than the
trade-finance one, and it is why the rows below are checksum-gated even though this
vertical's own account shape is specific.

The rows themselves mirror ``trade-finance-checker``, which corrects three rows
that the earlier sibling packs narrow away from the ``onprem-dlp`` recognizers
they claim to mirror: ``JP_MY_NUMBER`` as ``\\b\\d{12}\\b`` misses ``1234 5678 9018`` (the form
a My Number card is printed in), ``SG_NRIC_FIN`` upper-case-only misses a lower-cased NRIC
typed into a free-text field, and an ``HK_HKID`` row requiring the parens leaks the bare
keyed form ``A1234563``. Because the eval gate scores its leak check off THESE SAME rows, a
narrowed row is neither masked nor detected: a vacuous 1.0. Take these rows, not the
narrowed ones.

The residual, stated rather than hidden: ``agent/callbacks.py`` redacts the prose the model
is about to read, so an ordinary figure there that happens to have an identifier's shape is
masked. An 8-digit run starting 6/8/9 (a property price of ``85000000``) matches the SG
phone row. That is pinned by ``tests/unit/test_redaction_service.py`` so a future reader
meets it as a decision instead of a surprise. It does NOT touch the deterministic checks:
``_redact_extract`` redacts only the free-text identity fields (name / address / employer /
account_holder / title), never the numeric fields the CrossValidator reads, so a masked
figure can degrade a narration but can never move a verdict. ``onprem-dlp`` cuts this
class of over-match with ``require_context`` hotwords; adopting that would change the SHARED
pack every vertical's redactor and eval gate score off, so it belongs in one cross-repo
change rather than this rollout.

Each row is a 3-tuple ``(info_type, re.Pattern, validator | None)``; ``validator`` (when
present) is a ``str -> bool`` predicate applied to each raw match, so both the redactor and
the eval leak-check mask/detect a match only when it is a genuine identifier.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

#: A single redaction rule: an info type, the regex that finds it, and an optional
#: checksum validator applied to each raw match before it is treated as a real identifier.
Pattern = tuple[str, re.Pattern[str], Callable[[str], bool] | None]


def hk_hkid_valid(value: str) -> bool:
    """Validate a Hong Kong Identity Card number: prefix + 6 digits + check character.

    Mirrors ``onprem-dlp`` (``onprem_dlp.domain.recognizers.hk_hkid_valid``). Only
    the BARE form needs this: ``A123456(3)`` is unambiguous and is masked on shape alone,
    but the keyed form ``A1234563`` has the same shape as a loan document reference
    (``PS1234567``), so it is masked only when the check character actually computes.
    """
    m = re.fullmatch(r"([A-Z]{1,2})(\d{6})\(?([0-9A])\)?", value.upper())
    if not m:
        return False
    prefix, digits, check = m.groups()
    vals = [36, ord(prefix) - 55] if len(prefix) == 1 else [ord(c) - 55 for c in prefix]
    vals.extend(int(c) for c in digits)
    s = sum(v * w for v, w in zip(vals, range(9, 1, -1), strict=True))
    r = (11 - s % 11) % 11
    return check == ("A" if r == 10 else str(r))


def jp_my_number_valid(value: str) -> bool:
    """Validate a Japanese Individual Number (My Number): 12 digits + check digit.

    Mirrors ``onprem-dlp`` (``onprem_dlp.domain.recognizers.jp_my_number_valid``),
    the catalog's authority on these recognizers, so a 12-digit run is reported as a My
    Number only when it genuinely is one and not an account or reference number. The check
    digit is computed over the first 11 digits.

    Separators are stripped with ``[\\s-]``, not the authority's ``[ -]``, because this
    pack's rows admit ``[\\s-]`` as a separator. Any mismatch there is a silent leak: a
    match the regex accepts but the validator cannot normalise fails ``isdigit()``, so it is
    neither masked nor (since the eval scores off these same rows) detected.
    """
    digits = re.sub(r"[\s-]", "", value)
    if not digits.isdigit() or len(digits) != 12:
        return False
    if len(set(digits)) == 1:
        return False  # an all-identical run is not a real My Number
    s = 0
    for n in range(1, 12):
        p = int(digits[11 - n])  # P_n: nth digit from the right of the first 11
        q = n + 1 if n <= 6 else n - 5
        s += p * q
    r = s % 11
    return int(digits[11]) == (0 if r <= 1 else 11 - r)


def au_tfn_valid(value: str) -> bool:
    """Validate an Australian Tax File Number: 9 digits, weighted checksum mod 11.

    Mirrors ``onprem-dlp`` (``onprem_dlp.domain.recognizers.au_tfn_valid``). Unlike
    the trade-finance vertical, a 9-digit run that fails this check is masked by NOTHING
    here (there is no bare-digit account catch-all), so this validator decides whether an
    applicant's ordinary figures survive as well as what a reviewer sees.

    Separators are stripped with ``[\\s-]`` rather than the authority's ``[ -]`` because
    this row's regex admits ``[\\s-]``. See :func:`jp_my_number_valid`: a TFN separated by a
    non-breaking space (which PDF text extraction routinely emits, and this redactor runs
    over parser output in ``_redact_extract``) would otherwise match the regex, fail the
    validator, and leak undetected.
    """
    digits = re.sub(r"[\s-]", "", value)
    if not digits.isdigit() or len(digits) != 9:
        return False
    weights = (1, 4, 3, 7, 5, 8, 6, 9, 10)
    # strict: the length check above pins digits to exactly the 9 weights.
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11 == 0


# Universal PII, applied for every jurisdiction.
_EMAIL: Pattern = (
    "EMAIL_ADDRESS",
    re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    None,
)
_PHONE_INTL: Pattern = (
    "PHONE_NUMBER",
    re.compile(r"\+\d{1,3}[\s-]?\d(?:[\s-]?\d){6,13}\b"),
    None,
)
#: The applicant's salary-crediting account: B5's own PII, not a bonus. This is the specific
#: 3-6-1 shape a bank statement prints, NOT the trade-finance pack's bare-digit catch-all.
#: Applied FIRST because the AU TFN row bites into it (see the module docstring).
_BANK_ACCOUNT: Pattern = (
    "BANK_ACCOUNT_NUMBER",
    re.compile(r"\b\d{3}-\d{6}-\d\b"),
    None,
)

#: Per-jurisdiction national-identifier patterns (ISO-3166 alpha-2 -> list of rows).
NATIONAL_ID_PATTERNS: dict[str, list[Pattern]] = {
    "SG": [
        # Case-insensitive, like the onprem-dlp recognizer: a lower-cased NRIC typed
        # into a free-text field is an NRIC, and an upper-only row would neither mask nor
        # detect it. No checksum: the shape does not collide with ordinary text, and recall
        # at the boundary is worth more than checksum precision.
        ("SG_NRIC_FIN", re.compile(r"\b[STFGMstfgm]\d{7}[A-Za-z]\b"), None),
        ("SG_PHONE", re.compile(r"\b(?:\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}\b"), None),
    ],
    "HK": [
        # Two rows, because the two forms carry different risks. The parenthesised form is
        # unambiguous, so it is masked on shape (any HKID, even one whose check character is
        # mistyped). The bare keyed form collides with loan document references
        # (``PS1234567`` has the identical shape), so it is checksum-gated instead of either
        # leaking or eating every payslip reference.
        ("HK_HKID", re.compile(r"\b[A-Z]{1,2}\d{6}\([0-9A]\)"), None),
        ("HK_HKID", re.compile(r"\b[A-Z]{1,2}\d{6}[0-9A]\b"), hk_hkid_valid),
    ],
    "JP": [
        # Checksum-gated so an applicant's ordinary 12-digit figures are not masked. The
        # 4-4-4 grouping is the form a My Number card is printed in, and it is the only
        # thing in this pack that sees it. The leading lookarounds also reject a 12-digit
        # prefix of a longer grouped run (e.g. the first 12 digits of a 16-digit card PAN,
        # which can pass the My Number checksum by chance). Both come from the
        # onprem-dlp recognizer this row mirrors.
        (
            "JP_MY_NUMBER",
            re.compile(r"(?<!\d)(?<!\d[- ])\d{4}[- ]?\d{4}[- ]?\d{4}(?![- ]?\d)"),
            jp_my_number_valid,
        ),
    ],
    "AU": [
        # Likewise a 9-digit run. This row runs AFTER the account row: it matches the
        # leading nine digits of the 3-6-1 account shape, and some of those pass the TFN
        # checksum (see the module docstring).
        ("AU_TFN", re.compile(r"\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b"), au_tfn_valid),
    ],
    "IN": [
        ("IN_PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), None),
        ("IN_AADHAAR", re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), None),
    ],
    "GB": [
        # National Insurance number (prefix rules simplified to a safe superset).
        ("GB_NINO", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b"), None),
    ],
}

#: Reference default: B5's APAC retail-lending markets.
DEFAULT_JURISDICTIONS: tuple[str, ...] = ("SG", "HK", "JP", "AU")


#: RE2-safe equivalents, by info type, for rows whose Python regex uses syntax RE2 rejects.
#: Google Cloud DLP custom info types are matched with RE2, which has no lookaround, so the
#: JP row's lookarounds make DLP reject the whole inspect config with INVALID_ARGUMENT: the
#: managed profile would fail on every call rather than degrade. RE2 supports ``\b``, which
#: still rejects a 12-digit prefix of a longer CONTIGUOUS run; what is lost is only the
#: rejection of a 12-digit prefix of a longer GROUPED run, which merely masks more. That is
#: the same fail-safe direction as the missing checksum (see the DLP adapter's docstring),
#: and both live here so the two forms of a row cannot drift apart.
_RE2_OVERRIDES: dict[str, str] = {
    "JP_MY_NUMBER": r"\b\d{4}[- ]?\d{4}[- ]?\d{4}\b",
}


def re2_pattern_for(info_type: str, pattern: re.Pattern[str]) -> str:
    """The RE2-compatible source for a row, for consumers that cannot run Python regex."""
    return _RE2_OVERRIDES.get(info_type, pattern.pattern)


def patterns_for(jurisdictions: Iterable[str]) -> list[Pattern]:
    """The redaction rows for ``jurisdictions``, in the order they must be applied.

    Universal email/phone first, then the ``BANK_ACCOUNT_NUMBER`` row, then each
    jurisdiction's national ids. The account row runs AHEAD of the national ids because the
    AU TFN row matches the leading nine digits of B5's 3-6-1 account shape and some of those
    pass the TFN checksum, which would report an applicant's account as a tax file number
    (see the module docstring). This is the reverse of the trade-finance pack, whose account
    row is a bare-digit catch-all that must run last.

    Unknown jurisdiction codes contribute no national-ID pattern (the universal rows still
    apply), so a partially-configured fork degrades safely rather than raising.

    De-duplication is keyed on the (info type, regex) pair, not the info type alone: two
    jurisdictions may legitimately share a row, but one jurisdiction may also carry the SAME
    info type under two shapes (HK's parenthesised and bare HKID forms), and keying on the
    name alone would silently drop the second.
    """
    national: list[Pattern] = []
    seen: set[tuple[str, str]] = {
        (row[0], row[1].pattern) for row in (_EMAIL, _PHONE_INTL, _BANK_ACCOUNT)
    }
    for code in jurisdictions:
        for row in NATIONAL_ID_PATTERNS.get((code or "").upper(), ()):
            key = (row[0], row[1].pattern)
            if key not in seen:
                national.append(row)
                seen.add(key)
    return [_EMAIL, _PHONE_INTL, _BANK_ACCOUNT, *national]
