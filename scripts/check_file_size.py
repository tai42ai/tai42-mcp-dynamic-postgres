#!/usr/bin/env python3
"""Fail when a source module grows past the maximum line count.

Test modules carry no line cap and are skipped.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

MAX_SOURCE_LINES = 800
SOURCE_DIRS = ("src", "scripts")


def is_test_file(path: Path) -> bool:
    if "tests" in path.parts:
        return True
    name = path.name
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def python_sources(source_dirs: Iterable[Path]) -> Iterator[Path]:
    for base in source_dirs:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if not is_test_file(path):
                yield path


def oversized_modules(source_dirs: Iterable[Path], max_lines: int) -> list[tuple[Path, int]]:
    offenders: list[tuple[Path, int]] = []
    for path in python_sources(source_dirs):
        lines = count_lines(path)
        if lines > max_lines:
            offenders.append((path, lines))
    return offenders


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    source_dirs = [root / name for name in SOURCE_DIRS]
    offenders = oversized_modules(source_dirs, MAX_SOURCE_LINES)
    for path, lines in offenders:
        rel = path.relative_to(root)
        print(f"{rel}: {lines} lines exceeds the {MAX_SOURCE_LINES}-line limit")
    if offenders:
        return 1
    print(f"All source modules are within {MAX_SOURCE_LINES} lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
