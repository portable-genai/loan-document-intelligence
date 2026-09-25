"""What the UI's model pill states before any answer must be true of the running profile.

Every served console shows, at the top right of every page, a pill naming the model: the
configured ``generator_model`` from ``/healthz`` (with WHERE it runs in its title) until an answer
arrives, then the model that ANSWERED, from ``X-Answered-By`` (owner decision, 2026-09-23; the
pills replaced the full-width provenance banner). Both starting values come from ``/healthz``
because the browser cannot know either: a console that read its runtime from
``window.location`` would be right until the day the deployment served through a proxy, and
wrong silently after that. ``tests/unit/test_answer_provenance.py`` owns the answering half.

The reason this is worth a test rather than a glance is what the pill is FOR. These systems
are demonstrated on a laptop and on a deployment, sometimes in the same hour, and a screenshot
of one is indistinguishable from the other. A pill that was merely present but wrong is worse
than no pill: it converts "the viewer does not know" into "the viewer has been told the wrong
thing", and the wrong thing here is whether a figure came from a managed model or from a
deterministic offline stub.

So the assertions below are about AGREEMENT with the profile, not about presence.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from loan_doc_intel.config import Settings

CONFIG_PATH = Path("config/settings.yaml")

#: Answers that mean "no managed model produced this". Each says something different and the
#: difference is the point, which is why this is a set rather than one sentinel:
#: ``deterministic-offline-stub`` says a model-shaped port is bound to a stub;
#: ``no-model`` says there is no such port at all; ``onprem-not-implemented`` says the port
#: exists and refuses. A reviewer approving an escalation is entitled to know which they read.
_NON_MANAGED_ANSWERS = frozenset(
    {
        "deterministic-offline-stub",
        "no-model",
        "onprem-not-implemented",
        "managed-model-unavailable",
    }
)


def _for_profile(profile: str) -> Settings:
    return dataclasses.replace(Settings.load(CONFIG_PATH), profile=profile)


@pytest.mark.parametrize("profile", ["local", "gcp", "onprem"])
def test_the_runtime_half_states_where_the_process_runs(profile: str) -> None:
    """``onprem`` reads ``local``, because that is its entire point.

    A managed model call does not make a process cloud-hosted. This half is about where the
    PROCESS runs and the other half is about whose model answers, and collapsing the two is how
    an on-premises deployment ends up describing itself as running on GCP.
    """
    settings = _for_profile(profile)
    assert settings.runtime == ("gcp" if profile == "gcp" else "local")


@pytest.mark.parametrize("profile", ["local", "gcp", "onprem"])
def test_the_model_half_is_always_answered(profile: str) -> None:
    """A blank is not an option: the pill renders nothing rather than render a falsehood."""
    assert _for_profile(profile).generator_model.strip()


@pytest.mark.parametrize("profile", ["local", "onprem"])
def test_no_offline_profile_claims_a_managed_model(profile: str) -> None:
    """The defect that matters, stated as an assertion.

    A laptop run naming a Gemini model is precisely the confusion the pill exists to remove,
    and it is the one direction a reviewer cannot detect by looking at the page.
    """
    answer = _for_profile(profile).generator_model
    assert answer in _NON_MANAGED_ANSWERS, (
        f"the {profile!r} profile reports {answer!r}, which reads as a managed model answering "
        "a request that never left the machine"
    )


def test_the_health_contract_carries_both_halves() -> None:
    """The wire contract the console actually reads. A property nothing serves is not a contract.

    Asserted on the response MODEL rather than by calling ``/healthz`` through a test client.
    That is not a convenience: under the ``local`` posture these services deliberately refuse an
    unauthenticated non-loopback peer, so a client call here would exercise that refusal instead
    of this contract, and the refusal already has its own tests. What must not rot is that the
    two fields exist on the response the endpoint returns.
    """
    from loan_doc_intel.api.schemas import HealthResponse

    fields = set(HealthResponse.model_fields)
    assert "runtime" in fields, "the console reads runtime off /healthz and the field is absent"
    assert "generator_model" in fields


def test_the_endpoint_answers_from_settings_rather_than_a_literal() -> None:
    """A pill value hard-coded at the endpoint would be right once and wrong after the next rebind.

    Both halves are properties of :class:`Settings`, so the values the endpoint sends are the
    values the profile implies; this pins that they are readable and non-empty together, which
    is what the endpoint relies on.
    """
    settings = Settings.load(CONFIG_PATH)
    assert settings.runtime in {"gcp", "local"}
    assert settings.generator_model.strip()


def test_the_managed_profile_names_a_model_or_says_exactly_why_not() -> None:
    """No placeholder survives here: every answer is a model id or a stated reason.

    ``managed-model-unnamed`` used to be a real answer in twenty-five trees, and it was the
    resolver looking in the wrong place rather than the trees being silent -- most of the fleet
    pins the id in settings under a per-repository field name. It is kept only as a defensive
    fallback and no tree should reach it.
    """
    answer = _for_profile("gcp").generator_model
    assert answer != "managed-model-unnamed", (
        "the managed model id is not being resolved from anywhere: set _GENERATOR_MODEL_ATTR "
        "to the settings path holding it, or declare _MODEL on the bound adapter"
    )
    assert answer.strip()


def test_not_implemented_is_claimed_only_by_an_adapter_that_never_calls_a_model() -> None:
    """The one answer that is INFERRED rather than read, so it is the one that can be wrong.

    ``managed-not-implemented`` is reached when a tree names no settings path and its adapter
    declares no model constant. That is correct for a deployment-wired placeholder, and a LIE
    for an adapter that generates while declaring nothing.

    The check is "does it call the model API", not "does it raise". Raising was tried first and
    is too weak: it passed `soc-fraud-fusion`, which generates and also raises on bad input, and
    it had already let a real mis-classification through -- `conversation-qa-scorecard` calls
    ``generate_content`` and raises only when its model is unconfigured, and was grouped with
    the placeholders on the strength of that raise. Its model is named now.
    """
    from importlib import import_module
    from pathlib import Path as _Path

    # The MANAGED profile, not whatever the settings file defaults to. Reading the default
    # profile here made this test inert: offline it answers `deterministic-offline-stub`, so it
    # returned before checking anything, and it passed a deliberately broken tree.
    settings = _for_profile("gcp")
    if settings.generator_model != "managed-not-implemented":
        return
    from loan_doc_intel.config import _GENERATOR_PORT

    binding = str((settings.adapters.get(_GENERATOR_PORT) or {}).get("gcp", ""))
    module = import_module(binding.partition(":")[0])
    source = _Path(module.__file__ or "").read_text()
    for call in ("generate_content", ".predict(", ".invoke("):
        assert call not in source, (
            f"{binding} reports managed-not-implemented but calls {call!r}: it generates, so "
            "the model it calls must be named rather than declared absent"
        )


def test_the_pills_call_the_base_this_console_actually_serves() -> None:
    """The half of the contract that lives in the BROWSER, and the half that was once broken.

    The banner the pills replaced fetched ``/api/agent/healthz``, the same-origin route handler
    the service template ships. This console does not ship one; it calls its backend directly on
    ``NEXT_PUBLIC_API_BASE``. So the health call 404'd, took the failure branch, and the failure
    branch renders nothing, deliberately, because a pill that guessed would assert provenance it
    does not have. A check that cannot fail loudly fails as an ABSENCE, and an absent element is
    exactly what no reviewer notices.

    Both architectures are legitimate, so this pins AGREEMENT rather than a literal: a tree with
    ``ui/app/api/agent`` proxies through its own origin and the pills should name that path; a
    tree without one must read the same base the rest of its console reads, for ``/healthz`` and
    for the answer headers alike.
    """
    pills = Path("ui/app/ModelPills.tsx").read_text()
    proxies_through_own_origin = Path("ui/app/api/agent").is_dir()

    assert ('"/api/agent"' in pills) == proxies_through_own_origin, (
        "the pills name /api/agent but this console has no route handler at "
        "ui/app/api/agent, so the health call reaches nothing and the pills render nothing"
        if not proxies_through_own_origin
        else "this console ships a /api/agent route handler but the pills do not use it"
    )

    if not proxies_through_own_origin:
        assert "${API_BASE}/healthz" in pills, (
            "the pills must resolve /healthz the way the rest of the console does, through the "
            "NEXT_PUBLIC_API_BASE reader in ui/lib/api.ts"
        )
        assert "watchAnswers(window, API_BASE" in pills, (
            "the answer headers must be read off responses from that same base, or a standalone "
            "run's pill stays on the configured model forever"
        )


def test_the_pills_sit_where_a_reader_can_see_them_without_covering_the_console() -> None:
    """A pill that renders off-screen, or over a control, has satisfied every other assertion.

    The banner this replaced once rendered 32px ABOVE the viewport in this very console: a margin
    written for a padded ``body`` was carried into one with none. So the geometry is asserted,
    not assumed: the pills are fixed at the top right with a non-negative offset, and the
    embedded layout (no header of its own) leaves them a clear top strip taller than they are.
    """
    import re as _re

    css = Path("ui/app/globals.css").read_text()
    start = css.index(".model-pills {")
    block = css[start : css.index("}", start)]
    assert "position: fixed;" in block
    top = _re.search(r"^\s*top:\s*(\d+)px;", block, _re.MULTILINE)
    assert top is not None and int(top.group(1)) >= 0, "the pills sit above the viewport"
    assert _re.search(r"^\s*right:\s*\d+px;", block, _re.MULTILINE), "not pinned to the right"
    assert ".provenance-banner" not in css, "the old banner's rule is back"

    layout = Path("ui/app/layout.tsx").read_text()
    assert "<ModelPills />" in layout, "the pills are not mounted on every page"
    assert 'className="px-4 pb-4 pt-8"' in layout, (
        "the embedded layout lost the top strip the fixed pills sit in, so they now cover the "
        "embedded page's first row"
    )


def test_the_pills_read_both_answer_headers_through_one_node_tested_wrapper() -> None:
    """The pill names what ANSWERED only if something reads the two headers the service emits.

    ``ui/tests/answer-provenance.test.mjs`` proves the wrapper's behaviour under ``npm test``;
    this holds the wiring from the offline gate, so a pill that stopped reading a header, or an
    old banner that came back beside it, fails here rather than in a screenshot.
    """
    pills = Path("ui/app/ModelPills.tsx").read_text()
    assert "generator_model" in pills and "runtime" in pills
    watcher = Path("ui/lib/answer-provenance.mjs").read_text()
    for header in ('"x-answered-by"', '"x-search-used"'):
        assert header in watcher, "the pills never read " + header
    assert Path("ui/tests/answer-provenance.test.mjs").is_file()
    assert not Path("ui/app/ProvenanceBanner.tsx").exists(), "the old banner is back"
