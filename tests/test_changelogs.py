"""Repo-consistency guard — every CHANGELOG.md is the Keep-a-Changelog header
with one empty Unreleased section, byte-identical to the canonical stub. Any
tracked — or newly added, not-yet-committed — CHANGELOG.md that differs is a
hard failure naming each path."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_STUB = (
    b"# Changelog\n"
    b"\n"
    b"All notable changes to this project will be documented in this file.\n"
    b"\n"
    b"The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
    b"and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n"
    b"\n"
    b"## [Unreleased]\n"
)


def _changelog_files() -> list[str]:
    # Tracked files, unioned with untracked-but-not-ignored ones, so a new
    # CHANGELOG.md is checked before it is git-added, not only after.
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=False,
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=False,
    ).stdout
    rels = [p.decode("utf-8") for p in (tracked + untracked).split(b"\x00") if p]
    return [rel for rel in rels if Path(rel).name == "CHANGELOG.md"]


def test_every_changelog_is_the_canonical_stub():
    offenders: list[str] = []
    for rel in _changelog_files():
        path = ROOT / rel
        if not path.is_file():  # tracked but deleted in the worktree
            continue
        if path.read_bytes() != _STUB:
            offenders.append(rel)
    assert not offenders, "CHANGELOG.md files differ from the canonical stub:\n" + "\n".join(offenders)


def test_scan_is_not_vacuous():
    # A green result must come from finding at least one CHANGELOG.md, never from
    # an empty scan set: the floor fails loudly if discovery collapses.
    assert len(_changelog_files()) >= 1
