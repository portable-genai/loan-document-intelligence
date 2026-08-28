"""Document AI may sit in a named multi-region, and in nothing else.

`Settings.__post_init__` required `document_ai.location == region`, which is the right instinct
for a build whose headline claim is residency. It also made the configuration unloadable: the
settings file routes Document AI to the `us` multi-region, with the reason written beside it
(Singapore is "limited support" for Document AI and access is gated behind Google's Single
Region Request Form), and the validation forbade exactly that. Every load raised, so the whole
suite failed on import rather than on anything it was testing.

The same deviation already exists one field away and is allowed there: `models.location`
defaults to `us` and carries no equality check at all, because the Vertex location and the
compute region are separate axes. This makes Document AI consistent with that, and STRICTER:

* the deploy region stays pinned to the allowlist, unchanged;
* Document AI may equal the region, which is the preferred state;
* or it may be a named MULTI-REGION, which names one jurisdiction and carries Google's
  ML-processing commitment for that geography;
* and anything else is refused, including `global`, which names no jurisdiction and is exactly
  the widening someone reaches for to make an apply succeed.

The deviation is a stated one, not a silent one: document bytes are extracted in the United
States while the rest of the stack stays in region, and that sentence belongs in the residency
record rather than only in a config comment.
"""

from __future__ import annotations

import dataclasses

import pytest

from loan_doc_intel.config import DocumentAiSettings, Settings


def _settings(location: str) -> Settings:
    return Settings(document_ai=DocumentAiSettings(location=location))


def test_the_region_itself_is_allowed() -> None:
    assert _settings("asia-southeast1").document_ai.location == "asia-southeast1"


@pytest.mark.parametrize("multi_region", ["us", "eu"])
def test_a_named_multi_region_is_allowed_as_a_stated_deviation(multi_region: str) -> None:
    assert _settings(multi_region).document_ai.location == multi_region


def test_global_is_refused_because_it_names_no_jurisdiction() -> None:
    with pytest.raises(ValueError, match="global"):
        _settings("global")


@pytest.mark.parametrize("elsewhere", ["us-central1", "europe-west2", "asia-northeast1"])
def test_another_single_region_is_refused(elsewhere: str) -> None:
    """A different single region is neither the deploy region nor a multi-region commitment."""
    with pytest.raises(ValueError):
        _settings(elsewhere)


def test_an_empty_location_is_refused_rather_than_inheriting_the_region() -> None:
    """Set-and-empty is not unset: it names nothing, so it must not take the documented default."""
    with pytest.raises(ValueError):
        _settings("")


def test_the_deploy_region_allowlist_is_untouched() -> None:
    """This change widens Document AI only. The region itself stays pinned."""
    with pytest.raises(ValueError, match="allowlist"):
        dataclasses.replace(_settings("us"), region="us-central1")
