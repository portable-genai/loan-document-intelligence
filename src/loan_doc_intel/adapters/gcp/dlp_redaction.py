"""Sensitive Data Protection (DLP) redaction adapter (A1 Guardrail Gateway).

Implements :class:`PIIRedactionPort` against **Sensitive Data Protection / DLP** of the
Gemini Enterprise Agent Platform. Applicant PII (names, addresses, NRIC, bank account
numbers, income) is de-identified at the boundary : before it reaches a model or the WORM
audit sink : so PII is minimised to the model (P-04). The call is regional
(``projects/{project}/locations/{region}``) to keep inspection inside Singapore for
residency.

If inspect/de-identify templates are configured in settings, they are used as-is.
Otherwise the adapter builds an inline configuration that masks the built-in info types most
relevant to a retail-lending PII corpus (names, emails, phone numbers, card numbers, IBANs,
street addresses) plus the national identifiers and account-number shape for the
jurisdictions configured in ``settings.pii.jurisdictions``, sourced from
``domain/pii_patterns.py``. That shared source is the point: the managed and local redactors
detect the same identifiers, so switching profile does not silently change what is masked,
and the eval gate scores the same shapes it redacts. An adapter carrying its own private
``SG_NRIC_FIN`` and ``BANK_ACCOUNT_NUMBER`` regexes gets upper-case-only, Singapore-only
matching: a managed deployment then masks strictly less than the pack the gate proves.

Two known deviations from the ``local`` redactor, both stated rather than papered over:

* A DLP custom info type is regex-only, with no hook for a checksum, so the JP My Number, AU
  TFN and bare HK HKID rows are matched here on shape alone while ``local`` additionally
  checksum-gates them. The managed profile therefore masks strictly MORE than local, which is
  the fail-safe direction at a redaction boundary.
* DLP matches custom info types with **RE2**, which has no lookaround, so a row whose Python
  regex uses one cannot be sent verbatim: DLP rejects the entire inspect config with
  INVALID_ARGUMENT, which fails the call rather than degrading it, and no SDK-free test would
  see it. The pack owns an RE2-safe form per affected row (``re2_pattern_for``, today only
  ``JP_MY_NUMBER``) so the two forms cannot drift; what RE2 loses there is only the rejection
  of a 12-digit prefix of a longer GROUPED run, i.e. it masks more.

Row order is not carried here, and does not need to be: DLP resolves overlapping custom info
types itself. The order that matters is the ``local`` redactor's (see
``domain/pii_patterns.py``), where the rows are applied in sequence over the text.

The ``google.cloud.dlp_v2`` import is lazy so on-prem and test profiles load this module
with no GCP SDK installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import RedactionFinding, RedactionResult
from ...domain.pii_patterns import patterns_for, re2_pattern_for

# Built-in info types masked when no de-identify template is configured. The applicant's
# national id and account shapes come from the shared pack, not from this module.
_DEFAULT_INFO_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD_NUMBER",
    "IBAN_CODE",
    "STREET_ADDRESS",
)

# Built-in DLP types that duplicate a universal pack row; skipped as custom info types so a
# single identifier is not transformed twice under two names.
_BUILTIN_EQUIVALENTS: frozenset[str] = frozenset({"EMAIL_ADDRESS", "PHONE_NUMBER"})

_MASKING_CHAR = "#"


class DlpRedactionAdapter:
    """De-identify PII via DLP ``deidentify_content`` (templates or inline config)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dlp = settings.dlp
        self._parent = f"projects/{settings.project_id}/locations/{settings.region}"
        self._jurisdictions = settings.pii.jurisdictions
        self._client: Any | None = None

    # -- public API -------------------------------------------------------- #
    def redact(self, text: str) -> RedactionResult:
        """Return de-identified text plus per-info-type finding counts."""
        if not text:
            return RedactionResult(text=text, findings=())

        client = self._service_client()
        request = self._build_request(text)
        response = client.deidentify_content(request=request)

        redacted_text: str = response.item.value
        findings = self._summarise(response)
        return RedactionResult(text=redacted_text, findings=findings)

    # -- client / request -------------------------------------------------- #
    def _service_client(self) -> Any:
        from google.cloud import dlp_v2  # lazy

        if self._client is None:
            self._client = dlp_v2.DlpServiceClient()
        return self._client

    def _build_request(self, text: str) -> dict[str, Any]:
        request: dict[str, Any] = {
            "parent": self._parent,
            "item": {"value": text},
        }
        if self._dlp.deidentify_template:
            request["deidentify_template_name"] = self._dlp.deidentify_template
        else:
            request["deidentify_config"] = self._inline_deidentify_config()

        if self._dlp.inspect_template:
            request["inspect_template_name"] = self._dlp.inspect_template
        elif not self._dlp.deidentify_template:
            request["inspect_config"] = self._inline_inspect_config()
        return request

    # -- inline fallback configuration ------------------------------------- #
    def _custom_info_types(self) -> list[dict[str, Any]]:
        """The configured jurisdictions' identifiers, as DLP custom info types.

        Derived from the same ``domain/pii_patterns.py`` rows the local redactor and the
        eval gate use, so the three cannot drift. The validator is dropped (DLP regexes
        cannot carry a checksum) and the pattern is taken via ``re2_pattern_for`` because
        DLP matches with RE2, which rejects the lookarounds the JP row needs; see the module
        docstring on why matching on shape alone is the safe direction here. Rows DLP already
        detects natively (email, phone) are left to the built-in types.

        A row may appear under one info type in more than one shape (HK's parenthesised and
        bare HKID forms), which DLP accepts: several custom info types may share a name, and
        the transformation is keyed on the name.
        """
        # verify: https://cloud.google.com/dlp/docs/creating-custom-infotypes-likelihood
        custom: list[dict[str, Any]] = []
        for info_type, pattern, _validator in patterns_for(self._jurisdictions):
            if info_type in _BUILTIN_EQUIVALENTS:
                continue
            custom.append(
                {
                    "info_type": {"name": info_type},
                    "regex": {"pattern": re2_pattern_for(info_type, pattern)},
                    "likelihood": "POSSIBLE",
                }
            )
        return custom

    def _inline_inspect_config(self) -> dict[str, Any]:
        # verify: https://cloud.google.com/dlp/docs/reference/rest/v2/InspectConfig
        return {
            "info_types": [{"name": name} for name in _DEFAULT_INFO_TYPES],
            "custom_info_types": self._custom_info_types(),
            "min_likelihood": "POSSIBLE",
            "include_quote": False,
        }

    def _inline_deidentify_config(self) -> dict[str, Any]:
        # Mask every detected info type (built-in + the custom types) with a single masking
        # character : irreversible, no surrogate to reverse. Custom names are de-duplicated
        # because one info type may be declared under several shapes (HK's two HKID forms),
        # and a transformation names an info type once.
        # verify: https://cloud.google.com/dlp/docs/reference/rest/v2/DeidentifyConfig
        custom_names: list[str] = []
        for custom in self._custom_info_types():
            name = str(custom["info_type"]["name"])
            if name not in custom_names:
                custom_names.append(name)
        all_info_types = [{"name": name} for name in (*_DEFAULT_INFO_TYPES, *custom_names)]
        return {
            "info_type_transformations": {
                "transformations": [
                    {
                        "info_types": all_info_types,
                        "primitive_transformation": {
                            "character_mask_config": {
                                "masking_character": _MASKING_CHAR,
                            }
                        },
                    }
                ]
            }
        }

    # -- finding summary --------------------------------------------------- #
    def _summarise(self, response: Any) -> tuple[RedactionFinding, ...]:
        overview = getattr(response, "overview", None)
        summaries = getattr(overview, "transformation_summaries", None) or []
        findings: list[RedactionFinding] = []
        for summary in summaries:
            info_type = getattr(getattr(summary, "info_type", None), "name", "")
            if not info_type:
                continue
            count = self._transformed_count(summary)
            findings.append(RedactionFinding(info_type=info_type, count=count))
        return tuple(findings)

    @staticmethod
    def _transformed_count(summary: Any) -> int:
        total = 0
        for result in getattr(summary, "results", None) or []:
            code = getattr(result, "code", None)
            code_name = getattr(code, "name", str(code))
            if code_name == "SUCCESS":
                total += int(getattr(result, "count", 0) or 0)
        if total <= 0:
            total = int(getattr(summary, "transformed_bytes", 0) or 0) and 1 or 1
        return total
