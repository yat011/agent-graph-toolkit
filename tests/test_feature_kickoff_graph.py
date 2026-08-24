"""Tests for the ported feature-kickoff graph.py: branch/planner/tech-review loop, load_tasks
env-check branch, sequential map/fan-out over standard-task with a dependency gate, and the
Python final-review additional-test runner. Uses a fake `_run_subprocess` (no real Worker CLI
calls), a fake `_run_git` so create_feature_branch cannot touch the real repo, and a fake
`_run_additional_test` so final_review does not execute host scripts.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    FINAL_REVIEW_NODE,
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
    RESULT_KEY,
    RESULT_REJECT,
    RESULT_STOPPED,
    RESULT_IMPLEMENTED,
    RETURNCODE_KEY,
    RUN_DIR_KEY,
    SPEC_PATH_KEY,
    STANDARD_TASK_SUCCESS_DIR,
    STDERR_KEY,
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
NODES_MODULE = "agentgraph_template__feature_kickoff.nodes"


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


def _fake_git(args: list[str]) -> subprocess.CompletedProcess:
    cmd = args[0] if args else ""
    if cmd == "rev-parse":
        return subprocess.CompletedProcess(["git", *args], 0, stdout="main\n", stderr="")
    if cmd == "status":
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
    if cmd == "show-ref":
        return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr="")
    if cmd == "checkout":
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
    raise AssertionError(f"unexpected git args: {args}")


def _fake_additional_test(script_path: Path) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["additional_test", str(script_path)], 0, stdout="", stderr="")


def _seed_additional_test(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    nodes = sys.modules[NODES_MODULE]
    path = nodes._additional_test_script_path(run_dir)
    if sys.platform == "win32":
        path.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    else:
        path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return path


@pytest.fixture
def build_graph(monkeypatch):
    module = load_graph_module(GRAPH_PATH)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_git", _fake_git)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", _fake_additional_test)
    return get_build_graph(module)


def _seed_plan_files(tmp_path, items: list) -> tuple[Path, Path]:
    """Seed spec + plan artifacts at the convention paths load_tasks reads."""
    specs_dir = tmp_path / "agent_works" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = specs_dir / "demo.md"
    spec_path.write_text("# demo\n", encoding="utf-8")
    plans_dir = tmp_path / "agent_works" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    tasks_json = plans_dir / "demo.tasks.json"
    tasks_json.write_text(json.dumps(items), encoding="utf-8")
    (plans_dir / "demo.md").write_text("# plan\n", encoding="utf-8")
    return tasks_json, spec_path


def _kickoff_state(run_dir: Path, spec_path: Path) -> dict:
    return {RUN_DIR_KEY: str(run_dir), SPEC_PATH_KEY: str(spec_path)}


def test_accepted_path_env_working_reaches_success(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)

    steps = [
        ("Result: plan written", True),
        (f"Result: {RESULT_ACCEPT}", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert result["planner_node"][ATTEMPT_COUNT_KEY] == 1
    assert json.loads((run_dir / "04_load_tasks" / "attempt-1" / "items.json").read_text(encoding="utf-8")) == []
    recap = (run_dir / "09_success" / "attempt-1" / "output.md").read_text(encoding="utf-8")
    assert "Result: recap written" in recap


def test_reject_loops_back_then_blocked_after_three_attempts(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    plan_line = "Result: plan written"

    steps = [
        (plan_line, True),
        (f"Result: {RESULT_REJECT} — missing test cases", True),
        (plan_line, True),
        (f"Result: {RESULT_REJECT} — still missing", True),
        (plan_line, True),
        (f"Result: {RESULT_REJECT} — still missing", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
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
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    plan_line = "Result: plan written"

    steps = [
        (plan_line, True),
        ("garbled, no recognizable keyword at all", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
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
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    plan_line = "Result: plan written"

    steps = [
        (plan_line, True),
        ("Result: manual — needs a human judgment call", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_BLOCKED
    assert result["planner_node"][ATTEMPT_COUNT_KEY] == 1
    assert result[HALT_REASON_KEY] == HALT_MANUAL_REQUESTED
    assert result[HALTED_AT_NODE_KEY] == PLANNER_NODE


def test_env_down_routes_to_needs_manual_review(monkeypatch, build_graph, tmp_path):
    """04_load_tasks has no reject/loop-back keyword pair: a missing tasks JSON routes to
    needs_manual_review immediately (no self-retry hop).
    """
    run_dir = tmp_path / "run"
    specs_dir = tmp_path / "agent_works" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = specs_dir / "demo.md"
    spec_path.write_text("# demo\n", encoding="utf-8")
    _seed_additional_test(run_dir)

    steps = [
        ("Result: plan written", True),
        (f"Result: {RESULT_ACCEPT}", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_REVIEW
    assert result[LOAD_TASKS_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[HALTED_KEY] is True
    assert result[HALTED_AT_NODE_KEY] == LOAD_TASKS_NODE
    assert (run_dir / "08_needs_manual_review" / "attempt-1" / "output.md").exists()


def test_sequential_fan_out_with_dependency_gate_and_final_review_passed(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    items = [
        {"id": "t1", "title": "First", "description": "d1", "dependencies": []},
        {"id": "t2", "title": "Second", "description": "d2", "dependencies": ["t1"]},
    ]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    plan_line = "Result: plan written"

    steps = [
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    outcomes = {(s.get(ITEM_KEY) or {}).get("id"): s.get(OUTCOME_KEY) for s in result[MAP_TASK_STATES_KEY]}
    assert outcomes == {"t1": OUTCOME_SUCCESS, "t2": OUTCOME_SUCCESS}


def test_dependency_on_manual_flagged_item_leaves_dependent_blocked(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    items = [
        {"id": "t1", "title": "First", "description": "d1", "dependencies": []},
        {"id": "t2", "title": "Second", "description": "d2", "dependencies": ["t1"]},
    ]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    plan_line = "Result: plan written"

    steps = [
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_STOPPED} — missing capability", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
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
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    plan_line = "Result: plan written"

    item_a_success_path = run_dir / "05_run_tasks" / "item-1" / STANDARD_TASK_SUCCESS_DIR / "attempt-1" / "output.md"
    seen_a_done_before_b_dispatch = {"value": None}

    script = [
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
    ]
    remaining = list(script)
    call_count = {"n": 0}

    def executor(argv, input_text, timeout):
        call_count["n"] += 1
        if call_count["n"] == 5:
            seen_a_done_before_b_dispatch["value"] = item_a_success_path.exists()
        content, ok = remaining.pop(0)
        _write_output(input_text, content)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": content}), stderr="")

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert seen_a_done_before_b_dispatch["value"] is True


def test_planner_prompt_asks_for_os_specific_additional_test_script(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)
    captured: list[str] = []
    steps = [
        ("Result: plan written", True),
        (f"Result: {RESULT_ACCEPT}", True),
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
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    nodes = sys.modules[NODES_MODULE]
    script_path = str(nodes._additional_test_script_path(run_dir))
    planner_text = next(t for t in captured if "Write the tech plan" in t)
    assert "Additional test script:" in planner_text
    assert script_path in planner_text
    assert nodes._additional_test_script_kind() in planner_text
    assert "Do not run the script" in planner_text
    review_text = next(t for t in captured if "Review the plan and tasks" in t)
    assert "existence only" in review_text
    assert "Do not review which tests it runs" in review_text
    assert script_path in review_text
    expected_name = "additional_test.cmd" if sys.platform == "win32" else "additional_test.sh"
    assert expected_name in script_path


def test_tech_review_rejects_accepted_plan_when_additional_test_script_missing(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    plan_line = "Result: plan written"
    steps = [
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
        (plan_line, True),
        (f"Result: {RESULT_ACCEPT}", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))
    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_BLOCKED
    assert result[PLANNER_NODE][ATTEMPT_COUNT_KEY] == 3
    assert result[HALT_REASON_KEY] == HALT_REJECT_ATTEMPTS_EXHAUSTED
    review_out = (run_dir / "03_tech_plan_reviewer" / "attempt-1" / "output.md").read_text(
        encoding="utf-8"
    )
    assert "additional_test script missing" in review_out


def test_final_review_runs_script_stores_stderr_and_succeeds(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    script_path = _seed_additional_test(run_dir)
    ran: list[Path] = []

    def runner(path: Path) -> subprocess.CompletedProcess:
        ran.append(path)
        return subprocess.CompletedProcess(
            ["additional_test", str(path)], 0, stdout="ok\n", stderr="warning: unused\n"
        )

    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", runner)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor([("Result: plan written", True), (f"Result: {RESULT_ACCEPT}", True)]),
    )
    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert ran == [script_path]
    record = result[FINAL_REVIEW_NODE]
    assert record[STDERR_KEY] == "warning: unused\n"
    assert record[RETURNCODE_KEY] == 0
    assert record[RESULT_KEY] == RESULT_ACCEPT
    stderr_file = run_dir / "06_final_review" / "attempt-1" / "stderr.txt"
    assert stderr_file.read_text(encoding="utf-8") == "warning: unused\n"
    output = (run_dir / "06_final_review" / "attempt-1" / "output.md").read_text(encoding="utf-8")
    assert "warning: unused" in output
    assert "Return code: 0" in output


def test_final_review_nonzero_exit_routes_to_manual_and_stores_stderr(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)

    def runner(path: Path) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            ["additional_test", str(path)], 1, stdout="", stderr="FAILED tests/test_foo.py\n"
        )

    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", runner)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor([("Result: plan written", True), (f"Result: {RESULT_ACCEPT}", True)]),
    )
    graph = build_graph()
    result = graph.invoke(_kickoff_state(run_dir, spec_path), config={"recursion_limit": 50})
    assert result[OUTCOME_KEY] == OUTCOME_MANUAL_REVIEW
    record = result[FINAL_REVIEW_NODE]
    assert record[STDERR_KEY] == "FAILED tests/test_foo.py\n"
    assert record[RETURNCODE_KEY] == 1
    assert "additional tests failed (exit 1)" in (record[RESULT_KEY] or "")
    stderr_file = run_dir / "06_final_review" / "attempt-1" / "stderr.txt"
    assert stderr_file.read_text(encoding="utf-8") == "FAILED tests/test_foo.py\n"


def test_additional_test_argv_is_os_specific():
    load_graph_module(GRAPH_PATH)
    nodes = sys.modules[NODES_MODULE]
    script = Path("run") / nodes._additional_test_script_name()
    argv = nodes._additional_test_argv(script)
    if sys.platform == "win32":
        assert argv == ["cmd", "/c", str(script)]
        assert script.name == "additional_test.cmd"
    else:
        assert argv[0] in {"bash", "sh"} or argv[0].endswith(("bash", "sh"))
        assert str(script) in argv
        assert script.name == "additional_test.sh"

