"""Shared LLM-grounding routine (private to the domain layer).

The B5 services that call the LLM (income normalisation, discrepancy explanation) share
the same skeleton: render the document extracts into a prompt context block, call the
LLM with a structured-output schema, defensively parse the JSON, coerce fields onto the
domain enums, and map the model's ``used_document_ids`` back to ``Citation`` objects that
point at the source documents.

This module factors out that machinery so each service keeps a clean signature while
sharing one well-tested core. It is ``_``-prefixed and not part of the public domain API.

Pure domain code : talks only to ports and models, no Google Cloud / ADK imports.
"""

from __future__ import annotations

import json
from typing import Any

from .models import (
    Citation,
    DocumentExtract,
    IncomeKind,
    IncomePeriod,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    SourceType,
    ThinkingLevel,
)
from .prompts import EXTRACT_BLOCK

_PERIOD_BY_VALUE: dict[str, IncomePeriod] = {p.value: p for p in IncomePeriod}
_KIND_BY_VALUE: dict[str, IncomeKind] = {k.value: k for k in IncomeKind}


def coerce_period(value: Any, default: IncomePeriod = IncomePeriod.MONTHLY) -> IncomePeriod:
    """Map a model-emitted period string onto the ``IncomePeriod`` enum defensively."""
    if isinstance(value, IncomePeriod):
        return value
    if isinstance(value, str):
        return _PERIOD_BY_VALUE.get(value.strip().lower(), default)
    return default


def coerce_kind(value: Any, default: IncomeKind = IncomeKind.SALARY) -> IncomeKind:
    """Map a model-emitted income-kind string onto the ``IncomeKind`` enum defensively."""
    if isinstance(value, IncomeKind):
        return value
    if isinstance(value, str):
        return _KIND_BY_VALUE.get(value.strip().lower(), default)
    return default


def coerce_amount(value: Any) -> float | None:
    """Coerce a model-emitted amount to a float, or ``None`` when it is not a number.

    Three states, never two. This returned ``0.0`` for anything it could not parse until
    2026-08-28, so a model answering "not disclosed" or "see attached" produced an income figure
    of 0.00 against a real source document and nothing downstream could tell that apart from a
    genuine zero. These are money: a fabricated 0.00 drags a monthly average down and
    manufactures a discrepancy against the applicant's declared income, so a clean file gets
    flagged on a figure the lender never saw.

    ``cross_validator._amount`` parses the same shape of string and has always returned ``None``.
    The deterministic half of this repository was right and the model-facing half was not; this
    is the model-facing half agreeing with it. The caller skips a figure it cannot read rather
    than inventing one.
    """
    if isinstance(value, bool):  # bool is an int subclass, and True is not an amount
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def render_extracts(extracts: list[DocumentExtract]) -> str:
    """Render document extracts into the numbered context block for the prompt.

    Each block is keyed by ``document_id`` so the model can echo it as the citation key.
    Flat ``fields`` and dated line items are flattened into readable lines.
    """
    if not extracts:
        return "(no document extracts were produced)"
    blocks: list[str] = []
    for e in extracts:
        lines = [f"  {k}: {v}" for k, v in e.fields.items()]
        for item in e.line_items:
            dated = f" on {item.date}" if item.date else ""
            lines.append(f"  line: {item.label} {item.amount} {item.currency}{dated}")
        blocks.append(
            EXTRACT_BLOCK.format(
                document_id=e.document_id,
                doc_type=e.doc_type.value,
                period=e.period or "?",
                confidence=f"{e.confidence:.2f}",
                fields="\n".join(lines) if lines else "  (no fields)",
            )
        )
    return "\n".join(blocks)


def parse_structured(response: LlmResponse) -> dict[str, Any]:
    """Parse an LLM structured-output response into a dict, defensively.

    The GCP adapter returns the structured JSON as ``LlmResponse.text`` when a
    ``response_schema`` is set. We ``json.loads`` it; on any failure (plain text,
    truncation, a fenced block) we fall back to extracting the first balanced JSON
    object, and finally to an empty dict so callers degrade gracefully rather than
    raising on a malformed model reply.
    """
    text = (response.text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"items": parsed}
    except (json.JSONDecodeError, ValueError):
        pass

    snippet = _extract_json_object(text)
    if snippet is not None:
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` block in ``text``, or None."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def citations_for_document_ids(
    used_document_ids: list[str],
    extracts: list[DocumentExtract],
    field_label: str = "",
) -> tuple[Citation, ...]:
    """Map model-returned ``used_document_ids`` back to source-document Citations.

    Builds one citation per cited document that was actually extracted, carrying the
    document title (its first field title or the document id) and the page from the
    extract's own citations when available. Unknown ids the model may have hallucinated
    are dropped : we only ever cite documents we actually processed.
    """
    by_id: dict[str, DocumentExtract] = {e.document_id: e for e in extracts}
    wanted = list(used_document_ids or [])
    selected = [d for d in wanted if d in by_id]
    if not selected:
        selected = list(by_id.keys())

    out: list[Citation] = []
    seen: set[str] = set()
    for doc_id in selected:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        extract = by_id[doc_id]
        page = extract.citations[0].page if extract.citations else None
        title = extract.fields.get("title") or extract.doc_type.value
        out.append(
            Citation(
                source_id=doc_id,
                source_type=SourceType.DOCUMENT,
                title=title,
                page=page,
                field=field_label,
            )
        )
    return tuple(out)


def build_llm_request(
    system_instruction: str,
    user_content: str,
    model: str | None,
    response_schema: dict | None,
    thinking: ThinkingLevel = ThinkingLevel.HIGH,
    temperature: float = 0.0,
    max_output_tokens: int = 4096,
) -> LlmRequest:
    """Assemble an ``LlmRequest`` with a single user message and a system prompt.

    ``model=None`` lets the adapter pick its configured default (the reasoning model,
    ``gemini-3.5-flash``); thinking defaults to HIGH for grounded reasoning per SPEC.
    """
    return LlmRequest(
        messages=(LlmMessage(role="user", content=user_content),),
        system_instruction=system_instruction,
        model=model,
        thinking=thinking,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_schema=response_schema,
    )


def maybe_record_usage(tracer: Any, response: Any) -> None:
    """Emit token usage to the tracer for FinOps, defensively (never fatal)."""
    try:
        usage = getattr(response, "usage", None)
        model = getattr(response, "model", "") or ""
        if usage is not None and hasattr(tracer, "record_token_usage"):
            tracer.record_token_usage(usage, model)
    except Exception:  # noqa: BLE001 - metrics must never break a generation path
        return


def as_str_list(value: Any) -> list[str]:
    """Coerce an arbitrary model value into a list of stripped non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
