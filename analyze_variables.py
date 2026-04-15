#!/usr/bin/env python3
"""
analyze_variables.py

Uses the GitHub Copilot CLI (`gh copilot explain`) to examine all source files
in the current directory (and subdirectories), extract every variable name via
Python's AST module, then ask Copilot to produce a 1-2 sentence summary of
each variable's role.

Requirements:
  - GitHub CLI installed and authenticated  (`gh auth login`)
  - GitHub Copilot extension installed      (`gh extension install github/gh-copilot`)
  - Python 3.8+

Usage:
  python analyze_variables.py [--root <dir>] [--output <file>] [--extensions .py .js ...]

  --root        Root directory to scan (default: current working directory)
  --output      Write the report to this file instead of stdout
  --extensions  File extensions to scan (default: .py)
  --batch       Number of variables to describe per Copilot call (default: 10)
  --verbose     Print progress messages to stderr
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Generator


# ---------------------------------------------------------------------------
# 1.  Discover source files
# ---------------------------------------------------------------------------

def iter_source_files(root: Path, extensions: list[str]) -> Generator[Path, None, None]:
    """Yield every file under *root* whose suffix is in *extensions*."""
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in extensions:
            yield path


# ---------------------------------------------------------------------------
# 2.  Extract variable names from Python source (AST-based)
# ---------------------------------------------------------------------------

def extract_python_variables(source_code: str) -> list[str]:
    """Return all unique variable names assigned in *source_code*."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []

    names: set[str] = set()

    for node in ast.walk(tree):
        # Simple assignments:  x = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for n in ast.walk(target):
                    if isinstance(n, ast.Name):
                        names.add(n.id)

        # Annotated assignments:  x: int = ...
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)

        # Augmented assignments:  x += ...
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)

        # For-loop variables:  for x in ...
        elif isinstance(node, ast.For):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    names.add(n.id)

        # Comprehension variables:  [x for x in ...]
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            generators = node.generators if hasattr(node, "generators") else []
            for gen in generators:
                for n in ast.walk(gen.target):
                    if isinstance(n, ast.Name):
                        names.add(n.id)

        # With statements:  with open(...) as f
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars:
                    for n in ast.walk(item.optional_vars):
                        if isinstance(n, ast.Name):
                            names.add(n.id)

        # Exception handlers:  except ValueError as e
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)

        # Function / class names count as "variables" in module scope
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)

    # Filter out Python builtins and common throw-away names
    builtins = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
    ignored = {"_", "__", "self", "cls"} | builtins
    return sorted(names - ignored)


# ---------------------------------------------------------------------------
# 3.  Generic variable extraction (grep-based fallback for non-Python files)
# ---------------------------------------------------------------------------

def extract_generic_variables(source_code: str, extension: str) -> list[str]:
    """
    Very lightweight regex-free heuristic for JS/TS/other files.
    Looks for common assignment patterns and returns unique identifiers.
    """
    import re
    patterns = [
        r'\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)',   # JS const
        r'\blet\s+([A-Za-z_$][A-Za-z0-9_$]*)',      # JS let
        r'\bvar\s+([A-Za-z_$][A-Za-z0-9_$]*)',      # JS var
        r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)', # JS function
        r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)',       # class name
        r'^([A-Za-z_][A-Za-z0-9_]*)\s*=',            # generic assignment
    ]
    names: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, source_code, re.MULTILINE):
            names.add(match.group(1))
    return sorted(names)


def extract_variables(file_path: Path) -> list[str]:
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    if file_path.suffix == ".py":
        return extract_python_variables(source)
    return extract_generic_variables(source, file_path.suffix)


# ---------------------------------------------------------------------------
# 4.  Call GitHub Copilot CLI
# ---------------------------------------------------------------------------

