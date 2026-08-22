# B5 Loan / Mortgage Document Intelligence : developer Makefile.
#
# The default dev/test/lint targets run under the LOCAL profile : a WORKING offline stack
# (local document parser + deterministic LLM, SDK-free) that needs NO Google Cloud SDK.
# Override PROFILE=gcp for the managed stack, or PROFILE=onprem for the fail-fast target.

PYTHON      ?= python3
PYTHON      := $(if $(wildcard .venv/bin/python),.venv/bin/python,$(PYTHON))
PIP         ?= pip
PROFILE     ?= local
SRC         := src/loan_doc_intel
TESTS       := tests
API_APP     := loan_doc_intel.api.app:app
API_HOST    ?= 127.0.0.1  # no-auth local dev binds loopback; override deliberately
API_PORT    ?= 8092
UI_DIR      := ui
TF_DIR      := infra/terraform

export LOAN_DOC_PROFILE := $(PROFILE)

DEMO_OUT    := demo-out
.DEFAULT_GOAL := help
.PHONY: help install install-gcp fmt lint test eval check demo demo-selftest portability-demo \
        run-local run-api run-ui ui-install ui-check tf-plan clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install the package + dev tooling (NO GCP SDK : local/test profile).
	$(PIP) install -e ".[dev]"

install-gcp: ## Install with the managed-stack extra (google-adk, genai, documentai, ...).
	$(PIP) install -e ".[gcp,dev]"

fmt: ## Auto-format and auto-fix lint issues.
	ruff format $(SRC) $(TESTS) eval
	ruff check --fix $(SRC) $(TESTS) eval

lint: ## Lint (ruff check + format check) and type-check (mypy).
	ruff check $(SRC) $(TESTS) eval scripts/demo_selftest.py scripts/portability_demo.py \
		scripts/loan_doc_demo_playwright.py scripts/run_ui_demo.py
	ruff format --check $(SRC) $(TESTS) eval scripts/demo_selftest.py scripts/portability_demo.py \
		scripts/loan_doc_demo_playwright.py scripts/run_ui_demo.py
	mypy $(SRC)

.PHONY: demo-ui
demo-ui:
	LOAN_DOC_PROFILE=local PYTHONPATH=src python3 scripts/run_ui_demo.py

test: ## Run unit + contract tests on the local profile (no GCP SDK required).
	LOAN_DOC_PROFILE=local pytest -m 'not integration' -q

eval: ## Run the A4 eval gate (extraction / validation recall+precision / PII safety).
	$(PYTHON) eval/run_eval.py

portability: portability-demo ## Standard fleet alias for the executable portability proof.

check: lint test eval demo-selftest portability-demo ## Run the full offline quality gate.

ui-install: ## Install the console's locked dependencies (what CI does).
	npm ci --prefix $(UI_DIR)

ui-check: ## The console gate: types, policy unit tests, build, then HYDRATION against the build.
	npm --prefix $(UI_DIR) run lint
	npm --prefix $(UI_DIR) test
	NEXT_TELEMETRY_DISABLED=1 npm --prefix $(UI_DIR) run build
	# LAST, and against the artefact the build just made. Everything above this line passes
	# identically whether or not the served HTML carries the CSP nonce; only starting the built
	# server and reading its script tags can tell a hydrating console from dead markup.
	npm --prefix $(UI_DIR) run assert-hydratable

demo: ## Run the offline demo (LOCAL): write the audit-view JSON + static HTML into demo-out/.
	mkdir -p $(DEMO_OUT)
	LOAN_DOC_PROFILE=local PYTHONPATH=src $(PYTHON) scripts/loan_doc_demo.py $(DEMO_OUT)/loan_doc_demo.json
	PYTHONPATH=src $(PYTHON) scripts/render_loan_doc_ui.py $(DEMO_OUT)/loan_doc_demo.json $(DEMO_OUT)
	@echo "open $(DEMO_OUT)/loan-doc-summary.html"

demo-selftest: ## Prove the demo's visible states and stable evidence hooks cannot rot silently.
	LOAN_DOC_PROFILE=local PYTHONPATH=src:scripts $(PYTHON) scripts/demo_selftest.py

portability-demo: ## Execute bounded local/managed/onprem portability evidence.
	LOAN_DOC_PROFILE=local PYTHONPATH=src:scripts $(PYTHON) scripts/portability_demo.py

run-local: ## Process the bundled synthetic application offline (LOCAL profile, no GCP SDK).
	LOAN_DOC_PROFILE=local loan-document-intelligence process examples/application.json

run-api: ## Run the FastAPI service (PROFILE=$(PROFILE)).
	uvicorn $(API_APP) --host $(API_HOST) --port $(API_PORT) --reload

run-ui: ## Run the React / Next.js UI (dev server).
	cd $(UI_DIR) && npm install && npm run dev

tf-plan: ## Terraform plan for the asia-southeast1 infrastructure.
	cd $(TF_DIR) && terraform init -input=false && terraform plan

clean: ## Remove caches and build artefacts.
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
