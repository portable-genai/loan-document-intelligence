"""Put what the runtime controls did on a response, so the user who asked can see it.

Two fields, defined on every response model that can carry them:

* ``input_redacted``: redaction changed the application before the model saw it.
* ``review_routing``: what happened to the human-review hand-off, one of ``routed``,
  ``failed``, ``off`` or ``not_required``.

The values come from the request-scoped wrappers in :mod:`..adapters.controls`, which the
route receives from the same FastAPI dependency its service was built with.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..adapters.controls import DisclosingRedaction, RecordingReviewRouter


def disclose[ResponseT: BaseModel](
    response: ResponseT,
    *,
    redaction: DisclosingRedaction | None = None,
    routing: RecordingReviewRouter | None = None,
) -> ResponseT:
    """Return ``response`` with the controls' outcome for this request filled in."""
    update: dict[str, object] = {}
    if redaction is not None:
        update["input_redacted"] = redaction.changed
    if routing is not None:
        update["review_routing"] = routing.outcome.value
    return response.model_copy(update=update)
