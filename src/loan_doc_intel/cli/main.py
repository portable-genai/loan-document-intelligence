"""``loan-document-intelligence`` : the Typer CLI for the B5 Loan Document Intelligence service.

This is a thin presentation layer over the domain service. It owns no business logic:
every command builds the wiring from :func:`loan_doc_intel.config.build_container` and the
factory functions in :mod:`loan_doc_intel.api.deps`, invokes the service, and pretty-prints
the cited result.

Design constraints honoured here:

* **Import-safe.** Importing this module (e.g. the ``[project.scripts]`` entry point, or
  ``--help``) must never pull in FastAPI, uvicorn, the Google Cloud SDKs, or even the
  domain service. All of those are imported *lazily inside command bodies*, so the
  on-prem/test profile : which installs no Google Cloud SDK : can still load the CLI.
* **Profile-aware.** ``LOAN_DOC_PROFILE`` selects the adapter stack. The ``onprem`` profile
  binds placeholder adapters that raise ``NotImplementedError``; when a command trips one,
  the CLI fails clearly (exit code 2) with a message that names the migration target.
* **Citations are first-class.** Every verified figure and check is printed with its source
  document and field, because a verification an underwriter cannot trace is worthless.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime top level
    from ..config import Container
    from ..domain.models import (
        CrossValidationResult,
        DocumentExtract,
        LoanApplicationCase,
    )

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "B5 Loan / Mortgage Document Intelligence : extract income/bank data from an "
        "applicant's documents and deterministically cross-validate it into a cited "
        "income verification, on the Gemini Enterprise Agent Platform (region "
        "asia-southeast1). Decision-support for underwriting, not a lending decision."
    ),
)

_PROFILE_EXIT = 2
_RUNTIME_EXIT = 1

_CLI_ACTOR = "cli:underwriter"
# The offline CLI runs as a demo-bank underwriter; object-level authorization (C2) still
# applies inside the service (it only proceeds for objects demo-bank owns, e.g. the bundled
# examples/application.json). Imports stay lazy to keep this module import-safe.
_CLI_TENANT = "demo-bank"
_CLI_PRINCIPALS = ("group:loan-analyst", "group:underwriting")


# --------------------------------------------------------------------------- #
# Wiring helpers (all imports lazy, so this module stays import-safe)
# --------------------------------------------------------------------------- #
def _container() -> Container:
    from ..config import build_container

    return build_container()


def _cli_principal() -> Any:
    """The verified-equivalent Principal the offline CLI acts as (demo-bank underwriter)."""
    from ..domain.identity import Principal

    return Principal(
        subject=_CLI_ACTOR,
        principals=_CLI_PRINCIPALS,
        tenant=_CLI_TENANT,
        source="cli",
    )


def _deps() -> Any:
    try:
        from ..api import deps  # type: ignore[attr-defined]
    except ImportError as exc:  # pragma: no cover - defensive wiring guard
        _fail(
            f"Service factories (loan_doc_intel.api.deps) are unavailable: {exc}",
            code=_RUNTIME_EXIT,
        )
    return deps


def _fail(message: str, *, code: int = _RUNTIME_EXIT) -> Any:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _run(action: str, fn: Any) -> Any:
    """Execute ``fn`` and translate adapter failures into clean CLI errors."""
    from ..config import Settings

    profile = Settings.load().profile
    try:
        return fn()
    except NotImplementedError as exc:
        detail = str(exc) or "method not implemented"
        _fail(
            f"'{action}' is not available under profile '{profile}'. "
            f"This profile uses placeholder adapters (on-prem migration target): {detail}",
            code=_PROFILE_EXIT,
        )
    except KeyError as exc:
        _fail(
            f"'{action}' has no adapter wired for profile '{profile}': {exc}",
            code=_PROFILE_EXIT,
        )
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary: no tracebacks to operators
        _fail(f"'{action}' failed: {type(exc).__name__}: {exc}", code=_RUNTIME_EXIT)


# --------------------------------------------------------------------------- #
# Pretty-printing
# --------------------------------------------------------------------------- #
def _fmt_citation(c: Any) -> str:
    page = f" p.{c.page}" if c.page is not None else ""
    field = f" .{c.field}" if c.field else ""
    return f"[{c.source_id}{field}{page}] {c.title}"


def _echo_review_banner(requires_review: bool) -> None:
    if requires_review:
        typer.secho(
            "  [HUMAN REVIEW REQUIRED] maker-checker gate (P-06) : the underwriter decides; "
            "this agent verifies, it does not approve.",
            fg=typer.colors.YELLOW,
            bold=True,
        )


def _print_checks(result: CrossValidationResult, indent: str = "  ") -> None:
    color = {
        "pass": typer.colors.GREEN,
        "warn": typer.colors.YELLOW,
        "fail": typer.colors.RED,
    }
    for check in result.checks:
        status = check.status.value
        styled = typer.style(status.upper(), fg=color.get(status, typer.colors.WHITE), bold=True)
        typer.echo(f"{indent}[{styled}] {check.kind.value} ({check.severity.value})")
        typer.echo(f"{indent}    expected: {check.expected}")
        typer.echo(f"{indent}    observed: {check.observed}")
        for c in check.citations:
            typer.secho(f"{indent}    evidence: {_fmt_citation(c)}", fg=typer.colors.BRIGHT_BLACK)


def _print_case(case: LoanApplicationCase) -> None:
    typer.secho(f"Loan application case : {case.id}", bold=True, fg=typer.colors.GREEN)
    _echo_review_banner(case.requires_human_review)
    if case.income is not None:
        income = case.income
        typer.secho(f"  Verdict: {income.verdict.value.upper()}", bold=True)
        vi = income.verified_income
        typer.echo(f"  Verified income: {vi.amount} {vi.currency} per {vi.period.value}")
        if income.stability:
            typer.echo(f"  Stability: {income.stability}")
        for flag in income.red_flags:
            typer.secho(f"  red flag: {flag}", fg=typer.colors.RED)
    if case.validation is not None:
        typer.secho("  Cross-validation checks:", bold=True)
        _print_checks(case.validation, indent="    ")


def _print_extract(extract: DocumentExtract) -> None:
    typer.secho(f"Document extract : {extract.document_id}", bold=True, fg=typer.colors.GREEN)
    typer.echo(f"  doc_type: {extract.doc_type.value}  confidence: {extract.confidence:.2f}")
    for k, v in extract.fields.items():
        typer.echo(f"    {k}: {v}")
    for item in extract.line_items:
        dated = f" on {item.date}" if item.date else ""
        typer.echo(f"    line: {item.label} {item.amount} {item.currency}{dated}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def process(
    application_file: str = typer.Argument(
        ..., help="Path to a JSON file: {application: {...}, documents: [...]}."
    ),
) -> None:
    """Process an application's documents into a cited income verification."""
    from ..api.schemas import ProcessRequest

    def _do() -> LoanApplicationCase:
        with open(application_file) as fh:
            payload = json.load(fh)
        request = ProcessRequest(**payload)
        applicant = request.application.to_domain()
        documents = [d.to_domain() for d in request.documents]
        svc = _deps().build_loan_doc_service(_container())
        return svc.process(applicant, documents, _cli_principal())

    case = _run("process", _do)
    _print_case(case)


