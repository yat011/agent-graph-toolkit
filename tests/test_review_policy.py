"""Tests for agentgraph_engine.review_policy — phase review dispatch and skip-path commit."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentgraph_engine.constants import (
    DEFAULT_REVIEW_LINE_THRESHOLD,
    REVIEW_POLICY_ALWAYS,
    REVIEW_POLICY_IF_SUBSTANTIAL,
    REVIEW_POLICY_NEVER,
)
from agentgraph_engine.review_policy import (
    DiffStats,
    GitOpError,
    commit_dirty_tree,
    collect_diff_stats,
    cs_file_declares_public_type,
    normalize_review_policy,
    parse_numstat_changed_lines,
    path_is_scene_or_prefab,
    porcelain_paths,
    should_dispatch_review,
)


def test_always_dispatches_review_without_stats():
    assert should_dispatch_review(REVIEW_POLICY_ALWAYS, None) is True


def test_never_skips_review():
    stats = DiffStats(changed_lines=999, new_public_type=True, scene_or_prefab=True, paths=("a.cs",))
    assert should_dispatch_review(REVIEW_POLICY_NEVER, stats) is False


def test_if_substantial_over_line_threshold():
    stats = DiffStats(
        changed_lines=81,
        new_public_type=False,
        scene_or_prefab=False,
        paths=("a.py",),
    )
    assert should_dispatch_review(
        REVIEW_POLICY_IF_SUBSTANTIAL, stats, line_threshold=80
    ) is True


def test_if_substantial_at_threshold_is_not_substantial():
    stats = DiffStats(
        changed_lines=80,
        new_public_type=False,
        scene_or_prefab=False,
        paths=("a.py",),
    )
    assert should_dispatch_review(
        REVIEW_POLICY_IF_SUBSTANTIAL, stats, line_threshold=80
    ) is False


def test_if_substantial_new_public_type_is_substantial():
    stats = DiffStats(
        changed_lines=1,
        new_public_type=True,
        scene_or_prefab=False,
        paths=("Foo.cs",),
    )
    assert should_dispatch_review(REVIEW_POLICY_IF_SUBSTANTIAL, stats) is True


def test_if_substantial_scene_or_prefab_is_substantial():
    stats = DiffStats(
        changed_lines=1,
        new_public_type=False,
        scene_or_prefab=True,
        paths=("HomeScene.unity",),
    )
    assert should_dispatch_review(REVIEW_POLICY_IF_SUBSTANTIAL, stats) is True


def test_if_substantial_none_stats_fail_safe_reviews():
    assert should_dispatch_review(REVIEW_POLICY_IF_SUBSTANTIAL, None) is True


def test_normalize_missing_and_blank_are_if_substantial():
    assert normalize_review_policy(None) == REVIEW_POLICY_IF_SUBSTANTIAL
    assert normalize_review_policy("") == REVIEW_POLICY_IF_SUBSTANTIAL
    assert normalize_review_policy("  ") == REVIEW_POLICY_IF_SUBSTANTIAL


def test_normalize_unknown_fail_safe_always():
    assert normalize_review_policy("sometimes") == REVIEW_POLICY_ALWAYS


def test_parse_numstat_sums_insertions_and_deletions_skips_binary():
    text = "10\t5\tfoo.py\n3\t1\tbar.py\n-\t-\tbin.dat\n"
    assert parse_numstat_changed_lines(text) == 19


def test_cs_public_type_detection():
    assert cs_file_declares_public_type("public class Foo {}")
    assert cs_file_declares_public_type("public sealed class Bar {}")
    assert cs_file_declares_public_type("public struct Baz {}")
    assert cs_file_declares_public_type("public interface IFoo {}")
    assert cs_file_declares_public_type("public record Item();")
    assert not cs_file_declares_public_type("class Foo {}")
    assert not cs_file_declares_public_type("public void Foo() {}")


def test_path_is_scene_or_prefab():
    assert path_is_scene_or_prefab("Assets/Scenes/HomeScene.unity")
    assert path_is_scene_or_prefab(r"Assets\Prefabs\Cat.prefab")
    assert not path_is_scene_or_prefab("Assets/Local/Scripts/Cat.cs")


def test_porcelain_paths_handles_rename():
    porcelain = " M foo.py\nR  old.txt -> new.txt\n?? extra.cs\n"
    assert porcelain_paths(porcelain) == ["foo.py", "new.txt", "extra.cs"]


def test_collect_diff_stats_counts_numstat_and_untracked_public_type(tmp_path):
    new_cs = tmp_path / "NavPlane.cs"
    new_cs.write_text("public class NavPlane {}\n", encoding="utf-8")

    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        if cmd == "diff" and "--numstat" in args:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="2\t1\told.py\n", stderr="")
        if cmd == "diff" and "--diff-filter=A" in args:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        if cmd == "diff" and "--name-only" in args:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="old.py\n", stderr="")
        if cmd == "ls-files":
            return subprocess.CompletedProcess(["git", *args], 0, stdout="NavPlane.cs\n", stderr="")
        raise AssertionError(args)

    stats = collect_diff_stats(cwd=tmp_path, run_git=run_git)
    assert stats is not None
    assert stats.changed_lines == 3 + 1
    assert stats.new_public_type is True
    assert stats.scene_or_prefab is False
    assert "old.py" in stats.paths
    assert "NavPlane.cs" in stats.paths


def test_collect_diff_stats_flags_prefab(tmp_path):
    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "diff" and "--numstat" in args:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="1\t0\tCat.prefab\n", stderr="")
        if args[0] == "diff" and "--name-only" in args:
            return subprocess.CompletedProcess(["git", *args], 0, stdout="Cat.prefab\n", stderr="")
        if args[0] == "ls-files":
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        raise AssertionError(args)

    stats = collect_diff_stats(cwd=tmp_path, run_git=run_git)
    assert stats is not None
    assert stats.scene_or_prefab is True
    assert stats.is_substantial(DEFAULT_REVIEW_LINE_THRESHOLD) is True


def test_collect_diff_stats_returns_none_when_git_fails():
    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git", *args], 128, stdout="", stderr="not a repo")

    assert collect_diff_stats(run_git=run_git) is None


def test_commit_dirty_tree_adds_porcelain_paths_then_commits():
    calls: list[list[str]] = []

    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "status":
            return subprocess.CompletedProcess(
                ["git", *args], 0, stdout=" M foo.py\n?? bar.py\n", stderr=""
            )
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    assert commit_dirty_tree("1: title", run_git=run_git) is True
    assert calls[0] == ["status", "--porcelain"]
    assert calls[1][0] == "add"
    assert "foo.py" in calls[1]
    assert "bar.py" in calls[1]
    assert calls[2] == ["commit", "-m", "1: title"]


def test_commit_dirty_tree_clean_tree_is_false():
    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    assert commit_dirty_tree("1: title", run_git=run_git) is False


def test_commit_dirty_tree_raises_on_commit_failure():
    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "status":
            return subprocess.CompletedProcess(["git", *args], 0, stdout=" M foo.py\n", stderr="")
        if args[0] == "commit":
            return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr="no user")
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")

    try:
        commit_dirty_tree("1: title", run_git=run_git)
        raise AssertionError("expected GitOpError")
    except GitOpError as exc:
        assert "no user" in str(exc)
