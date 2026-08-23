"""Tests for the ported feature-kickoff graph.py: branch/planner/tech-review loop, load_tasks
env-check branch, sequential map/fan-out over standard-task with a dependency gate, and the
final-review passed/issues branch. Uses a fake `_run_subprocess` (no real `claude` CLI calls).
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


def _seed_plan_files(tmp_path, run_dir: Path, items: list) -> None:
    """Seed the plan .md + .tasks.json files 02_planner's output.md points at, since the fake
    executor doesn't actually run a planner LLM that would create these itself.
    """
    plans_dir = tmp_path / "agent_works" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    tasks_json = plans_dir / "demo.tasks.json"
    tasks_json.write_text(json.dumps(items), encoding="utf-8")
    return tasks_json


def test_approve_path_env_working_reaches_success(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])

    steps = [
        ("branch ready\nResult: branch ready", True),
        (f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written", True),
        ("Result: Approve", True),
        ("loaded 0 tasks\nResult: environment working", True),
        ("passed\nResult: passed", True),
        ("Result: recap written", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    assert result["outcome"] == "success"
    assert result["planner_attempt_count"] == 1


def test_reject_loops_back_then_blocked_after_three_attempts(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        ("Result: Reject — missing test cases", True),
        (plan_line, True),
        ("Result: Reject — still missing", True),
        (plan_line, True),
        ("Result: Reject — still missing", True),
        ("blocked\nResult: blocked", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    assert result["outcome"] == "blocked"
    assert result["planner_attempt_count"] == 3
    # Reject-exhausted is a redrive-able halt: a human resolving it restarts the loop at
    # 02_planner (the gate's loop-back target), attempt counts reset to zero on redrive.
    assert result["halted"] is True
    assert result["halted_at_node"] == "planner"
    assert result["halt_reason"] == "reject_attempts_exhausted"


def test_unrecognized_tech_review_result_falls_to_blocked_not_loop_back(monkeypatch, build_graph, tmp_path):
    """The bug this router replaces: `route_after_tech_review` used to check only the accept
    keyword, so ANY other Result: line -- including a garbled/unrecognized one, not just a real
    `Reject` -- fell straight into the attempt-count check and silently looped back to 02_planner.
    A garbled line on attempt 1 must now fall toward blocked_plan_rejected (03_tech_plan_reviewer's
    own self-retry budget is 0, mirroring its `dispatch_with_retry(retry=0)`, so there's no
    self-retry chance) -- crucially NOT loop back to 02_planner, and NOT wait for 3 attempts.
    """
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        ("garbled, no recognizable keyword at all", True),  # tech_plan_reviewer: unrecognized
        ("blocked\nResult: blocked", True),  # blocked_plan_rejected
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    assert result["outcome"] == "blocked"
    # planner was dispatched exactly once -- proof this never looped back to it.
    assert result["planner_attempt_count"] == 1
    assert result["tech_review_attempt_count"] == 1
    assert result["halt_reason"] == "unrecognized_result_exhausted"
    assert result["halted_at_node"] == "planner"


def test_manual_keyword_from_tech_review_routes_immediately_to_blocked(monkeypatch, build_graph, tmp_path):
    """The reserved `manual` Result: keyword bypasses the attempt-count check entirely -- reaches
    blocked_plan_rejected on the very first tech-review dispatch, not after 3 rejected attempts.
    """
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        ("Result: manual — needs a human judgment call", True),  # tech_plan_reviewer
        ("blocked\nResult: blocked", True),  # blocked_plan_rejected
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    assert result["outcome"] == "blocked"
    assert result["planner_attempt_count"] == 1
    assert result["halt_reason"] == "manual_requested"
    assert result["halted_at_node"] == "planner"


def test_env_down_routes_to_needs_manual_review(monkeypatch, build_graph, tmp_path):
    """04_load_tasks has no reject/loop-back keyword pair (degenerate gate): "environment not
    working" is unrecognized from the router's perspective, so it gets one self-retry (this
    node's own `dispatch_with_retry(retry=1)` budget) before falling to needs_manual_review.
    """
    run_dir = tmp_path / "run"
    tasks_json = _seed_plan_files(tmp_path, run_dir, [])
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    steps = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        ("Result: Approve", True),
        ("env down\nResult: environment not working", True),  # 1st attempt -> self-retry
        ("env down\nResult: environment not working", True),  # 2nd attempt -> budget exhausted
        ("manual review needed\nResult: manual review needed", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    assert result["outcome"] == "manual_review"
    assert result["load_tasks_self_retry_count"] == 1
    assert result["halted"] is True
    assert result["halted_at_node"] == "load_tasks"


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
        ("Result: Approve", True),
        ("loaded 2 tasks\nResult: environment working", True),
        # item t1: standard-task -> implement (verified, skip review)
        ("Result: verified", True),
        # item t2: standard-task -> implement (verified, skip review)
        ("Result: verified", True),
        ("passed\nResult: passed", True),
        ("Result: recap written", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    assert result["outcome"] == "success"
    outcomes = {o["id"]: o["outcome"] for o in result["task_outcomes"]}
    assert outcomes == {"t1": "success", "t2": "success"}


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
        ("Result: Approve", True),
        ("loaded 2 tasks\nResult: environment working", True),
        # item t1: stopped -> manual_flag (dispatch code-writer then general-purpose flag write)
        ("Result: stopped — missing capability", True),
        ("manual flag summary\nResult: flagged", True),
        # t2 never dispatched (permanently blocked by t1's manual_flag outcome)
        ("issues\nResult: needs manual review — task t2 blocked", True),
        ("manual review summary\nResult: manual review needed", True),
    ]
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _script_executor(steps))

    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    outcomes = {o["id"]: o["outcome"] for o in result["task_outcomes"]}
    assert outcomes["t1"] == "manual_flag"
    assert outcomes["t2"] == "blocked"
    assert result["outcome"] == "manual_review"


def test_map_fan_out_is_sequential_item_b_waits_for_item_a_to_finish(monkeypatch, build_graph, tmp_path):
    """Two independent (no dependency between them) items must still dispatch strictly one
    after another: by the moment item b's implement step is dispatched, item a's own success
    receipt must already exist on disk — a concurrent/interleaved implementation could dispatch
    b before a's terminal file is written.
    """
    run_dir = tmp_path / "run"
    items = [
        {"id": "a", "title": "A", "description": "d", "dependencies": []},
        {"id": "b", "title": "B", "description": "d", "dependencies": []},
    ]
    tasks_json = _seed_plan_files(tmp_path, run_dir, items)
    plan_line = f"agent_works/plans/demo.md\n{tasks_json}\nResult: plan written"

    item_a_success_path = run_dir / "05_run_tasks" / "item-1" / "04_success" / "attempt-1" / "output.md"
    seen_a_done_before_b_dispatch = {"value": None}

    script = [
        ("branch ready\nResult: branch ready", True),
        (plan_line, True),
        ("Result: Approve", True),
        ("loaded 2 tasks\nResult: environment working", True),
        ("Result: verified", True),  # item a implement
        ("Result: verified", True),  # item b implement
        ("passed\nResult: passed", True),
        ("Result: recap written", True),
    ]
    remaining = list(script)
    call_count = {"n": 0}

    def executor(argv, input_text, timeout):
        call_count["n"] += 1
        # Call #6 is item b's implement dispatch (1=branch, 2=plan, 3=approve, 4=load_tasks,
        # 5=item a implement, 6=item b implement) — check item a's terminal artifact already
        # exists on disk at that exact moment.
        if call_count["n"] == 6:
            seen_a_done_before_b_dispatch["value"] = item_a_success_path.exists()
        content, ok = remaining.pop(0)
        _write_output(input_text, content)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": content}), stderr="")

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    graph = build_graph()
    result = graph.invoke({"run_dir": str(run_dir)}, config={"recursion_limit": 50})
    assert result["outcome"] == "success"
    assert seen_a_done_before_b_dispatch["value"] is True