@app.command()
def extract(
    document_id: str = typer.Argument(..., help="The document id."),
    doc_type: str = typer.Option("payslip", "--type", "-t", help="Document type."),
    uri: str = typer.Option("", "--uri", help="Storage URI of the document."),
) -> None:
    """Extract structured fields from a single applicant document."""
    from ..domain.models import ApplicantDocument, DocType

    def _do() -> DocumentExtract:
        document = ApplicantDocument(id=document_id, doc_type=DocType(doc_type), uri=uri)
        svc = _deps().build_loan_doc_service(_container())
        return svc.extract_only(document, b"", "application/pdf", _cli_principal())

    extract_result = _run("extract", _do)
    _print_extract(extract_result)


@app.command()
def validate(
    application_file: str = typer.Argument(
        ..., help="Path to a JSON file: {application_id, applicant, extracts: [...]}."
    ),
) -> None:
    """Run the deterministic cross-validation over supplied extracts (no model)."""
    from ..api.deps import build_cross_validator
    from ..api.schemas import ValidateRequest
    from ..config import Settings
    from ..domain.models import Citation, DocType, DocumentExtract, LineItem, SourceType

    def _do() -> CrossValidationResult:
        with open(application_file) as fh:
            payload = json.load(fh)
        request = ValidateRequest(**payload)
        applicant = request.applicant.to_domain()
        extracts = [
            DocumentExtract(
                document_id=e.document_id,
                doc_type=DocType(e.doc_type),
                fields=dict(e.fields),
                line_items=tuple(
                    LineItem(label=li.label, amount=li.amount, currency=li.currency, date=li.date)
                    for li in e.line_items
                ),
                period=e.period,
                confidence=e.confidence,
                citations=tuple(
                    Citation(
                        source_id=c.source_id,
                        source_type=SourceType(c.source_type),
                        title=c.title,
                        page=c.page,
                        field=c.field,
                        snippet=c.snippet,
                    )
                    for c in e.citations
                ),
            )
            for e in request.extracts
        ]
        return build_cross_validator(Settings.load()).validate(
            extracts, applicant, application_id=request.application_id
        )

    result = _run("validate", _do)
    typer.secho(
        f"Cross-validation : {result.application_id} (passed={result.passed})",
        bold=True,
        fg=typer.colors.GREEN,
    )
    _echo_review_banner(result.requires_human_review)
    _print_checks(result)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address for the API server."),
    port: int = typer.Option(8092, help="TCP port for the API server."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev only)."),
) -> None:
    """Run the FastAPI app (A2A card, MCP, and REST endpoints) under uvicorn."""

    def _do() -> None:
        import uvicorn

        deps = _deps()
        if not hasattr(deps, "create_app"):
            _fail(
                "loan_doc_intel.api.deps does not expose create_app(); cannot start the server.",
                code=_RUNTIME_EXIT,
            )
        typer.secho(
            f"serving loan-document-intelligence on http://{host}:{port} "
            f"(profile={_profile_label()})",
            fg=typer.colors.GREEN,
        )
        uvicorn.run(
            "loan_doc_intel.api.deps:create_app",
            factory=True,
            host=host,
            port=port,
            reload=reload,
        )

    _run("serve", _do)


