"""
Static checks aligned with README axes: naming, coupling, cohesion,
namespace pollution, and undocumented assumptions.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Issue:
    """One finding from a static check (file location, README axis, rule id, human text)."""

    path: str
    line: int
    column: int
    axis: str
    code: str
    message: str


def _attr_chain_depth(node: ast.AST) -> int:
    """Depth of attribute/call nesting on the callee side of a call (Law-of-Demeter style)."""
    # Count how "far" you reach before the actual call target — stuff like
    # foo.bar().baz().qux() stacks up fast and usually means you're poking
    # through someone else's internals. Not always wrong, just a smell.
    if isinstance(node, ast.Attribute):
        return 1 + _attr_chain_depth(node.value)
    if isinstance(node, ast.Call):
        return _attr_chain_depth(node.func)
    if isinstance(node, ast.Subscript):
        return _attr_chain_depth(node.value)
    return 0


def _iter_calls(tree: ast.AST) -> Iterable[ast.Call]:
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            yield n


def _import_nodes(tree: ast.Module) -> list[ast.AST]:
    # Only module-level imports. Nested imports are not included unless we add that.
    out: list[ast.AST] = []
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            out.append(n)
    return out


def check_file(path: Path, source: str) -> list[Issue]:
    """Parse *source* as Python and return README-axis issues for *path* (used for display)."""
    rel = str(path)
    issues: list[Issue] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        issues.append(
            Issue(
                rel, e.lineno or 1, e.offset or 0, "namespace", "SYNTAX", str(e.args[0])
            )
        )
        return issues

    if not isinstance(tree, ast.Module):
        return issues

    # --- Namespace: wildcard imports, import volume ---
    # Star-imports dump names into the module namespace. Static tools and readers
    # cannot see where each name came from.
    for node in _import_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names):
                issues.append(
                    Issue(
                        rel,
                        node.lineno,
                        node.col_offset,
                        "namespace",
                        "WILDCARD_IMPORT",
                        "`from ... import *` widens the namespace and hides where names come from.",
                    )
                )
    n_imports = len(_import_nodes(tree))
    # Threshold is arbitrary: flag when a single module accumulates many direct
    # dependencies (splitting or lazy imports may help readability).
    if n_imports > 20:
        issues.append(
            Issue(
                rel,
                1,
                0,
                "namespace",
                "MANY_IMPORTS",
                f"This module has {n_imports} import statements. Consider splitting or lazy imports.",
            )
        )

    # --- Coupling: deep callee chains ---
    # Each `ast.Call` is evaluated separately, so one line can yield several issues.
    for call in _iter_calls(tree):
        depth = _attr_chain_depth(call.func)
        # Minimum depth before reporting. Lower values catch more fluent-style APIs.
        if depth >= 4:
            issues.append(
                Issue(
                    rel,
                    call.lineno,
                    call.col_offset,
                    "coupling",
                    "DEEP_CALLEE_CHAIN",
                    "Long chain of attribute access / nested calls on the callee may indicate tight coupling.",
                )
            )

    # --- Cohesion: very large classes ---
    # Uses method count only. It does not inspect field usage or class size in lines.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = sum(
                1
                for b in node.body
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            # Report classes with many direct methods (threshold is tunable).
            if methods > 15:
                issues.append(
                    Issue(
                        rel,
                        node.lineno,
                        node.col_offset,
                        "cohesion",
                        "LARGE_CLASS",
                        f"Class `{node.name}` defines {methods} methods. It may be doing too many jobs.",
                    )
                )

    # --- Naming: one-letter and ambiguous identifiers (module scope only) ---
    # Inner scopes are skipped: loop variables and comprehensions would dominate.
    ambiguous = frozenset({"l", "O", "I"})
    # Single-letter names allowed here (common in math or science code). Extend the set if needed.
    allowed_short = frozenset(
        {"_", "i", "j", "k", "n", "x", "y", "z", "t", "e", "f", "g", "h"}
    )
    for stmt in tree.body:
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.target:
            targets = [stmt.target]
        for t in targets:
            if isinstance(t, ast.Name):
                name = t.id
                if len(name) == 1 and name not in allowed_short:
                    issues.append(
                        Issue(
                            rel,
                            stmt.lineno,
                            stmt.col_offset,
                            "naming",
                            "SHORT_NAME",
                            f"Module-level name `{name}` is very short. Prefer a descriptive identifier.",
                        )
                    )
                # l/O/I are easy to confuse with 1/0/I depending on typeface.
                if name in ambiguous:
                    issues.append(
                        Issue(
                            rel,
                            stmt.lineno,
                            stmt.col_offset,
                            "naming",
                            "AMBIGUOUS_NAME",
                            f"`{name}` is easy to misread (similar glyphs). Choose a clearer name.",
                        )
                    )

    # --- Assumptions: bare except, missing docstrings on public API ---
    # `except:` catches BaseException. Usually you want specific exception types.
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(
                Issue(
                    rel,
                    node.lineno,
                    node.col_offset,
                    "assumptions",
                    "BARE_EXCEPT",
                    "Bare `except:` swallows all errors and hides failure modes. Catch specific exceptions.",
                )
            )

    # Top-level functions/classes without leading underscore: expect a short docstring
    # so callers see purpose and non-obvious preconditions.
    for stmt in tree.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if stmt.name.startswith("_"):
            continue
        if ast.get_docstring(stmt):
            continue
        # `def foo: pass` placeholders are ignored.
        if len(stmt.body) == 1 and isinstance(stmt.body[0], ast.Pass):
            continue
        kind = (
            "function"
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            else "class"
        )
        issues.append(
            Issue(
                rel,
                stmt.lineno,
                stmt.col_offset,
                "assumptions",
                "MISSING_DOCSTRING",
                f"Public {kind} `{stmt.name}` has no docstring. State invariants, preconditions, or purpose.",
            )
        )

    return issues
