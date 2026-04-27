#!/usr/bin/env python3
"""
smartlint.py

Format a Python project with Black, discover variable-like names with AST,
ask GitHub Copilot CLI about each name, and print a map from name to
purpose/usage.

Usage:
  python smartlint.py <project_path> [--verbose] [--timeout 60]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
import ast

from checks import Issue, check_file

IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "venv",
}


def _import_nodes(tree: ast.Module) -> list[ast.AST]:
    # Only module-level imports. Nested imports are not included unless we add that.
    out: list[ast.AST] = []
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            out.append(n)
    return out


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
        elif isinstance(
            node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
        ):
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
    builtins = (
        set(dir(__builtins__))
        if isinstance(__builtins__, dict)
        else set(dir(__builtins__))
    )
    ignored = {"_", "__", "self", "cls"} | builtins
    return sorted(names - ignored)


def iter_python_files(root: Path):
    """Yield Python files under *root*, skipping common virtualenv/cache folders."""
    for path in root.rglob("*.py"):
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            yield path


def run_black(project_root: Path) -> None:
    """Run Black on *project_root* and fail fast if formatting fails."""
    try:
        result = subprocess.run(
            ["black", str(project_root)],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Black is not installed or not available on PATH. Install it with `pip install black`."
        ) from exc

    if result.returncode != 0:
        stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            "Black failed while formatting the project.\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )


def discover_project_variables(root: Path) -> dict[str, set[Path]]:
    """Return each variable-like name and the files in which it appears."""
    variables: dict[str, set[Path]] = defaultdict(set)

    for file_path in iter_python_files(root):
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for name in extract_python_variables(source):
            variables[name].add(file_path)

    return variables


def build_context_snippet(
    file_path: Path, variable_name: str, max_hits: int = 3
) -> str:
    """Collect a few short code snippets showing where *variable_name* appears."""
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""

    pattern = re.compile(rf"\b{re.escape(variable_name)}\b")
    snippets: list[str] = []

    for index, line in enumerate(lines):
        if not pattern.search(line):
            continue

        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        block = "\n".join(lines[start:end]).strip()
        snippets.append(f"{file_path.name}:{index + 1}\n{block}")

        if len(snippets) >= max_hits:
            break

    return "\n\n".join(snippets)


def build_prompt(variable_name: str, files: set[Path], root: Path) -> str:
    """Build a prompt that asks Copilot for the variable's purpose and usage."""

    return (
        f"This is a Python project. Find the variable named '{variable_name}'."
        f"Describe the variable's purpose and how it is used in the project in 1-2 sentences. "
        f"Do not just give a synonym for the name. If the name is reused "
        f"for different roles, mention it."
        f"Mark the beginning and end of your explanation with <START> and <END>."
    )


def build_judgement_prompt(variable_name: str, description: str) -> str:
    """Build a prompt that scores one variable description against README axes."""
    return (
        "You are evaluating Python code quality signals from a variable description. "
        f"Variable name: '{variable_name}'. "
        f"Description: '{description}'. "
        "Score each axis from 0 to 2 where 0=good/clear, 1=possible concern, 2=likely problem. "
        "Axes: naming, coupling, cohesion, namespace_pollution, undocumented_assumptions. "
        "Return strict JSON only between <START> and <END> with keys: "
        "naming, coupling, cohesion, namespace_pollution, undocumented_assumptions, summary. "
        "The summary must be one short sentence."
    )


def ask_copilot(prompt: str, timeout: int = 60) -> str:
    """Run `gh copilot -p` with a natural-language prompt and return the response."""
    try:
        result = subprocess.run(
            ["gh", "copilot", "-p", prompt],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "GitHub CLI (`gh`) is not available on PATH. Install it and authenticate with `gh auth login`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Copilot timed out after {timeout}s.") from exc

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"gh copilot returned exit code {result.returncode}.\n" f"stderr: {stderr}"
        )

    return (result.stdout or b"").decode("utf-8", errors="replace").strip()


