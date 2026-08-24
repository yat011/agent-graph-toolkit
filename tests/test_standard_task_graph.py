"""Tests for the ported standard-task graph.py (implement -> review -> success/manual_flag).

Uses a fake `_run_subprocess` (patched at agentgraph_engine.dispatch, the module-level default
executor dispatch_worker falls back to) so no real `claude` CLI process is invoked — fast,
deterministic, and exercises the real node/router functions end to end via `.invoke()`.
"""

import json
import subprocess
from pathlib import Path

import pytest

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    HALT_MANUAL_REQUESTED,
    HALT_REASON_KEY,
    HALT_RETRIES_EXHAUSTED,
    HALT_UNRECOGNIZED_RESULT,
    IMPLEMENT_REQUIREMENTS_NODE,
    ITEM_KEY,
    OUTCOME_KEY,
    OUTCOME_MANUAL_FLAG,
    OUTCOME_SUCCESS,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RESULT_REJECT,
    RESULT_STOPPED,
    REVIEW_NODE,
    ROUTE_KEY,
    RUN_DIR_KEY,
)
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
    calls = {"n": 0, "inputs": []}
    remaining = list(steps)

    def executor(argv, input_text, timeout):
        calls["n"] += 1
        calls["inputs"].append(input_text)
        if not remaining:
            raise AssertionError("executor called more times than scripted")
        content, ok = remaining.pop(0)
        if content is not None:
            _write_output(input_text, content)
        return subprocess.CompletedProcess(
            argv, 0 if ok else 1, stdout=json.dumps({"result": content or ""}), stderr=""
        )

    return executor, calls


def test_verified_result_does_not_skip_review(monkeypatch, build_graph, tmp_path):
    """Result: verified is not a skip-review path; it routes to manual_flag."""
    executor, calls = _script_executor(
        [
            ("Result: verified", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}}
    )
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_FLAG
    assert REVIEW_NODE not in result or not (result.get(REVIEW_NODE) or {}).get(ATTEMPT_COUNT_KEY)
    flagged = tmp_path / "05_manual_flag" / "attempt-1" / "output.md"
    assert flagged.read_text(encoding="utf-8") == "Result: flagged\n"


def test_implemented_prompt_puts_item_fields_in_suffix(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            (f"Result: {RESULT_IMPLEMENTED}", True),
            (f"Result: {RESULT_ACCEPT}", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    item = {
        "title": "UNIQUE_TITLE_XYZ",
        "description": "UNIQUE_DESC_XYZ",
        "test_cases": ["happy path"],
        "test_scope": "tests/test_foo.py",
        "dependencies": [],
    }
    result = graph.invoke({RUN_DIR_KEY: str(tmp_path), ITEM_KEY: item})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    implement_text = calls["inputs"][0]
    assert "Implement the task." in implement_text
    assert implement_text.index("Implement the task.") < implement_text.index("UNIQUE_TITLE_XYZ")
    assert implement_text.index("Implement the task.") < implement_text.index("UNIQUE_DESC_XYZ")
    lowered = implement_text.lower()
    assert "full_suite" not in lowered
    assert "kind: verify" not in lowered
    assert "skips review" not in lowered
    assert "result: verified" not in lowered


def test_implemented_then_accepted_reaches_success(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [(f"Result: {RESULT_IMPLEMENTED}", True), (f"Result: {RESULT_ACCEPT}", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 1


def test_rejected_loops_back_under_three_attempts_then_succeeds(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            (f"Result: {RESULT_IMPLEMENTED}", True),
            (f"Result: {RESULT_REJECT} — needs fix", True),
            (f"Result: {RESULT_IMPLEMENTED}", True),
            (f"Result: {RESULT_ACCEPT}", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}},
        config={"recursion_limit": 50},
    )
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 2
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 2


def test_rejected_three_times_routes_to_manual_flag(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            (f"Result: {RESULT_IMPLEMENTED}", True),
            (f"Result: {RESULT_REJECT} — still bad", True),
            (f"Result: {RESULT_IMPLEMENTED}", True),
            (f"Result: {RESULT_REJECT} — still bad", True),
            (f"Result: {RESULT_IMPLEMENTED}", True),
            (f"Result: {RESULT_REJECT} — still bad", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}},
        config={"recursion_limit": 50},
    )
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_FLAG
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 3


def test_stopped_without_completing_routes_to_manual_flag(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            (f"Result: {RESULT_STOPPED} — missing capability", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}})
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_FLAG


def test_unrecognized_review_result_goes_to_manual_immediately(monkeypatch, build_graph, tmp_path):
    """A `Result:` line that is neither accepted, rejected, nor manual — garbled/missing —
    routes to manual_flag immediately with halt_reason unrecognized_result. It does not retry
    review and does not loop back to implement_requirements.
    """
    executor, calls = _script_executor(
        [
            (f"Result: {RESULT_IMPLEMENTED}", True),
            ("garbled nonsense, no keyword", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke(
        {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}},
        config={"recursion_limit": 50},
    )
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_FLAG
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[REVIEW_NODE][ROUTE_KEY] == "manual"
    assert result[REVIEW_NODE][HALT_REASON_KEY] == HALT_UNRECOGNIZED_RESULT
    assert result[HALTED_KEY] is True
    assert result[HALT_REASON_KEY] == HALT_UNRECOGNIZED_RESULT
    assert result[HALTED_AT_NODE_KEY] == IMPLEMENT_REQUIREMENTS_NODE
    assert calls["n"] == 2


def test_manual_keyword_from_review_routes_immediately_to_manual_flag(monkeypatch, build_graph, tmp_path):
    """The reserved `manual` Result: keyword is an explicit human escape hatch: it routes
    straight to manual_flag on the very first review dispatch, bypassing the reject-loop budget.
    """
    executor, calls = _script_executor(
        [
            (f"Result: {RESULT_IMPLEMENTED}", True),
            ("Result: manual — needs a human judgment call", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}})
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_FLAG
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[HALTED_KEY] is True
    assert result[HALT_REASON_KEY] == HALT_MANUAL_REQUESTED
    assert result[HALTED_AT_NODE_KEY] == IMPLEMENT_REQUIREMENTS_NODE


def test_technical_failure_exhausting_retries_halts(monkeypatch, build_graph, tmp_path):
    # implement_requirements has retry=1 -> 2 attempts; never write output.md -> both fail.
    def always_fail(argv, input_text, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout='{"result":""}', stderr="boom")

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", always_fail)

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(tmp_path), ITEM_KEY: {"title": "t", "description": "d"}})
    assert result.get(HALTED_KEY) is True
    assert result.get(HALT_REASON_KEY) == HALT_RETRIES_EXHAUSTED
    assert OUTCOME_KEY not in result or result.get(OUTCOME_KEY) is None
