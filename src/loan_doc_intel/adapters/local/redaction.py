"""Local PII redaction adapter (PIIRedactionPort) : regex de-identification.

The ``local`` profile's stand-in for **Sensitive Data Protection / DLP**: masks the national
identifiers for the configured jurisdiction(s) plus the universal email / phone / bank
account rows (the applicant PII B5 handles, rule R1) with deterministic regexes, returning
findings. Applicant PII is removed at the boundary before it reaches a model, a trace span
or the audit sink (P-04). The pattern set is jurisdiction-driven
(``settings.pii.jurisdictions``, default SG/HK/JP/AU) so a non-APAC lending book detects its
own identifiers by config, not a code change (see ``domain/pii_patterns.py``). There is no
Google emulator for DLP, so this path is unconditional and imports no google-cloud package.

Row order comes from the pack and is load-bearing: the ``BANK_ACCOUNT_NUMBER`` row runs
FIRST, ahead of the national ids, because the AU TFN row matches the leading nine digits of
B5's 3-6-1 account shape and some of those pass the TFN checksum, which would report an
applicant's account number as a tax file number. That is the REVERSE of the trade-finance
vertical this pack mirrors, whose account row is a bare-digit catch-all that must run
last. The pack's module docstring explains why, and ``tests/unit/test_redaction_service.py``
pins both directions.

The mask token is the info type (``[SG_NRIC_FIN]``, ``[BANK_ACCOUNT_NUMBER]``) rather than
the older hand-written ``[NRIC]`` / ``[ACCOUNT]`` labels, so what a reviewer reads in a
redacted audit record names the same row the findings and the eval gate name.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ...config import Settings
from ...domain.models import RedactionFinding, RedactionResult
from ...domain.pii_patterns import patterns_for


def _mask_validated(
    pattern: re.Pattern[str], info_type: str, validator: Callable[[str], bool], text: str
) -> tuple[str, int]:
    """Mask only the matches of ``pattern`` in ``text`` that pass ``validator``."""
    count = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal count
        if validator(match.group(0)):
            count += 1
            return f"[{info_type}]"
        return match.group(0)

    return pattern.sub(_repl, text), count


class LocalRegexRedactionAdapter:
    """Mask the configured jurisdictions' national ids + email/phone/account, like DLP."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._patterns = patterns_for(settings.pii.jurisdictions)

    def redact(self, text: str) -> RedactionResult:
        findings: list[RedactionFinding] = []
        redacted = text
        for info_type, pattern, validator in self._patterns:
            if validator is None:
                hits = pattern.findall(redacted)
                if hits:
                    redacted = pattern.sub(f"[{info_type}]", redacted)
                    findings.append(RedactionFinding(info_type=info_type, count=len(hits)))
            else:
                # Checksum-gated: report only genuine identifiers under this info type.
                redacted, count = _mask_validated(pattern, info_type, validator, redacted)
                if count:
                    findings.append(RedactionFinding(info_type=info_type, count=count))
        return RedactionResult(text=redacted, findings=tuple(findings))
