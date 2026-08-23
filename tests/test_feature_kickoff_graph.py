"""Tests for the ported feature-kickoff graph.py: branch/planner/tech-review loop, load_tasks
env-check branch, sequential map/fan-out over standard-task with a dependency gate, and the
final-review accepted/manual branch. Uses a fake `_run_subprocess` (no real `claude` CLI calls).
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
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_UNRECOGNIZED_RESULT,
    ITEM_KEY,
    LOAD_TASKS_NODE,
    MAP_TASK_STATES_KEY,
    OUTCOME_BLOCKED,
    OUTCOME_KEY,
    OUTCOME_MANUAL_FLAG,
    OUTCOME_MANUAL_REVIEW,
    OUTCOME_SUCCESS,
    PLANNER_NODE,
    RESULT_ACCEPT,
    RESULT_REJECT,
    RESULT_STOPPED,
    RESULT_IMPLEMENTED,
    RUN_DIR_KEY,
    STANDARD_TASK_SUCCESS_DIR,
    TECH_PLAN_REVIEWER_NODE,
)
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module

GRAPH_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "agentgraph-run-graph"
    / "templates"
    / "feature-kickoff"
    / "graph.py"
)

MARKER = "Write your full output to this exact file path before finishing: "


def _write_output(input_text: str, content: str) -> None:
    path_line = next(line for line in input_text.splitlines() if line.startswith(MARKER))
    out_path = Path(path_line[len(MARKER) :].strip())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


def _script_executor(steps):
    remaining = list(steps)

    def executor(argv, input_text, timeout):
        if not remaining:
            raise AssertionError("executor called more times than scripted")
        content, ok = remaining.pop(0)
        if content is not None:
            _write_output(input_text, content)
        return subprocess.CompletedProcess(
            argv, 0 if ok else 1, stdout=json.dumps({"result": content or ""}), stderr=""
        )

    return executor


@pytest.fixture
def build_graph():
    module = load_graph_module(GRAPH_PATH)
    return get_build_graph(module)


def _seed_plan_files(tmp_path, run_dir: Path, items: list):
    """Seed the plan .md + .tasks.json files 02_planner's output.md points at, since the fake
    executor doesn't actually run a planner LLM that would create these itself.
    """
    plans_dir = tmp_path / "agent_works" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    tasks_json = plans_dir / "demo.tasks.json"
    tasks_json.write_text(json.dumps(items), encoding="utf-8")
    return tasks_json


def test_accepted_path_env_working_reaches_success(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])

    steps = [
        ("branch ready\nResult: branch ready", True),
        (f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"loaded 0 tasks\nResult: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        ("Result: recap written", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert result["planner_node"][ATTEMPT_COUNT_KEY] == 1


def test_reject_loops_back_then_blocked_after_three_attempts(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        (f"Result: {RESULT_REJECT} — missing test cases", True),
        (plan_line, True),
        (f"Result: {RESULT_REJECT} — still missing", True),
        (plan_line, True),
        (f"Result: {RESULT_REJECT} — still missing", True),
        ("blocked\nResult: blocked", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_BLOCKED
    assert result["planner_node"][ATTEMPT_COUNT_KEY] == 3
    assert result[HALTED_KEY] is True
    assert result[HALTED_AT_NODE_KEY] == PLANNER_NODE
    assert result[HALT_REASON_KEY] == HALT_REJECT_ATTEMPTS_EXHAUSTED


def test_unrecognized_tech_review_result_falls_to_blocked_not_loop_back(monkeypatch, build_graph, tmp_path):
    """A garbled Result: line on tech_plan_reviewer routes immediately to blocked_plan_rejected
    with halt_reason unrecognized_result — it does not loop back to planner.
    """
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        ("garbled, no recognizable keyword at all", True),
        ("blocked\nResult: blocked", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_BLOCKED
    assert result["planner_node"][ATTEMPT_COUNT_KEY] == 1
    assert result[TECH_PLAN_REVIEWER_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[HALT_REASON_KEY] == HALT_UNRECOGNIZED_RESULT
    assert result[HALTED_AT_NODE_KEY] == PLANNER_NODE


def test_manual_keyword_from_tech_review_routes_immediately_to_blocked(monkeypatch, build_graph, tmp_path):
    """The reserved `manual` Result: keyword bypasses the reject-loop budget — reaches
    blocked_plan_rejected on the very first tech-review dispatch.
    """
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        ("Result: manual — needs a human judgment call", True),
        ("blocked\nResult: blocked", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_BLOCKED
    assert result["planner_node"][ATTEMPT_COUNT_KEY] == 1
    assert result[HALT_REASON_KEY] == HALT_MANUAL_REQUESTED
    assert result[HALTED_AT_NODE_KEY] == PLANNER_NODE


def test_env_down_routes_to_needs_manual_review(monkeypatch, build_graph, tmp_path):
    """04_load_tasks has no reject/loop-back keyword pair: an environment-down Result: line
    that isn't accepted routes to needs_manual_review immediately (no self-retry hop).
    """
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        ("env down\nResult: manual — environment not working", True),
        ("manual review needed\nResult: manual review needed", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_REVIEW
    assert result[LOAD_TASKS_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[HALTED_KEY] is True
    assert result[HALTED_AT_NODE_KEY] == LOAD_TASKS_NODE


def test_sequential_fan_out_with_dependency_gate_and_final_review_passed(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    items = [
        {"id": "t1", "title": "First", "description": "d1", "dependencies": []},
        {"id": "t2", "title": "Second", "description": "d2", "dependencies": ["t1"]},
    ]
    tasks_json = _seed_plan_files(tmp_path, run_dir, items)
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"loaded 2 tasks\nResult: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        ("Result: recap written", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    outcomes = {(s.get(ITEM_KEY) or {}).get("id"): s.get(OUTCOME_KEY) for s in result[MAP_TASK_STATES_KEY]}
    assert outcomes == {"t1": OUTCOME_SUCCESS, "t2": OUTCOME_SUCCESS}


def test_dependency_on_manual_flagged_item_leaves_dependent_blocked(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    items = [
        {"id": "t1", "title": "First", "description": "d1", "dependencies": []},
        {"id": "t2", "title": "Second", "description": "d2", "dependencies": ["t1"]},
    ]
    tasks_json = _seed_plan_files(tmp_path, run_dir, items)
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"loaded 2 tasks\nResult: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_STOPPED} — missing capability", True),
        ("manual flag summary\nResult: flagged", True),
        ("issues\nResult: manual — task t2 blocked", True),
        ("manual review summary\nResult: manual review needed", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    outcomes = {(s.get(ITEM_KEY) or {}).get("id"): s.get(OUTCOME_KEY) for s in result[MAP_TASK_STATES_KEY]}
    assert outcomes["t1"] == OUTCOME_MANUAL_FLAG
    assert outcomes["t2"] == OUTCOME_BLOCKED
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_REVIEW


def test_map_fan_out_is_sequential_item_b_waits_for_item_a_to_finish(monkeypatch, build_graph, tmp_path):
    """Two independent (no dependency between them) items must still dispatch strictly one
    after another: by the moment item b's implement step is dispatched, item a's own success
    receipt must already exist on disk.
    """
    run_dir = tmp_path / "run"
    items = [
        {"id": "a", "title": "A", "description": "d", "dependencies": []},
        {"id": "b", "title": "B", "description": "d", "dependencies": []},
    ]
    tasks_json = _seed_plan_files(tmp_path, run_dir, items)
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    item_a_success_path = run_dir / "05_run_tasks" / "item-1" / STANDARD_TASK_SUCCESS_DIR / "attempt-1" / "output.md"
    seen_a_done_before_b_dispatch = {"value": None}

    script = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"loaded 2 tasks\nResult: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        ("Result: recap written", True),
    ]
    remaining = list(script)
    call_count = {"n": 0}

    def executor(argv, input_text, timeout):
        call_count["n"] += 1
        if call_count["n"] == 7:
            seen_a_done_before_b_dispatch["value"] = item_a_success_path.exists()
        content, ok = remaining.pop(0)
        _write_output(input_text, content)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": content}), stderr="")

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert seen_a_done_before_b_dispatch["value"] is True

def test_final_review_prompt_is_scoped_not_unfiltered(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"
    captured: list[str] = []
    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"loaded 0 tasks\nResult: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        ("Result: recap written", True),
    ]
    remaining = list(steps)

    def executor(argv, input_text, timeout):
        captured.append(input_text)
        content, ok = remaining.pop(0)
        _write_output(input_text, content)
        return subprocess.CompletedProcess(
            argv, 0 if ok else 1, stdout=json.dumps({"result": content}), stderr=""
        )

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    graph = build_graph()
    result = graph.invoke({RUN_DIR_KEY: str(run_dir)}, config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    final_text = next(t for t in captured if "Confirm nothing was skipped." in t)
    assert "unfiltered test suite" not in final_text
    assert "--name-only" in final_text or "name-only" in final_text
    assert "directly import" in final_text
    assert "test_scope" in final_text
    assert not final_text.lstrip().startswith("- ")
    assert final_text.index("Confirm nothing was skipped.") < final_text.index("Per-task outcomes")

