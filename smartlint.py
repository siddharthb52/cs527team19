#!/usr/bin/env python3
"""
README-aligned linter: static checks for naming, coupling, cohesion,
namespace pollution, and undocumented assumptions.

Optional formatting (Black) is separate from these semantic checks.

Usage:
  python smartlint.py [PATH ...]     Run checks on files or directories (default: .)
  python smartlint.py --format PATH  Run Black only on PATH (legacy helper)
"""

from __future__ import annotations

import argparse
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


def run_checks(paths: list[Path]) -> int:
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
    return 1 if all_issues else 0


def run_black(target: str) -> int:
    """Invoke Black on *target*. Return process-style exit code (0 ok, 1 Black failed, 2 no binary)."""
    try:
        subprocess.run(["black", target], check=True)
    except FileNotFoundError:
        print("Black not found on PATH. Install with: pip install black", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError:
        return 1
    return 0


def main() -> int:
    """Parse CLI args and either run semantic checks or ``--format`` (Black-only)."""
    parser = argparse.ArgumentParser(
        description="Semantic checks aligned with README axes. Optional Black.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[Path(".")],
        type=Path,
        help="Files or directories to check (default: current directory)",
    )
    parser.add_argument(
        "--format",
        metavar="PATH",
        default=None,
        help="Run Black on PATH instead of running semantic checks",
    )
    args = parser.parse_args()

    if args.format is not None:
        return run_black(args.format)

    return run_checks(list(args.paths))


if __name__ == "__main__":
    raise SystemExit(main())
