"""Tests for the ported standard-task graph.py (implement -> review -> success/manual_flag).

Uses a fake `_run_subprocess` (patched at agentgraph_engine.dispatch, the module-level default
executor dispatch_worker falls back to) so no real `claude` CLI process is invoked — fast,
deterministic, and exercises the real node/router functions end to end via `.invoke()`.
"""

import json
import subprocess
from pathlib import Path

import pytest

from agentgraph_engine.graph_loader import get_build_graph, load_graph_module

GRAPH_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "agentgraph-run-graph"
    / "templates"
    / "standard-task"
    / "graph.py"
)

MARKER = "Write your full output to this exact file path before finishing: "


def _write_output(input_text: str, content: str) -> None:
    path_line = next(line for line in input_text.splitlines() if line.startswith(MARKER))
    out_path = Path(path_line[len(MARKER) :].strip())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


@pytest.fixture
def build_graph():
    module = load_graph_module(GRAPH_PATH)
    return get_build_graph(module)


def _script_executor(steps):
    """steps: flat list of (result_content_or_None, ok), consumed strictly in call order — the
    graph's own node/router logic already fixes the chronological order of role dispatches, so
    no role-matching is needed here, just "the next thing that happens".
    """
    calls = {"n": 0}
    remaining = list(steps)

    def executor(argv, input_text, timeout):
        calls["n"] += 1
        if not remaining:
            raise AssertionError("executor called more times than scripted")
        content, ok = remaining.pop(0)
        if content is not None:
            _write_output(input_text, content)
        return subprocess.CompletedProcess(
            argv, 0 if ok else 1, stdout=json.dumps({"result": content or ""}), stderr=""
        )

    return executor, calls


def test_verified_skips_review_and_reaches_success(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor([("Result: verified", True)])
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {"run_dir": str(tmp_path), "item": {"title": "t", "description": "d", "kind": "verify"}}
    )
    assert result["outcome"] == "success"
    assert calls["n"] == 1  # review never dispatched


def test_implemented_then_accepted_reaches_success(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [("Result: implemented", True), ("Result: accepted", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke({"run_dir": str(tmp_path), "item": {"title": "t", "description": "d"}})
    assert result["outcome"] == "success"
    assert result["implement_attempt_count"] == 1
    assert result["review_attempt_count"] == 1


def test_rejected_loops_back_under_three_attempts_then_succeeds(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            ("Result: implemented", True),
            ("Result: rejected — needs fix", True),
            ("Result: implemented", True),
            ("Result: accepted", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {"run_dir": str(tmp_path), "item": {"title": "t", "description": "d"}},
        config={"recursion_limit": 50},
    )
    assert result["outcome"] == "success"
    assert result["implement_attempt_count"] == 2
    assert result["review_attempt_count"] == 2


def test_rejected_three_times_routes_to_manual_flag(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            ("Result: implemented", True),
            ("Result: rejected — still bad", True),
            ("Result: implemented", True),
            ("Result: rejected — still bad", True),
            ("Result: implemented", True),
            ("Result: rejected — still bad", True),
            ("manual flag summary\nResult: flagged", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {"run_dir": str(tmp_path), "item": {"title": "t", "description": "d"}},
        config={"recursion_limit": 50},
    )
    assert result["outcome"] == "manual_flag"
    assert result["implement_attempt_count"] == 3


def test_stopped_without_completing_routes_to_manual_flag(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            ("Result: stopped — missing capability", True),
            ("manual flag summary\nResult: flagged", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke({"run_dir": str(tmp_path), "item": {"title": "t", "description": "d"}})
    assert result["outcome"] == "manual_flag"


def test_unrecognized_review_result_retries_review_then_falls_to_manual_flag(monkeypatch, build_graph, tmp_path):
    """A `Result:` line that is neither the accept keyword ("accepted") nor the reject keyword
    ("rejected") -- garbled/missing -- is a soft failure: it retries 03_review itself (bounded by
    this gate's own self-retry budget, mirroring its `dispatch_with_retry(retry=1)`), NOT a
    loop-back to 02_implement_requirements (the pre-fix bug: an unrecognized line used to fall
    straight into the attempt-count check and silently loop back into rework).
    """
    executor, calls = _script_executor(
        [
            ("Result: implemented", True),  # implement_requirements
            ("garbled nonsense, no keyword", True),  # review attempt 1 -> unrecognized -> self-retry
            ("still garbled", True),  # review attempt 2 (self-retry) -> budget exhausted -> manual
            ("manual flag summary\nResult: flagged", True),  # manual_flag
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {"run_dir": str(tmp_path), "item": {"title": "t", "description": "d"}},
        config={"recursion_limit": 50},
    )
    assert result["outcome"] == "manual_flag"
    # implement_requirements was dispatched exactly once -- proof this never looped back to it.
    assert result["implement_attempt_count"] == 1
    # review itself was dispatched twice (the self-retry), never implement_requirements again.
    assert result["review_attempt_count"] == 2
    assert result["review_self_retry_count"] == 1
    assert result["halted"] is True
    assert result["halt_reason"] == "unrecognized_result_exhausted"
    assert result["halted_at_node"] == "implement_requirements"


def test_manual_keyword_from_review_routes_immediately_to_manual_flag(monkeypatch, build_graph, tmp_path):
    """The reserved `manual` Result: keyword is an explicit human escape hatch: it routes
    straight to manual_flag on the very first review dispatch, bypassing the attempt-count check
    entirely (unlike a rejection, which only reaches manual_flag once attempts are exhausted).
    """
    executor, calls = _script_executor(
        [
            ("Result: implemented", True),  # implement_requirements
            ("Result: manual — needs a human judgment call", True),  # review -> explicit manual
            ("manual flag summary\nResult: flagged", True),  # manual_flag
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke({"run_dir": str(tmp_path), "item": {"title": "t", "description": "d"}})
    assert result["outcome"] == "manual_flag"
    # Only one review dispatch -- the manual keyword bypassed the attempt-count check entirely,
    # never retried review and never looped back to implement_requirements.
    assert result["review_attempt_count"] == 1
    assert result["implement_attempt_count"] == 1
    assert result["halted"] is True
    assert result["halt_reason"] == "manual_requested"
    assert result["halted_at_node"] == "implement_requirements"


def test_technical_failure_exhausting_retries_halts(monkeypatch, build_graph, tmp_path):
    # implement_requirements has retry=1 -> 2 attempts; never write output.md -> both fail.
    def always_fail(argv, input_text, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout='{"result":""}', stderr="boom")

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", always_fail)

    graph = build_graph()
    result = graph.invoke({"run_dir": str(tmp_path), "item": {"title": "t", "description": "d"}})
    assert result.get("halted") is True
    assert result.get("halt_reason") == "retries_exhausted"
    assert "outcome" not in result or result.get("outcome") is None
