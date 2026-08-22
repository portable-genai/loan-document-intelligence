"""A7: the kernel/vertical split is a real dependency direction, not a label.

The check that matters is not "a module named ``kernel`` exists" -- the repo had that
while ``kernel`` was a re-export shim over the mixed ``models`` module, which is exactly
the defect this file pins. What matters is that the arrow points one way: a fork must be
able to import the vertical-neutral kernel **without** dragging in the loan / applicant /
income artifacts it is going to rewrite.

So the primary assertion is executed, not read: a fresh interpreter imports
``loan_doc_intel.domain.kernel`` and reports whether ``loan_doc_intel.domain.models``
ended up in ``sys.modules``. Against the previous shim that subprocess printed the
vertical module, so this test was RED before the split.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from loan_doc_intel.domain import kernel, models

SRC = Path(__file__).resolve().parents[2] / "src"
KERNEL_PATH = SRC / "loan_doc_intel" / "domain" / "kernel.py"

# The vertical-neutral machinery A7 requires a fork to inherit untouched.
KERNEL_NAMES = (
    "AgentCard",
    "AgentSkill",
    "AuditEvent",
    "Citation",
    "Decision",
    "Direction",
    "EvalMetricResult",
    "EvalReport",
    "GuardrailCategory",
    "GuardrailFinding",
    "GuardrailVerdict",
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "RedactionFinding",
    "RedactionResult",
    "Severity",
    "SourceType",
    "StrEnum",
    "ThinkingLevel",
    "TokenUsage",
    "ToolSpec",
    "utcnow",
)

# The loan-underwriting artifacts a fork rewrites. None may live in the kernel.
VERTICAL_NAMES = (
    "Applicant",
    "ApplicantDocument",
    "CheckKind",
    "CheckStatus",
    "CrossValidationCheck",
    "CrossValidationResult",
    "DocType",
    "DocumentExtract",
    "IncomeFigure",
    "IncomeKind",
    "IncomePeriod",
    "IncomeVerificationSummary",
    "LineItem",
    "LoanApplicationCase",
    "VerificationVerdict",
)


def _import_probe(module: str) -> dict[str, object]:
    """Import ``module`` in a FRESH interpreter and report what it pulled in."""
    program = (
        "import json, sys\n"
        f"import {module}\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('loan_doc_intel'))))\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=str(SRC),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return {"modules": json.loads(completed.stdout.strip().splitlines()[-1])}


def test_importing_the_kernel_does_not_import_the_vertical_models() -> None:
    """Executed proof of the dependency direction, in a process of its own."""
    imported = _import_probe("loan_doc_intel.domain.kernel")["modules"]
    assert "loan_doc_intel.domain.kernel" in imported
    assert "loan_doc_intel.domain.models" not in imported, (
        "the kernel pulled the vertical model module in; the split is a label, not a "
        f"boundary (imported: {imported})"
    )


def test_the_vertical_models_do_import_the_kernel() -> None:
    """The arrow must exist in the other direction, or nothing is shared."""
    imported = _import_probe("loan_doc_intel.domain.models")["modules"]
    assert "loan_doc_intel.domain.kernel" in imported


def test_kernel_source_has_no_intra_package_imports() -> None:
    """Static backstop: the kernel depends on the stdlib and the commons only."""
    tree = ast.parse(KERNEL_PATH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"kernel makes a relative import of {node.module!r}"
            assert not (node.module or "").startswith("loan_doc_intel"), node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("loan_doc_intel"), alias.name


@pytest.mark.parametrize("name", KERNEL_NAMES)
def test_kernel_names_are_defined_in_the_kernel_and_re_exported(name: str) -> None:
    """Backward-compatible re-exports keep every existing import site working."""
    assert hasattr(kernel, name), f"{name} is not in the kernel"
    assert getattr(models, name) is getattr(kernel, name), (
        f"models.{name} is not the same object as kernel.{name}"
    )


@pytest.mark.parametrize("name", VERTICAL_NAMES)
def test_vertical_artifacts_stay_out_of_the_kernel(name: str) -> None:
    assert hasattr(models, name)
    assert not hasattr(kernel, name), f"{name} is vertical and must not sit in the kernel"
