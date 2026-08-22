"""JSON-safe serialization for domain objects.

``to_jsonable(obj)`` converts dataclasses, enums, datetimes and nested containers into
plain JSON-serializable Python. Used by the platform HTTP clients and the audit sink.

**Sourced from the shared ``hex-service-kit`` commons.** The walker lives in
:mod:`hex_service_kit.serialization` rather than as a copy here, and is re-exported
(same rules: enum ``.value``, ISO datetimes, dataclass field dicts, tuples to lists,
stringified keys, never raises). Pure standard library.
"""

from __future__ import annotations

from typing import Any

from hex_service_kit.serialization import to_jsonable

__all__ = ["citation_to_dict", "to_jsonable"]


def citation_to_dict(citation: Any) -> dict[str, Any]:
    """Serialize a citation-like dataclass to a plain dict (guaranteed dict result)."""
    result = to_jsonable(citation)
    if isinstance(result, dict):
        return result
    return {"value": result}
