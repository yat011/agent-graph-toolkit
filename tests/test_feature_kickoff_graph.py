"""Tests for the ported feature-kickoff graph.py: branch/planner/tech-review loop, load_phases
env-check branch, sequential map/fan-out over a compiled standard-phase subgraph, additional_test
(+ one integration_fix), and the final-reviewer agent. Pause paths compile with InMemorySaver.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentgraph_engine.constants import (
    ADDITIONAL_TEST_NODE,
    ATTEMPT_COUNT_KEY,
    FINAL_REVIEWER_NODE,
    HALT_MANUAL_REQUESTED,
    HALT_REASON_KEY,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_RETRIES_EXHAUSTED,
    HALT_UNRECOGNIZED_RESULT,
    HALTED_KEY,
    IMPLEMENT_REQUIREMENTS_NODE,
    INTEGRATION_FIX_NODE,
    ITEM_KEY,
    LOAD_PHASES_NODE,
    MAP_PHASE_STATES_KEY,
    OUTCOME_BLOCKED,
    OUTCOME_KEY,
    OUTCOME_SUCCESS,
    PLANNER_NODE,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RESULT_KEY,
    RESULT_REJECT,
    RESULT_STOPPED,
    RETURNCODE_KEY,
    RUN_DIR_KEY,
    RUN_ONE_PHASE_NODE,
    SPEC_PATH_KEY,
    STANDARD_TASK_SUCCESS_DIR,
    STDERR_KEY,
    STDOUT_KEY,
    TECH_PLAN_REVIEWER_NODE,
)
from agentgraph_engine.dispatch import OUTPUT_PATH_LINE_PREFIX
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.pause import (
    INTERRUPT_CHECKPOINT_NS_KEY,
    INTERRUPT_PARENT_NODE_KEY,
    INTERRUPT_REASON_KEY,
    INTERRUPT_REDRIVE_NODE_KEY,
    INTERRUPT_RESET_ATTEMPTS_KEY,
    interrupt_payload_from_result,
)

GRAPH_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "agentgraph-run-graph"
    / "templates"
    / "feature-kickoff"
    / "graph.py"
)

MARKER = OUTPUT_PATH_LINE_PREFIX
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
    if cmd == "merge-base":
        return subprocess.CompletedProcess(["git", *args], 0, stdout="abc123\n", stderr="")
    if cmd == "diff":
        return subprocess.CompletedProcess(["git", *args], 0, stdout="foo.py\n", stderr="")
    if cmd == "log":
        return subprocess.CompletedProcess(["git", *args], 0, stdout="abc123 message\n", stderr="")
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
    monkeypatch.setattr(
        "agentgraph_engine.review_policy.commit_dirty_tree",
        lambda message, **kwargs: False,
    )
    module = load_graph_module(GRAPH_PATH)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_git", _fake_git)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", _fake_additional_test)
    return get_build_graph(module)


def _compile(build_graph):
    return build_graph(checkpointer=InMemorySaver())


def _cfg(thread: str = "t") -> dict:
    return {"configurable": {"thread_id": thread}, "recursion_limit": 80}


def _assert_paused(result, *, reason: str, redrive: str, reset: bool) -> dict:
    payload = interrupt_payload_from_result(result)
    assert payload is not None
    assert payload[INTERRUPT_REASON_KEY] == reason
    assert payload[INTERRUPT_REDRIVE_NODE_KEY] == redrive
    assert payload[INTERRUPT_RESET_ATTEMPTS_KEY] is reset
    assert result.get(OUTCOME_KEY) is None
    return payload


def _seed_plan_files(tmp_path, items: list) -> tuple[Path, Path]:
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


def _phase(
    item_id: str,
    title: str,
    description: str,
    dependencies: list | None = None,
    review: str = "always",
) -> dict:
    return {
        "id": item_id,
        "title": title,
        "description": description,
        "dependencies": list(dependencies or []),
        "review": review,
    }


def test_accepted_path_env_working_reaches_success(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert result[PLANNER_NODE][ATTEMPT_COUNT_KEY] == 1
    assert json.loads((run_dir / "04_load_phases" / "attempt-1" / "items.json").read_text(encoding="utf-8")) == []
    recap = (run_dir / "09_success" / "attempt-1" / "output.md").read_text(encoding="utf-8")
    assert "Result: recap written" in recap


def test_reject_three_times_pauses_with_redrive_planner(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    plan_line = "Result: plan written"
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                (plan_line, True),
                (f"Result: {RESULT_REJECT} — missing test cases", True),
                (plan_line, True),
                (f"Result: {RESULT_REJECT} — still missing", True),
                (plan_line, True),
                (f"Result: {RESULT_REJECT} — still missing", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_REJECT_ATTEMPTS_EXHAUSTED,
        redrive=PLANNER_NODE,
        reset=True,
    )
    assert result[PLANNER_NODE][ATTEMPT_COUNT_KEY] == 3
    assert result.get(HALTED_KEY) is True
    assert not (run_dir / "07_blocked_plan_rejected").exists()


def test_unrecognized_tech_review_result_pauses_immediately(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                ("garbled, no recognizable keyword at all", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_UNRECOGNIZED_RESULT,
        redrive=TECH_PLAN_REVIEWER_NODE,
        reset=True,
    )
    assert result[PLANNER_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[TECH_PLAN_REVIEWER_NODE][ATTEMPT_COUNT_KEY] == 1


def test_manual_keyword_from_tech_review_pauses_immediately(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                ("Result: manual — needs a human judgment call", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=TECH_PLAN_REVIEWER_NODE,
        reset=True,
    )
    assert result[PLANNER_NODE][ATTEMPT_COUNT_KEY] == 1


def test_env_down_pauses_with_redrive_load_phases(monkeypatch, build_graph, tmp_path):
    """04_load_phases has no reject/loop-back pair: a missing tasks JSON pauses immediately."""
    run_dir = tmp_path / "run"
    specs_dir = tmp_path / "agent_works" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = specs_dir / "demo.md"
    spec_path.write_text("# demo\n", encoding="utf-8")
    _seed_additional_test(run_dir)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=LOAD_PHASES_NODE,
        reset=True,
    )
    assert result[LOAD_PHASES_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result.get(HALTED_KEY) is True
    assert not (run_dir / "08_needs_manual_review").exists()


def test_sequential_fan_out_with_dependency_gate_and_final_review_passed(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    items = [
        _phase("t1", "First", "d1"),
        _phase("t2", "Second", "d2", ["t1"]),
    ]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    outcomes = {(s.get(ITEM_KEY) or {}).get("id"): s.get(OUTCOME_KEY) for s in result[MAP_PHASE_STATES_KEY]}
    assert outcomes == {"t1": OUTCOME_SUCCESS, "t2": OUTCOME_SUCCESS}


def test_item_one_stopped_interrupts_before_item_two(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    items = [
        _phase("t1", "First", "d1"),
        _phase("t2", "Second", "d2", ["t1"]),
    ]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_STOPPED} — missing capability", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    payload = _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=IMPLEMENT_REQUIREMENTS_NODE,
        reset=True,
    )
    assert payload[INTERRUPT_PARENT_NODE_KEY] == RUN_ONE_PHASE_NODE
    assert payload[INTERRUPT_CHECKPOINT_NS_KEY] == "item-1"
    item2 = run_dir / "05_run_phases" / "item-2"
    assert not (item2 / "02_implement_requirements").exists()
    assert FINAL_REVIEWER_NODE not in result
    assert not (run_dir / "06_additional_test").exists()
    assert not (run_dir / "08_needs_manual_review").exists()



def test_item_one_redrive_still_stopped_does_not_start_item_two(
    monkeypatch, build_graph, tmp_path
):
    """A nested pause that is still stopped after redrive must interrupt again, not start item-2."""
    run_dir = tmp_path / "run"
    items = [
        _phase("t1", "First", "d1"),
        _phase("t2", "Second", "d2", ["t1"]),
    ]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_STOPPED} — missing capability", True),
            ]
        ),
    )
    compiled = build_graph(checkpointer=InMemorySaver())
    cfg = _cfg()
    first = compiled.invoke(_kickoff_state(run_dir, spec_path), config=cfg)
    _assert_paused(
        first,
        reason=HALT_MANUAL_REQUESTED,
        redrive=IMPLEMENT_REQUIREMENTS_NODE,
        reset=True,
    )
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                (f"Result: {RESULT_STOPPED} — still missing", True),
            ]
        ),
    )
    second = compiled.invoke(Command(resume="redrive"), config=cfg)
    payload = _assert_paused(
        second,
        reason=HALT_MANUAL_REQUESTED,
        redrive=IMPLEMENT_REQUIREMENTS_NODE,
        reset=True,
    )
    assert payload[INTERRUPT_PARENT_NODE_KEY] == RUN_ONE_PHASE_NODE
    item2 = run_dir / "05_run_phases" / "item-2"
    assert not item2.exists()
    outcomes = {
        (s.get(ITEM_KEY) or {}).get("id"): s.get(OUTCOME_KEY)
        for s in (second.get(MAP_PHASE_STATES_KEY) or [])
    }
    assert "t1" not in outcomes or outcomes.get("t1") != OUTCOME_SUCCESS
    assert "t2" not in outcomes


def test_map_fan_out_is_sequential_item_b_waits_for_item_a_to_finish(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    items = [
        _phase("a", "A", "d"),
        _phase("b", "B", "d"),
    ]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    item_a_success_path = (
        run_dir / "05_run_phases" / "item-1" / STANDARD_TASK_SUCCESS_DIR / "attempt-1" / "output.md"
    )
    seen_a_done_before_b_dispatch = {"value": None}
    script = [
        ("Result: plan written", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_IMPLEMENTED}", True),
        (f"Result: {RESULT_ACCEPT}", True),
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
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert seen_a_done_before_b_dispatch["value"] is True


def test_planner_prompt_asks_for_os_specific_additional_test_script(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)
    captured: list[str] = []
    remaining = [
        ("Result: plan written", True),
        (f"Result: {RESULT_ACCEPT}", True),
        (f"Result: {RESULT_ACCEPT}", True),
    ]

    def executor(argv, input_text, timeout):
        captured.append(input_text)
        content, ok = remaining.pop(0)
        _write_output(input_text, content)
        return subprocess.CompletedProcess(
            argv, 0 if ok else 1, stdout=json.dumps({"result": content}), stderr=""
        )

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", executor)
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    nodes = sys.modules[NODES_MODULE]
    script_path = str(nodes._additional_test_script_path(run_dir))
    planner_text = next(t for t in captured if "Write plan, phases JSON, and additional-test" in t)
    assert "additional_test_script:" in planner_text
    assert script_path in planner_text
    assert nodes._additional_test_script_kind() in planner_text
    assert "never a directory" in planner_text
    assert "repository root as cwd" in planner_text
    assert f"additional_test_script: {script_path}" in planner_text
    assert "final-reviewer agent" in planner_text
    assert "do not set review: always merely because" in planner_text.lower()
    review_text = next(t for t in captured if "not spec's own decisions" in t)
    assert script_path in review_text
    expected_name = "additional_test.cmd" if sys.platform == "win32" else "additional_test.sh"
    assert expected_name in script_path


def test_tech_review_rejects_accepted_plan_when_additional_test_script_missing(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    plan_line = "Result: plan written"
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                (plan_line, True),
                (f"Result: {RESULT_ACCEPT}", True),
                (plan_line, True),
                (f"Result: {RESULT_ACCEPT}", True),
                (plan_line, True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_REJECT_ATTEMPTS_EXHAUSTED,
        redrive=PLANNER_NODE,
        reset=True,
    )
    assert result[PLANNER_NODE][ATTEMPT_COUNT_KEY] == 3
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
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert ran == [script_path]
    record = result[ADDITIONAL_TEST_NODE]
    assert record[STDOUT_KEY] == "ok\n"
    assert record[STDERR_KEY] == "warning: unused\n"
    assert record[RETURNCODE_KEY] == 0
    assert record[RESULT_KEY] == RESULT_ACCEPT
    attempt_dir = run_dir / "06_additional_test" / "attempt-1"
    assert (attempt_dir / "stdout.txt").read_text(encoding="utf-8") == "ok\n"
    assert (attempt_dir / "stderr.txt").read_text(encoding="utf-8") == "warning: unused\n"
    output = (attempt_dir / "output.md").read_text(encoding="utf-8")
    assert "ok" in output
    assert "warning: unused" in output
    assert "Return code: 0" in output
    assert result[FINAL_REVIEWER_NODE][RESULT_KEY] == RESULT_ACCEPT


def test_additional_test_fail_one_fix_then_green_reaches_final_reviewer(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)
    tap_stdout = "Cannot find module '...\\\\scripts\\\\test'\n✖ agent_works\\scripts\\test\n"
    runs = {"n": 0}

    def runner(path: Path) -> subprocess.CompletedProcess:
        runs["n"] += 1
        if runs["n"] == 1:
            return subprocess.CompletedProcess(
                ["additional_test", str(path)], 1, stdout=tap_stdout, stderr=""
            )
        return subprocess.CompletedProcess(
            ["additional_test", str(path)], 0, stdout="ok\n", stderr=""
        )

    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", runner)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert runs["n"] == 2
    assert result[INTEGRATION_FIX_NODE][ATTEMPT_COUNT_KEY] == 1
    first = run_dir / "06_additional_test" / "attempt-1"
    assert (first / "stdout.txt").read_text(encoding="utf-8") == tap_stdout
    assert "Cannot find module" in (first / "output.md").read_text(encoding="utf-8")
    assert result[ADDITIONAL_TEST_NODE][ATTEMPT_COUNT_KEY] == 2
    assert result[FINAL_REVIEWER_NODE][RESULT_KEY] == RESULT_ACCEPT


def test_additional_test_fail_after_one_fix_pauses(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)
    tap_stdout = "still failing\n"

    def runner(path: Path) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            ["additional_test", str(path)], 1, stdout=tap_stdout, stderr=""
        )

    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", runner)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_REJECT_ATTEMPTS_EXHAUSTED,
        redrive=INTEGRATION_FIX_NODE,
        reset=True,
    )
    assert result[INTEGRATION_FIX_NODE][ATTEMPT_COUNT_KEY] == 1
    assert result[ADDITIONAL_TEST_NODE][ATTEMPT_COUNT_KEY] == 2
    assert "additional tests failed (exit 1)" in (result[ADDITIONAL_TEST_NODE][RESULT_KEY] or "")


def test_final_reviewer_reject_pauses_without_integration_fix(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_REJECT} — seam broken", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=FINAL_REVIEWER_NODE,
        reset=True,
    )
    assert INTEGRATION_FIX_NODE not in result or not (
        result.get(INTEGRATION_FIX_NODE) or {}
    ).get(ATTEMPT_COUNT_KEY)
    assert result[FINAL_REVIEWER_NODE][ATTEMPT_COUNT_KEY] == 1


def _assert_no_integration_fix(result) -> None:
    fix = result.get(INTEGRATION_FIX_NODE) or {}
    assert not fix.get(ATTEMPT_COUNT_KEY)
    reviewer = result.get(FINAL_REVIEWER_NODE) or {}
    assert not reviewer.get(ATTEMPT_COUNT_KEY)


def test_additional_test_missing_script_pauses_without_fix(monkeypatch, build_graph, tmp_path):
    """Tech-plan-reviewer requires the script; additional_test must still pause if it vanished after."""
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    seeded = _seed_additional_test(run_dir)
    nodes = sys.modules[NODES_MODULE]
    real_path = nodes._additional_test_script_path
    ran = {"n": 0}

    def path_for(rd: Path) -> Path:
        if (rd / "04_load_phases").exists():
            return rd / "missing_additional_test.cmd"
        return real_path(rd)

    def runner(path: Path) -> subprocess.CompletedProcess:
        ran["n"] += 1
        raise AssertionError("suite must not run when the script file is missing")

    monkeypatch.setattr(nodes, "_additional_test_script_path", path_for)
    monkeypatch.setattr(nodes, "_run_additional_test", runner)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=ADDITIONAL_TEST_NODE,
        reset=True,
    )
    _assert_no_integration_fix(result)
    assert ran["n"] == 0
    assert "additional_test script missing" in (result[ADDITIONAL_TEST_NODE][RESULT_KEY] or "")
    assert seeded.is_file()


def test_additional_test_incomplete_phases_pauses_without_fix(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    items = [_phase("t1", "Blocked", "d1", ["no-such-phase"])]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    ran = {"n": 0}

    def runner(path: Path) -> subprocess.CompletedProcess:
        ran["n"] += 1
        raise AssertionError("suite must not run when phases are incomplete")

    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", runner)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=ADDITIONAL_TEST_NODE,
        reset=True,
    )
    _assert_no_integration_fix(result)
    assert ran["n"] == 0
    assert "incomplete phases: t1 (blocked)" in (result[ADDITIONAL_TEST_NODE][RESULT_KEY] or "")
    outcomes = {
        (s.get(ITEM_KEY) or {}).get("id"): s.get(OUTCOME_KEY)
        for s in result[MAP_PHASE_STATES_KEY]
    }
    assert outcomes == {"t1": OUTCOME_BLOCKED}


def test_additional_test_launch_file_not_found_pauses_without_fix(
    monkeypatch, build_graph, tmp_path
):
    run_dir = tmp_path / "run"
    _tasks_json, spec_path = _seed_plan_files(tmp_path, [])
    _seed_additional_test(run_dir)

    def runner(path: Path) -> subprocess.CompletedProcess:
        raise FileNotFoundError("launcher missing")

    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", runner)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    _assert_paused(
        result,
        reason=HALT_MANUAL_REQUESTED,
        redrive=ADDITIONAL_TEST_NODE,
        reset=True,
    )
    _assert_no_integration_fix(result)
    record = result[ADDITIONAL_TEST_NODE]
    assert record[RETURNCODE_KEY] == 127
    assert "failed to launch additional_test script" in (record[RESULT_KEY] or "")
    assert "launcher missing" in (record[STDERR_KEY] or "")


def test_final_reviewer_prompt_lists_handoffs_not_reviewer_corpus(
    monkeypatch, build_graph, tmp_path
):
    load_graph_module(GRAPH_PATH)
    nodes = sys.modules[NODES_MODULE]
    run_dir = tmp_path / "run"
    item_dir = run_dir / "05_run_phases" / "item-1"
    item_dir.mkdir(parents=True)
    handoff = item_dir / "handoff.md"
    handoff.write_text("## Decisions\n- keep names\n", encoding="utf-8")
    spec = tmp_path / "spec.md"
    spec.write_text("# spec\n", encoding="utf-8")
    state = {
        RUN_DIR_KEY: str(run_dir),
        SPEC_PATH_KEY: str(spec),
        MAP_PHASE_STATES_KEY: [
            {
                ITEM_KEY: {"id": "1", "title": "Core"},
                OUTCOME_KEY: OUTCOME_SUCCESS,
                RUN_DIR_KEY: str(item_dir),
                "review_node": {ATTEMPT_COUNT_KEY: 1},
            }
        ],
        ADDITIONAL_TEST_NODE: {STDOUT_KEY: "12 passed\n", RETURNCODE_KEY: 0, STDERR_KEY: ""},
    }
    text = nodes._final_reviewer_prompt(state)
    assert str(handoff) in text
    assert "12 passed" in text
    assert "Do not open phase implementer output.md" in text
    assert "Do not re-run the unfiltered suite" in text
    assert "phase-reviewed" in text
    assert "abc123" in text


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


def test_review_never_phase_skips_nested_reviewer(monkeypatch, build_graph, tmp_path):
    run_dir = tmp_path / "run"
    items = [
        _phase("t1", "First", "d1", review="always"),
        _phase("t2", "Second", "d2", ["t1"], review="never"),
    ]
    _tasks_json, spec_path = _seed_plan_files(tmp_path, items)
    _seed_additional_test(run_dir)
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    result = _compile(build_graph).invoke(_kickoff_state(run_dir, spec_path), config=_cfg())
    assert result[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert (run_dir / "05_run_phases" / "item-2" / "03_skip_review_commit").exists()
    assert not (run_dir / "05_run_phases" / "item-2" / "03_review").exists()
    assert (run_dir / "05_run_phases" / "item-1" / "03_review").exists()
