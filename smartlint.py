#!/usr/bin/env python3
"""
README-aligned linter: static checks for naming, coupling, cohesion,
namespace pollution, and undocumented assumptions.

Usage:
    python smartlint.py [PATH ...]     Run checks on files or directories (default: .)
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

from checks import Issue, check_file


def iter_py_files(paths: list[Path]) -> list[Path]:
    """Collect `.py` files from given files or directories, skipping common junk paths."""
    files: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_file() and p.suffix == ".py":
            files.append(p)
        elif p.is_dir():
            for f in p.rglob("*.py"):
                if any(part in {"__pycache__", ".venv", "venv", ".git"} for part in f.parts):
                    continue
                files.append(f)
        else:
            print(f"Warning: skip missing path: {p}", file=sys.stderr)
    return sorted(set(files))


def format_issues(issues: list[Issue]) -> str:
    """Render *issues* as grouped plain text for stdout."""
    if not issues:
        return "No issues reported.\n"
    by_axis: dict[str, list[Issue]] = {}
    for i in issues:
        by_axis.setdefault(i.axis, []).append(i)
    lines = ["README-aligned report (static checks)", "=" * 60]
    for axis in sorted(by_axis.keys()):
        lines.append(f"\n[{axis.upper()}]")
        for i in by_axis[axis]:
            lines.append(f"  {i.path}:{i.line}:{i.column}  {i.code}  {i.message}")
    lines.append("")
    lines.append(f"Total: {len(issues)} issue(s)")
    return "\n".join(lines)


def run_checks(paths: list[Path]):
    """Run static checks on *paths*. Print report and return 0 if clean, 1 if issues, 2 on I/O error."""
    all_issues: list[Issue] = []
    for f in iter_py_files(paths):
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"Error reading {f}: {e}", file=sys.stderr)
            return 2
        all_issues.extend(check_file(f, src))
    print(format_issues(all_issues), end="")


def ask_copilot(prompt: str, timeout: int = 60) -> str:
    """Run `gh copilot explain` and return its text output."""
    try:
        result = subprocess.run(
            ["gh", "copilot", "explain", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "GitHub CLI (`gh`) not found. Install it from https://cli.github.com and run `gh auth login`."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Copilot timed out after {timeout}s.")

    if result.returncode != 0:
        raise RuntimeError(
            f"gh copilot returned exit code {result.returncode}.\nstderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def build_prompt(variables: list[str], context_snippet: str, file_name: str) -> str:
    """Build a JSON-oriented prompt for Copilot descriptions."""
    var_list = ", ".join(f'"{v}"' for v in variables)
    return (
        f"I am analyzing the file '{file_name}'. "
        f"Here is a short excerpt of the code:\n\n"
        f"{context_snippet}\n\n"
        f"For each of the following scoped variable names found in this file, "
        f"write a 1-2 sentence description of its role. "
        f"Respond ONLY with a JSON object mapping each name to its description. "
        f"Names: [{var_list}]"
    )


def parse_copilot_json(response: str) -> dict[str, str]:
    """Extract a JSON object from Copilot's response if possible."""
    cleaned = response
    for fence in ["```json", "```"]:
        cleaned = cleaned.replace(fence, "")
    cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        return {}

    try:
        return json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return {}


def _normalized_key(name: str) -> str:
    """Normalize a Copilot-returned key for comparison against scoped names."""
    return name.strip().strip('"').strip("'").lower()


def describe_variables_with_copilot(file_path: Path, variables: list[str], source: str, batch_size: int = 10) -> dict[str, str]:
    """Ask Copilot to describe *variables* using the file context in *source*."""
    try:
        lines = source.splitlines()
        snippet = "\n".join(lines[:60])
    except OSError:
        snippet = ""

    records: dict[str, str] = {}
    for i in range(0, len(variables), batch_size):
        batch = variables[i : i + batch_size]
        prompt = build_prompt(batch, snippet, str(file_path))
        try:
            response = ask_copilot(prompt)
            descriptions = parse_copilot_json(response)
        except RuntimeError as exc:
            print(f"  [error] Copilot call failed for {file_path}: {exc}", file=sys.stderr)
            descriptions = {}

        for var in batch:
            normalized_var = _normalized_key(var)
            description = descriptions.get(var)
            if description is None:
                for key, value in descriptions.items():
                    if _normalized_key(key) == normalized_var or _normalized_key(key).endswith(f".{normalized_var}"):
                        description = value
                        break
            records[var] = description or "(Copilot did not provide a description)"

    return records


def _collect_names(target: ast.AST) -> list[str]:
    """Return every simple name contained in an assignment-like target."""
    names: list[str] = []
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            names.append(node.id)
    return names


def extract_scoped_variables(file_path: Path, source: str) -> list[str]:
    """Return scoped variable names discovered in *source*."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    scoped_vars: set[str] = set()
    scope_stack: list[str] = [file_path.stem]

    def scoped_name(name: str) -> str:
        return ".".join(scope_stack + [name])

    def add_name(name: str) -> None:
        scoped_vars.add(scoped_name(name))

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._scope_depth = 0

        def _enter_scope(self, name: str) -> None:
            scope_stack.append(name)
            self._scope_depth += 1

        def _leave_scope(self) -> None:
            scope_stack.pop()
            self._scope_depth -= 1

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._enter_scope(node.name)
            for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                add_name(arg.arg)
            if node.args.vararg:
                add_name(node.args.vararg.arg)
            if node.args.kwarg:
                add_name(node.args.kwarg.arg)
            self.generic_visit(node)
            self._leave_scope()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._enter_scope(node.name)
            self.generic_visit(node)
            self._leave_scope()

        def visit_Assign(self, node: ast.Assign) -> None:
            if len(scope_stack) > 3:
                return
            for target in node.targets:
                for name in _collect_names(target):
                    add_name(name)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if len(scope_stack) > 3:
                return
            for name in _collect_names(node.target):
                add_name(name)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            if len(scope_stack) > 3:
                return
            for name in _collect_names(node.target):
                add_name(name)

        def visit_For(self, node: ast.For) -> None:
            if len(scope_stack) > 3:
                return
            for name in _collect_names(node.target):
                add_name(name)

        def visit_With(self, node: ast.With) -> None:
            if len(scope_stack) > 3:
                return
            for item in node.items:
                if item.optional_vars:
                    for name in _collect_names(item.optional_vars):
                        add_name(name)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if len(scope_stack) > 3:
                return
            if node.name:
                add_name(node.name)

    Visitor().visit(tree)
    return sorted(scoped_vars)


def main():
    """Parse CLI args and run semantic checks."""
    parser = argparse.ArgumentParser(
        description="Semantic checks aligned with README axes.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[Path(".")],
        type=Path,
        help="Files or directories to check (default: current directory)",
    )
    args = parser.parse_args()


    print("Running preliminary static checks...")
    run_checks(list(args.paths))

    print("Getting variable names and summaries...")
    vars: dict[str, str] = {}
    for file_path in iter_py_files(list(args.paths)):
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"Warning: skip {file_path}: {exc}", file=sys.stderr)
            continue
        scoped_variables = extract_scoped_variables(file_path, source)
        if not scoped_variables:
            continue
        vars.update(describe_variables_with_copilot(file_path, scoped_variables, source))

    print(f"Collected {len(vars)} scoped variable(s).")
    print(vars)


if __name__ == "__main__":
    main()