def ask_copilot(prompt: str, timeout: int = 60) -> str:
    """
    Run `gh copilot explain '<prompt>'` and return Copilot's response.
    Raises RuntimeError if the command fails.
    """
    try:
        result = subprocess.run(
            ["gh", "copilot", "explain", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "GitHub CLI (`gh`) not found. "
            "Install it from https://cli.github.com and run `gh auth login`."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Copilot timed out after {timeout}s.")

    if result.returncode != 0:
        raise RuntimeError(
            f"gh copilot returned exit code {result.returncode}.\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def build_prompt(variables: list[str], context_snippet: str, file_name: str) -> str:
    """
    Build a structured natural-language prompt for Copilot.
    Returns a prompt that asks for JSON output so it's easy to parse.
    """
    var_list = ", ".join(f'"{v}"' for v in variables)
    prompt = (
        f"I am analyzing the file '{file_name}'. "
        f"Here is a short excerpt of the code:\n\n"
        f"{context_snippet}\n\n"
        f"For each of the following variable/function/class names found in this file, "
        f"write a 1-2 sentence description of its role. "
        f"Respond ONLY with a JSON object mapping each name to its description. "
        f"Names: [{var_list}]"
    )
    return prompt


def parse_copilot_json(response: str) -> dict[str, str]:
    """Try to extract a JSON object from Copilot's response."""
    # Strip markdown fences if present
    cleaned = response
    for fence in ["```json", "```"]:
        cleaned = cleaned.replace(fence, "")
    cleaned = cleaned.strip()

    # Find the first { ... } block
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        return {}

    try:
        return json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# 5.  Main orchestration
# ---------------------------------------------------------------------------

def analyse_file(
    file_path: Path,
    root: Path,
    batch_size: int,
    verbose: bool,
) -> list[dict]:
    """
    Extract variables from a single file, ask Copilot in batches,
    and return a list of {file, variable, description} records.
    """
    rel_path = file_path.relative_to(root)
    variables = extract_variables(file_path)

    if not variables:
        if verbose:
            print(f"  [skip] {rel_path} — no variables found", file=sys.stderr)
        return []

    if verbose:
        print(f"  [scan] {rel_path} — {len(variables)} variable(s)", file=sys.stderr)

    # Build a short context snippet (first 60 lines) to help Copilot
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        snippet = "\n".join(lines[:60])
    except OSError:
        snippet = ""

    records: list[dict] = []

    # Process variables in batches to stay within CLI arg length limits
    for i in range(0, len(variables), batch_size):
        batch = variables[i : i + batch_size]
        prompt = build_prompt(batch, snippet, str(rel_path))

        try:
            response = ask_copilot(prompt)
            descriptions = parse_copilot_json(response)
        except RuntimeError as exc:
            print(f"  [error] Copilot call failed: {exc}", file=sys.stderr)
            descriptions = {}

        for var in batch:
            description = descriptions.get(var, "(Copilot did not provide a description)")
            records.append(
                {
                    "file": str(rel_path),
                    "variable": var,
                    "description": description,
                }
            )

    return records


def format_report(all_records: list[dict]) -> str:
    """Render records as a human-readable text report."""
    if not all_records:
        return "No variables found.\n"

    lines = ["=" * 72, "  VARIABLE ANALYSIS REPORT (powered by GitHub Copilot)", "=" * 72]

    current_file = None
    for rec in all_records:
        if rec["file"] != current_file:
            current_file = rec["file"]
            lines.append(f"\nFile: {current_file}\n" + "-" * 60)

        wrapped = textwrap.fill(
            rec["description"],
            width=68,
            initial_indent="    ",
            subsequent_indent="    ",
        )
        lines.append(f"  {rec['variable']}:\n{wrapped}")

    lines.append("\n" + "=" * 72)
    lines.append(f"Total variables described: {len(all_records)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse variable names in source files using GitHub Copilot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write report to this file (default: stdout)",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".py"],
        metavar="EXT",
        help="File extensions to scan, e.g. .py .js .ts (default: .py)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=10,
        help="Variables per Copilot request (default: 10)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress to stderr",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        sys.exit(f"Error: '{root}' is not a directory.")

    extensions = [e if e.startswith(".") else f".{e}" for e in args.extensions]

    if args.verbose:
        print(f"Scanning: {root}", file=sys.stderr)
        print(f"Extensions: {extensions}", file=sys.stderr)

    all_records: list[dict] = []

    for file_path in iter_source_files(root, extensions):
        records = analyse_file(file_path, root, args.batch, args.verbose)
        all_records.extend(records)

    report = format_report(all_records)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Report written to: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
