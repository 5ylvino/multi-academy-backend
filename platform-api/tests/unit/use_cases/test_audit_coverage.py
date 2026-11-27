"""Every use case that saves data also writes an audit entry, or says why it doesn't.

This guards the trail as new features land: a commit with no ``uow.audit(...)`` (or a helper
that calls it) fails here unless the function carries an ``# audit: not needed. <reason>`` note.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

USE_CASES = Path(__file__).resolve().parents[3] / "app" / "use_cases"
_AUDIT_HELPERS = {"audit", "_audit"}


def _calls(node: ast.AST) -> list[str]:
    names = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


def _committing_functions() -> list[tuple[str, ast.AsyncFunctionDef, str]]:
    found = []
    for path in sorted(USE_CASES.rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and "commit" in _calls(node):
                rel = path.relative_to(USE_CASES.parent.parent)
                found.append((f"{rel}:{node.lineno} {node.name}", node, source))
    return found


def test_use_cases_are_found() -> None:
    assert len(_committing_functions()) > 30


@pytest.mark.parametrize(
    ("where", "node", "source"),
    [pytest.param(*f, id=f[0]) for f in _committing_functions()],
)
def test_every_commit_is_audited(where: str, node: ast.AsyncFunctionDef, source: str) -> None:
    calls = set(_calls(node))
    body = ast.get_source_segment(source, node) or ""
    explained = "# audit: not needed." in body
    assert calls & _AUDIT_HELPERS or explained, (
        f"{where} commits without an audit entry. Add uow.audit(audit.by(...)) or, if the "
        "change is routine bookkeeping, a '# audit: not needed. <reason>' comment."
    )