def ask_gemini(prompt: str, model: str) -> str:
    """Run a Gemini API completion and return plain text."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Export it before running with --provider gemini."
        )
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is not installed. Install with `pip install google-genai`."
        ) from exc

    client = genai.Client(api_key=api_key)
    try:
        result = client.models.generate_content(model=model, contents=prompt)
    except Exception as exc:
        raise RuntimeError(f"Gemini request failed: {exc}") from exc

    text = getattr(result, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text.strip()


def ask_llm(prompt: str, provider: str, timeout: int, model: str) -> str:
    """Dispatch completion call to the selected provider."""
    if provider == "copilot":
        return ask_copilot(prompt, timeout=timeout)
    if provider == "gemini":
        return ask_gemini(prompt, model=model)
    raise RuntimeError(f"Unsupported provider: {provider}")


def clean_response(response: str) -> str:
    """Extract the part of the response between <START> and <END> tokens."""
    start_token = "<START>"
    end_token = "<END>"

    start_idx = response.find(start_token)
    end_idx = response.find(end_token)

    if start_idx == -1 or end_idx == -1 or start_idx >= end_idx:
        raise RuntimeError("Response missing or malformed <START> and <END> tokens.")

    return response[start_idx + len(start_token) : end_idx].strip()


def clean_json_response(response: str) -> dict[str, object]:
    """Extract and parse JSON from a Copilot response with <START>/<END> tokens."""
    payload = clean_response(response)
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse JSON response: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Expected JSON object in Copilot response.")
    return value


def describe_variables(
    root: Path, timeout: int, verbose: bool, provider: str, model: str
) -> dict[str, str]:
    """Return a map of variable name to LLM-produced purpose/usage summary."""
    variables = discover_project_variables(root)
    descriptions: dict[str, str] = {}

    if verbose:
        print(f"Found {len(variables)} unique variable-like names.", file=sys.stderr)

    for variable_name in sorted(variables):
        if verbose:
            file_count = len(variables[variable_name])
            print(
                f"[{provider}] {variable_name} ({file_count} file{'s' if file_count != 1 else ''})",
                file=sys.stderr,
            )

        prompt = build_prompt(variable_name, variables[variable_name], root)
        try:
            response = ask_llm(prompt, provider=provider, timeout=timeout, model=model)
            descriptions[variable_name] = (
                clean_response(response) or "(no LLM response)"
            )
        except RuntimeError as exc:
            descriptions[variable_name] = f"(error: {exc})"
            if verbose:
                print(f"[error] {variable_name}: {exc}", file=sys.stderr)

    return descriptions


def judge_descriptions(
    descriptions: dict[str, str],
    timeout: int,
    verbose: bool,
    provider: str,
    model: str,
) -> dict[str, dict[str, object]]:
    """Return a map of variable name to README-axis judgement."""
    judgements: dict[str, dict[str, object]] = {}
    for variable_name in sorted(descriptions):
        description = descriptions[variable_name]
        if description.startswith("(error:"):
            judgements[variable_name] = {
                "naming": None,
                "coupling": None,
                "cohesion": None,
                "namespace_pollution": None,
                "undocumented_assumptions": None,
                "summary": "Skipped due to description error.",
            }
            continue

        if verbose:
            print(f"[judge:{provider}] {variable_name}", file=sys.stderr)
        prompt = build_judgement_prompt(variable_name, description)
        try:
            response = ask_llm(prompt, provider=provider, timeout=timeout, model=model)
            parsed = clean_json_response(response)
            judgements[variable_name] = {
                "naming": parsed.get("naming"),
                "coupling": parsed.get("coupling"),
                "cohesion": parsed.get("cohesion"),
                "namespace_pollution": parsed.get("namespace_pollution"),
                "undocumented_assumptions": parsed.get("undocumented_assumptions"),
                "summary": parsed.get("summary", ""),
            }
        except RuntimeError as exc:
            judgements[variable_name] = {
                "naming": None,
                "coupling": None,
                "cohesion": None,
                "namespace_pollution": None,
                "undocumented_assumptions": None,
                "summary": f"Error while judging: {exc}",
            }
            if verbose:
                print(f"[error] judge {variable_name}: {exc}", file=sys.stderr)
    return judgements


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Format project with Black, run static checks, and run LLM analysis.",
    )
    parser.add_argument(
        "project_path",
        help="Path to the Python project to analyze.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for each Copilot request (default: 60).",
    )
    parser.add_argument(
        "--provider",
        choices=("copilot", "gemini"),
        default="copilot",
        help="LLM provider for variable descriptions and judgements.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name for --provider gemini (default: models/gemini-2.5-flash).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress information to stderr.",
    )
    parser.add_argument(
        "--skip-judgement",
        action="store_true",
        help="Skip the second AI pass that scores each variable description.",
    )
    args = parser.parse_args()
    model = args.model or "models/gemini-2.5-flash"

    project_root = Path(args.project_path).resolve()
    if not project_root.is_dir():
        sys.exit(f"Error: '{project_root}' is not a directory.")

    if args.verbose:
        print(f"Running Black on {project_root}", file=sys.stderr)
    try:
        run_black(project_root)
    except RuntimeError as exc:
        sys.exit(str(exc))

    # Run static checks on all Python files
    if args.verbose:
        print(f"Running static checks on {project_root}", file=sys.stderr)
    all_issues = []
    for file_path in iter_python_files(project_root):
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        issues = check_file(file_path, source)
        all_issues.extend(issues)

    if all_issues and args.verbose:
        print(f"Found {len(all_issues)} static check issues.", file=sys.stderr)
        for issue in all_issues:
            print(
                f"  {issue.path}:{issue.line}:{issue.column} [{issue.code}] {issue.message}",
                file=sys.stderr,
            )

    if args.verbose:
        print(f"Scanning Python files under {project_root}", file=sys.stderr)
    if args.verbose:
        print(f"Using provider={args.provider} model={model}", file=sys.stderr)
    descriptions = describe_variables(
        project_root,
        args.timeout,
        args.verbose,
        provider=args.provider,
        model=model,
    )

    judgements: dict[str, dict[str, object]] = {}
    if not args.skip_judgement:
        if args.verbose:
            print("Running second AI pass for README-axis judgement", file=sys.stderr)
        judgements = judge_descriptions(
            descriptions,
            args.timeout,
            args.verbose,
            provider=args.provider,
            model=model,
        )

    output = {
        "descriptions": descriptions,
        "judgements": judgements,
        "static_issues": [
            {
                "path": issue.path,
                "line": issue.line,
                "column": issue.column,
                "axis": issue.axis,
                "code": issue.code,
                "message": issue.message,
            }
            for issue in all_issues
        ],
    }
    print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
