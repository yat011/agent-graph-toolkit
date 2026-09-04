"""Tests for the standard-phase graph.py (implement -> conditional review -> success / pause).

Uses a fake `_run_subprocess` so no real Worker CLI is invoked. Pause paths compile with
InMemorySaver. Git side effects are patched at `agentgraph_engine.review_policy`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    HALT_MANUAL_REQUESTED,
    HALT_REASON_KEY,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_RETRIES_EXHAUSTED,
    HALT_UNRECOGNIZED_RESULT,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    HANDOFF_FILENAME,
    IMPLEMENT_REQUIREMENTS_NODE,
    ITEM_KEY,
    OUTCOME_KEY,
    OUTCOME_SUCCESS,
    PLAN_PATH_KEY,
    PREVIOUS_HANDOFF_PATH_KEY,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RESULT_KEY,
    RESULT_REJECT,
    RESULT_STOPPED,
    REVIEW_NODE,
    REVIEW_POLICY_ALWAYS,
    REVIEW_POLICY_IF_SUBSTANTIAL,
    REVIEW_POLICY_NEVER,
    ROUTE_KEY,
    RUN_DIR_KEY,
    SKIP_REVIEW_COMMIT_NODE,
    SPEC_PATH_KEY,
)
from agentgraph_engine.dispatch import OUTPUT_PATH_LINE_PREFIX
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.pause import (
    INTERRUPT_REASON_KEY,
    INTERRUPT_REDRIVE_NODE_KEY,
    INTERRUPT_RESET_ATTEMPTS_KEY,
    interrupt_payload_from_result,
)
from agentgraph_engine.review_policy import DiffStats

GRAPH_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "agentgraph-run-graph"
    / "templates"
    / "standard-phase"
    / "graph.py"
)

MARKER = OUTPUT_PATH_LINE_PREFIX


def _write_output(input_text: str, content: str) -> None:
    path_line = next(line for line in input_text.splitlines() if line.startswith(MARKER))
    out_path = Path(path_line[len(MARKER) :].strip())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


@pytest.fixture
def build_graph(monkeypatch):
    monkeypatch.setattr(
        "agentgraph_engine.review_policy.commit_dirty_tree",
        lambda message, **kwargs: False,
    )
    monkeypatch.setattr(
        "agentgraph_engine.review_policy.collect_diff_stats",
        lambda **kwargs: DiffStats(0, False, False, ()),
    )
    module = load_graph_module(GRAPH_PATH)
    return get_build_graph(module)


def _invoke(build_graph, state, *, thread: str = "t"):
    graph = build_graph(checkpointer=InMemorySaver())
    return graph.invoke(
        state,
        config={"configurable": {"thread_id": thread}, "recursion_limit": 80},
    )


def _assert_paused(result, *, reason: str, redrive: str, reset: bool) -> dict:
    payload = interrupt_payload_from_result(result)
    assert payload is not None
    assert payload[INTERRUPT_REASON_KEY] == reason
    assert payload[INTERRUPT_REDRIVE_NODE_KEY] == redrive
    assert payload[INTERRUPT_RESET_ATTEMPTS_KEY] is reset
    assert result.get(HALTED_KEY) is True
    assert result.get(HALT_REASON_KEY) == reason
    assert result.get(OUTCOME_KEY) is None
    return payload


def _script_executor(steps):
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


def _item(**overrides) -> dict:
    item = {
        "id": "1",
        "title": "t",
        "description": "d",
        "review": REVIEW_POLICY_ALWAYS,
    }
    item.update(overrides)
    return item


def test_implemented_then_accepted_reaches_success(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [(f"Result: {RESULT_IMPLEMENTED}", True), (f"Result: {RESULT_ACCEPT}", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    result = _invoke(build_graph, {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item()})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 1
    assert calls["n"] == 2


def test_review_never_skips_reviewer_and_commits(monkeypatch, build_graph, tmp_path):
    committed = {"n": 0}

    def fake_commit(message, **kwargs):
        committed["n"] += 1
        committed["message"] = message
        return True

    monkeypatch.setattr("agentgraph_engine.review_policy.commit_dirty_tree", fake_commit)
    executor, calls = _script_executor([(f"Result: {RESULT_IMPLEMENTED}", True)])
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)

    result = _invoke(
        build_graph,
        {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item(id="p2", title="Scale remaining", review=REVIEW_POLICY_NEVER)},
    )
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert calls["n"] == 1
    assert REVIEW_NODE not in result or not (result.get(REVIEW_NODE) or {}).get(ATTEMPT_COUNT_KEY)
    assert result[SKIP_REVIEW_COMMIT_NODE][ATTEMPT_COUNT_KEY] == 1
    assert committed["n"] == 1
    assert committed["message"] == "p2: Scale remaining"
    receipt = (tmp_path / "04_success" / "attempt-1" / "output.md").read_text(encoding="utf-8")
    assert "review skipped by policy" in receipt


def test_if_substantial_small_diff_skips_review(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor([(f"Result: {RESULT_IMPLEMENTED}", True)])
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    result = _invoke(
        build_graph,
        {
            RUN_DIR_KEY: str(tmp_path),
            ITEM_KEY: _item(review=REVIEW_POLICY_IF_SUBSTANTIAL),
        },
    )
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert calls["n"] == 1
    assert SKIP_REVIEW_COMMIT_NODE in result


def test_if_substantial_large_diff_dispatches_review(monkeypatch, build_graph, tmp_path):
    monkeypatch.setattr(
        "agentgraph_engine.review_policy.collect_diff_stats",
        lambda **kwargs: DiffStats(81, False, False, ("a.py",)),
    )
    executor, calls = _script_executor(
        [(f"Result: {RESULT_IMPLEMENTED}", True), (f"Result: {RESULT_ACCEPT}", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    result = _invoke(
        build_graph,
        {
            RUN_DIR_KEY: str(tmp_path),
            ITEM_KEY: _item(review=REVIEW_POLICY_IF_SUBSTANTIAL),
        },
    )
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert calls["n"] == 2
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 1


def test_implement_prompt_includes_handoff_spec_plan_and_previous(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [(f"Result: {RESULT_IMPLEMENTED}", True), (f"Result: {RESULT_ACCEPT}", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    spec = tmp_path / "spec.md"
    plan = tmp_path / "plan.md"
    prev = tmp_path / "prev" / HANDOFF_FILENAME
    prev.parent.mkdir()
    prev.write_text("## Decisions\n- keep names\n", encoding="utf-8")
    result = _invoke(
        build_graph,
        {
            RUN_DIR_KEY: str(tmp_path / "phase"),
            ITEM_KEY: _item(title="UNIQUE_TITLE_XYZ", description="UNIQUE_DESC_XYZ"),
            SPEC_PATH_KEY: str(spec),
            PLAN_PATH_KEY: str(plan),
            PREVIOUS_HANDOFF_PATH_KEY: str(prev),
        },
    )
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    implement_text = calls["inputs"][0]
    assert "Implement the phase." in implement_text
    assert implement_text.index("Implement the phase.") < implement_text.index("UNIQUE_TITLE_XYZ")
    assert HANDOFF_FILENAME in implement_text
    assert str(spec) in implement_text
    assert str(plan) in implement_text
    assert str(prev) in implement_text
    assert "Do not reopen a previous implementer's output.md" in implement_text
    review_text = calls["inputs"][1]
    assert str(prev) in review_text
    assert "do not flag it as missing work" in review_text
    lowered = implement_text.lower()
    assert "full_suite" not in lowered
    assert "kind: verify" not in lowered


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
    result = _invoke(build_graph, {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item()})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 2
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 2


def test_rejected_three_times_pauses_with_redrive_implement(monkeypatch, build_graph, tmp_path):
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
    result = _invoke(build_graph, {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item()})
    _assert_paused(
        result,
        reason=HALT_REJECT_ATTEMPTS_EXHAUSTED,
        redrive=IMPLEMENT_REQUIREMENTS_NODE,
        reset=True,
    )
    assert result[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 3
    assert result[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 3


def test_stopped_without_completing_pauses_with_redrive_implement(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [(f"Result: {RESULT_STOPPED} — missing capability", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    result = _invoke(build_graph, {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item()})
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=IMPLEMENT_REQUIREMENTS_NODE,
        reset=True,
    )


def test_unrecognized_review_result_pauses_immediately(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [(f"Result: {RESULT_IMPLEMENTED}", True), ("garbled nonsense, no keyword", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    result = _invoke(build_graph, {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item()})
    _assert_paused(
        result,
        reason=HALT_UNRECOGNIZED_RESULT,
        redrive=REVIEW_NODE,
        reset=True,
    )
    assert result[REVIEW_NODE][ROUTE_KEY] == "manual"
    assert result[HALTED_AT_NODE_KEY] == REVIEW_NODE
    assert calls["n"] == 2


def test_manual_keyword_from_review_pauses_immediately(monkeypatch, build_graph, tmp_path):
    executor, calls = _script_executor(
        [
            (f"Result: {RESULT_IMPLEMENTED}", True),
            ("Result: manual — needs a human judgment call", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    result = _invoke(build_graph, {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item()})
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=REVIEW_NODE,
        reset=True,
    )


def test_technical_failure_exhausting_retries_pauses_with_reset(
    monkeypatch, build_graph, tmp_path
):
    def always_fail(argv, input_text, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout='{"result":""}', stderr="boom")

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", always_fail)
    result = _invoke(build_graph, {RUN_DIR_KEY: str(tmp_path), ITEM_KEY: _item()})
    _assert_paused(
        result,
        reason=HALT_RETRIES_EXHAUSTED,
        redrive=IMPLEMENT_REQUIREMENTS_NODE,
        reset=True,
    )
