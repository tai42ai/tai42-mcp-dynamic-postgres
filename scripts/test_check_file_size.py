from __future__ import annotations

from pathlib import Path

from check_file_size import (
    MAX_SOURCE_LINES,
    count_lines,
    is_test_file,
    oversized_modules,
    python_sources,
)


def _write(path: Path, line_count: int) -> None:
    path.write_text("".join(f"x = {i}\n" for i in range(line_count)))


def test_count_lines(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    _write(target, 5)
    assert count_lines(target) == 5


def test_is_test_file_detects_test_paths_and_names() -> None:
    assert is_test_file(Path("src/pkg/tests/helper.py"))
    assert is_test_file(Path("scripts/test_thing.py"))
    assert is_test_file(Path("pkg/thing_test.py"))
    assert is_test_file(Path("tests/conftest.py"))
    assert not is_test_file(Path("src/pkg/module.py"))


def test_python_sources_skips_tests(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "module.py", 3)
    _write(src / "test_module.py", 3)
    (src / "tests").mkdir()
    _write(src / "tests" / "inner.py", 3)

    found = {p.name for p in python_sources([src])}
    assert found == {"module.py"}


def test_oversized_modules_flags_only_over_limit(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "small.py", 10)
    _write(src / "big.py", 15)

    offenders = oversized_modules([src], max_lines=12)
    assert [(p.name, n) for p, n in offenders] == [("big.py", 15)]


def test_oversized_modules_empty_when_all_within_limit(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "small.py", 10)

    assert oversized_modules([src], max_lines=MAX_SOURCE_LINES) == []
