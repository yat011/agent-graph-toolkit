"""Phase review policy: when to dispatch a clean-context reviewer, and skip-path commits.

Used by the `standard-phase` template after implement. Policy values come from the phase JSON
`review` field (`always` / `if_substantial` / `never`). Diff inspection is git porcelain +
numstat against HEAD, plus untracked files — never an LLM call.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from agentgraph_engine.constants import (
    DEFAULT_REVIEW_LINE_THRESHOLD,
    REVIEW_POLICY_ALWAYS,
    REVIEW_POLICY_IF_SUBSTANTIAL,
    REVIEW_POLICY_NEVER,
)

GitRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]

_PUBLIC_TYPE_RE = re.compile(
    r"\bpublic\s+(?:(?:sealed|abstract|partial|readonly|static)\s+)*"
    r"(?:class|struct|interface|record)\b"
)
_SCENE_OR_PREFAB_SUFFIXES = (".unity", ".prefab")


class GitOpError(Exception):
    """git is missing, or a git command needed for review/commit failed."""


@dataclass(frozen=True)
class DiffStats:
    changed_lines: int
    new_public_type: bool
    scene_or_prefab: bool
    paths: tuple[str, ...]

    def is_substantial(self, line_threshold: int = DEFAULT_REVIEW_LINE_THRESHOLD) -> bool:
        return (
            self.changed_lines > line_threshold
            or self.new_public_type
            or self.scene_or_prefab
        )


def normalize_review_policy(value: object) -> str:
    """Map a phase JSON `review` value to a known policy.

    Missing/blank defaults to `if_substantial`. Unknown values fail-safe to `always`.
    """
    if value is None:
        return REVIEW_POLICY_IF_SUBSTANTIAL
    text = str(value).strip().lower()
    if not text:
        return REVIEW_POLICY_IF_SUBSTANTIAL
    if text in {
        REVIEW_POLICY_ALWAYS,
        REVIEW_POLICY_IF_SUBSTANTIAL,
        REVIEW_POLICY_NEVER,
    }:
        return text
    return REVIEW_POLICY_ALWAYS


def parse_numstat_changed_lines(numstat: str) -> int:
    """Sum insertions + deletions from `git diff --numstat`. Binary rows (`-`) count as 0."""
    total = 0
    for raw in numstat.splitlines():
        parts = raw.split("\t", 2)
        if len(parts) < 2:
            continue
        added, deleted = parts[0], parts[1]
        if added == "-" or deleted == "-":
            continue
        total += int(added) + int(deleted)
    return total


def cs_file_declares_public_type(source: str) -> bool:
    return _PUBLIC_TYPE_RE.search(source) is not None


def path_is_scene_or_prefab(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    return lowered.endswith(_SCENE_OR_PREFAB_SUFFIXES)


def path_is_csharp(path: str) -> bool:
    return path.replace("\\", "/").lower().endswith(".cs")


def porcelain_paths(porcelain: str) -> list[str]:
    """Paths from `git status --porcelain` (handles rename ` -> ` lines)."""
    paths: list[str] = []
    for raw in porcelain.splitlines():
        if len(raw) < 4:
            continue
        path = raw[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path:
            paths.append(path)
    return paths


def _run_git(args: list[str], *, cwd: Path | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
    )


def _git(
    args: list[str],
    *,
    cwd: Path | None,
    run_git: GitRunner | None,
) -> subprocess.CompletedProcess[str]:
    if run_git is not None:
        return run_git(args)
    try:
        return _run_git(args, cwd=cwd)
    except FileNotFoundError as exc:
        raise GitOpError("git is not on PATH") from exc


def _read_optional(cwd: Path | None, rel: str) -> str:
    path = Path(rel) if cwd is None else cwd / rel
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _diff_name_list(diff_names: str, untracked: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw in (*diff_names.splitlines(), *untracked.splitlines()):
        path = raw.strip().strip('"')
        if not path or path in seen:
            continue
        seen.add(path)
        names.append(path)
    return names


def collect_diff_stats(
    *,
    cwd: Path | None = None,
    run_git: GitRunner | None = None,
) -> Optional[DiffStats]:
    """Inspect the working tree vs HEAD. Returns None when git cannot be queried (fail-safe)."""
    try:
        numstat = _git(["diff", "--numstat", "HEAD"], cwd=cwd, run_git=run_git)
        names = _git(["diff", "--name-only", "HEAD"], cwd=cwd, run_git=run_git)
        added = _git(
            ["diff", "--name-only", "--diff-filter=A", "HEAD"],
            cwd=cwd,
            run_git=run_git,
        )
        untracked = _git(
            ["ls-files", "--others", "--exclude-standard"],
            cwd=cwd,
            run_git=run_git,
        )
    except GitOpError:
        return None
    if numstat.returncode != 0 or names.returncode != 0:
        return None
    if added.returncode != 0 or untracked.returncode != 0:
        return None

    paths = _diff_name_list(names.stdout or "", untracked.stdout or "")
    untracked_paths = set(_diff_name_list("", untracked.stdout or ""))
    new_files = _diff_name_list(added.stdout or "", untracked.stdout or "")
    extra_lines = 0
    new_public_type = False
    scene_or_prefab = any(path_is_scene_or_prefab(path) for path in paths)
    for path in new_files:
        if path_is_scene_or_prefab(path):
            scene_or_prefab = True
        if not path_is_csharp(path):
            continue
        source = _read_optional(cwd, path)
        if path in untracked_paths:
            extra_lines += source.count("\n") + (
                1 if source and not source.endswith("\n") else 0
            )
        if cs_file_declares_public_type(source):
            new_public_type = True
    return DiffStats(
        changed_lines=parse_numstat_changed_lines(numstat.stdout or "") + extra_lines,
        new_public_type=new_public_type,
        scene_or_prefab=scene_or_prefab,
        paths=tuple(paths),
    )


def should_dispatch_review(
    policy: object,
    stats: Optional[DiffStats],
    *,
    line_threshold: int = DEFAULT_REVIEW_LINE_THRESHOLD,
) -> bool:
    """Whether a clean-context reviewer should run after implement."""
    normalized = normalize_review_policy(policy)
    if normalized == REVIEW_POLICY_ALWAYS:
        return True
    if normalized == REVIEW_POLICY_NEVER:
        return False
    if stats is None:
        return True
    return stats.is_substantial(line_threshold)


def commit_dirty_tree(
    message: str,
    *,
    cwd: Path | None = None,
    run_git: GitRunner | None = None,
) -> bool:
    """Stage currently dirty paths and commit. Returns False when the tree is clean."""
    status = _git(["status", "--porcelain"], cwd=cwd, run_git=run_git)
    if status.returncode != 0:
        raise GitOpError((status.stderr or status.stdout or "git status failed").strip())
    paths = porcelain_paths(status.stdout or "")
    if not paths:
        return False
    added = _git(["add", "--", *paths], cwd=cwd, run_git=run_git)
    if added.returncode != 0:
        raise GitOpError((added.stderr or added.stdout or "git add failed").strip())
    committed = _git(["commit", "-m", message], cwd=cwd, run_git=run_git)
    if committed.returncode != 0:
        raise GitOpError((committed.stderr or committed.stdout or "git commit failed").strip())
    return True