@app.command()
def eval(  # noqa: A001 - "eval" is the documented command name in SPEC §3
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Path to the golden eval dataset (defaults to the gate's bundled set).",
    ),
) -> None:
    """Run the A4 promotion eval gate (extraction / validation recall+precision / PII safety)."""

    def _do() -> int:
        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        script = repo_root / "eval" / "run_eval.py"
        if not script.exists():
            _fail(f"eval gate script not found at {script}", code=_RUNTIME_EXIT)
        cmd = [sys.executable, str(script)]
        if dataset:
            cmd += ["--dataset", dataset]
        typer.secho(f"running eval gate: {script}", fg=typer.colors.CYAN)
        completed = subprocess.run(cmd, check=False)  # noqa: S603 - trusted local script
        return completed.returncode

    code = _run("eval", _do)
    if code == 0:
        typer.secho("eval gate: PASS", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho("eval gate: FAIL", fg=typer.colors.RED, bold=True)
    raise typer.Exit(int(code))


@app.command(name="agent-card")
def agent_card() -> None:
    """Print this service's A2A AgentCard (the /.well-known/agent-card.json body)."""

    def _do() -> dict[str, Any]:
        from ..agent.agent_card import agent_card_document
        from ..config import Settings

        return agent_card_document(Settings.load())

    card = _run("agent-card", _do)
    typer.echo(json.dumps(card, indent=2))


def _profile_label() -> str:
    from ..config import Settings

    return Settings.load().profile


if __name__ == "__main__":  # pragma: no cover
    app()
