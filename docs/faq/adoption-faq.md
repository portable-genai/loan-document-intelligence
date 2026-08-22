# Adoption FAQ

For an engineering lead forking this repo (Doc5, Loan / Mortgage Document Intelligence) as
their institution's base. The step-by-step is [`docs/ADOPTING.md`](../ADOPTING.md); this
answers the "will it hurt later?" questions.

### How do I rebrand it for my institution?

`scripts/rename_fork.py` rewrites the package name (`loan_doc_intel`), the CLI entry point
(`loan-document-intelligence`), the `LOAN_DOC_` env prefix, and the resource / distribution id in one
pass (preview with `--dry-run`, apply with `--yes`). In this repo the distribution name, the
CLI name and the resource-id stem are the same string (`loan-document-intelligence`), so `--dist`
defaults to `--resource` and a fork normally sets all three to one new value. Then recreate
the venv, `pip install -e ".[dev]"`, and run `make lint test eval`. The script does the
mechanical rename; the human decisions (region, IdP, PII pack, validation policy, fixtures,
eval golden set) are the checklist in `ADOPTING.md`.

### If several banks fork this, how does each take upstream fixes?

Track upstream via **git tags** (semver). The repo declares a **core-vs-adopter-owned boundary** (ADOPTING section 2): upstream
owns the ports (`ports/`), the contract tests (`tests/contract/`), the eval harness
mechanics and CI, and the hexagon wiring (`config.py` `Container`); you own
`config/settings.yaml` values, the local fixtures, `adapters/onprem/*`, UI theming, the eval
golden set, and `COMPLIANCE.md` jurisdiction rows. Rebase your adopter-owned changes onto
each release rather than merging `main` continuously, so conflicts stay in files you were
told to expect.

### How do I add a new outbound dependency (a new port)?

There is a fixed touch list, and the contract test fails loudly if you miss part of it
(`test_port_protocols_matches_settings_adapters`): define the `@runtime_checkable` Protocol
under `ports/`, re-export it from `ports/__init__.py`, implement one adapter per profile (at
least `local` and `onprem`), bind all of them in `config/settings.yaml`, add the port to the
parity map in the contract test, add a `cached_property` on the `Container`
(`config.py`), and wire it in `api/deps.py`. See [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
(The full "every file to touch" enumeration is being expanded, tracked as audit check G6.)

### How do I add a new sub-service or output panel?

A sub-service is pure domain: add `domain/<name>_service.py` (stdlib only), re-export it,
keep any bank-owned constants in config rather than hard-coding them, construct it in
`api/deps.py`, and unit-test it. For an output panel, the renderer
(`scripts/render_loan_doc_ui.py`) already renders attached artifacts; stable `data-*` panel
hooks for the demo walkthrough are a known gap (audit check F2), so add them as you go.

### How do I change the taxonomy (document types, verdict kinds)?

The vocabularies are `StrEnum`s (members ARE their wire values) and the engines are typed on
`str`, so you extend the vocabulary without editing engine code and serialized JSON stays the
enum strings. To replace the taxonomy wholesale for a different vertical, edit the enums in
`domain/models.py` and the label maps in the UI.

### How do I retune the validation policy without touching code?

Honest answer: **not fully yet.** A frozen `ValidationSettings` (amount tolerance,
affordability warn / fail ratios) exists in `config.py` and `config/settings.yaml`, but the
`CrossValidator` is currently constructed without it and reads module-level constants
(`_AMOUNT_TOLERANCE`, `_AFFORDABILITY_WARN_RATIO`, `_AFFORDABILITY_FAIL_RATIO`, the
balance-decline thresholds). Threading config into the engine (a `from_settings` /
`from_policy` path) is tracked as audit check **B4 (PARTIAL)** in
[`docs/practices-audit.md`](../practices-audit.md). Until it lands, a fork tunes those
thresholds in `domain/cross_validator.py`; treat the defaults as reference numbers your
compliance function must own.

### Will the demo rot after I diverge?

Partly guarded today. `make demo` runs the offline batch render in one command, but there is
**no CI demo self-test** and no stable `data-*` panel hooks yet, so a refactor can drift the
demo without failing a build (audit checks **F1 / F2**). If you rely on the demo for
stakeholder reviews, wire a self-test into `.github/workflows/ci.yaml` as an early fork step.

### Does the CI run for my fork out of the box?

Yes. CI and the eval gate run on the `local` profile with **no cloud credentials and no org
secrets** (audit check D3), so a fork's build is green immediately; you add secrets only when
you wire the `gcp` / `platform` profiles. Note the eval gate measures the *reference* vertical
until you rebuild the golden set (`eval/datasets/`), which is an explicit adoption step, not
a silent pass.
