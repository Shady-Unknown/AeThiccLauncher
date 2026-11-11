"""Static refactor metrics for the 1.8 split.

Usage:
    python tools/refactor_metrics.py

It prints a markdown table with physical lines and method counts for the
files that matter to the god-object budget. No third-party dependency.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "ui/main_window.py",
    "ui/shell/app_window.py",
    "ui/shell/layout.py",
    "ui/shell/widgets.py",
    "ui/controllers",
    "core/app_context.py",
    "core/events.py",
    "core/jsonl_store.py",
    "core/data_files.py",
    "managers/stats_manager.py",
    "ui/tabs/stats_tab.py",
]


def _files(target: str) -> list[Path]:
    path = ROOT / target
    if path.is_dir():
        return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
    if path.exists():
        return [path]
    return []


def _count_methods(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return 0
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def main() -> None:
    print("| File | Lines | Methods |")
    print("|---|---:|---:|")
    total_lines = 0
    total_methods = 0
    for target in TARGETS:
        for path in _files(target):
            try:
                lines = len(path.read_text(encoding="utf-8").splitlines())
            except OSError:
                continue
            methods = _count_methods(path)
            total_lines += lines
            total_methods += methods
            print(f"| `{path.relative_to(ROOT).as_posix()}` | {lines} | {methods} |")
    print(f"| **TOTAL** | **{total_lines}** | **{total_methods}** |")


if __name__ == "__main__":
    main()
