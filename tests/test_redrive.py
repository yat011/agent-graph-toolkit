"""Tests for cmd_redrive nested attempt_count reset.

A human resolving a gate's manual/exhausted halt via `agentgraph redrive` restarts the loop with
nested attempt_count fields reset to zero, landing back at the gate's loop-back target. Uses a
fake `_run_subprocess` (no real `claude` CLI calls).
"""

import argparse
import json
import subprocess
from pathlib import Path

from agentgraph_engine import cli as agentgraph_cli
from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    HALT_REASON_KEY,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_RETRIES_EXHAUSTED,
    IMPLEMENT_REQUIREMENTS_NODE,
    ITEM_KEY,
    OUTCOME_KEY,
    OUTCOME_SUCCESS,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RESULT_REJECT,
    REVIEW_NODE,
    RUN_DIR_KEY,
)
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module, resolve_graph_path
from agentgraph_engine.runs import open_checkpointer, run_dir_for, thread_config

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


def _standard_task_build_graph():
    return get_build_graph(load_graph_module(resolve_graph_path("standard-task")))


def test_redrive_resets_attempt_count_after_manual_resolution(monkeypatch, tmp_path):
    graph_name = "standard-task"
    run_id = "20260101T000000_demo"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_REJECT} — bad", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_REJECT} — bad", True),
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_REJECT} — bad", True),
                ("manual flag summary\nResult: flagged", True),
            ]
        ),
    )
    config = thread_config(run_id)
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        halted_state = compiled.invoke(
            {RUN_DIR_KEY: str(run_dir), ITEM_KEY: {"title": "t", "description": "d"}},
            config={**config, "recursion_limit": 50},
        )

    assert halted_state[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 3
    assert halted_state[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 3
    assert halted_state[HALTED_KEY] is True
    assert halted_state[HALT_REASON_KEY] == HALT_REJECT_ATTEMPTS_EXHAUSTED
    assert halted_state[HALTED_AT_NODE_KEY] == IMPLEMENT_REQUIREMENTS_NODE

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor([(f"Result: {RESULT_IMPLEMENTED}", True), (f"Result: {RESULT_ACCEPT}", True)]),
    )
    args = argparse.Namespace(run=str(run_dir), recursion_limit=50)
    exit_code = agentgraph_cli.cmd_redrive(args)
    assert exit_code == 0

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        final_state = compiled.get_state(config).values

    assert final_state[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert final_state[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 1
    assert final_state[REVIEW_NODE][ATTEMPT_COUNT_KEY] == 1
    assert final_state[HALTED_KEY] is False
    assert final_state[HALTED_AT_NODE_KEY] is None


def test_redrive_of_a_plain_technical_failure_does_not_reset_attempt_count(monkeypatch, tmp_path):
    """A `retries_exhausted` halt keeps its attempt_count: the node continues from where it was,
    not reset to zero the way a gate-manual halt's redrive is.
    """
    graph_name = "standard-task"
    run_id = "20260101T000000_technical"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_REJECT} — bad", True),
                (None, False),
                (None, False),
            ]
        ),
    )
    config = thread_config(run_id)
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        halted_state = compiled.invoke(
            {RUN_DIR_KEY: str(run_dir), ITEM_KEY: {"title": "t", "description": "d"}},
            config={**config, "recursion_limit": 50},
        )

    assert halted_state[HALTED_KEY] is True
    assert halted_state[HALT_REASON_KEY] == HALT_RETRIES_EXHAUSTED
    assert halted_state[HALTED_AT_NODE_KEY] == IMPLEMENT_REQUIREMENTS_NODE
    assert halted_state[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 2

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [(f"Result: {RESULT_IMPLEMENTED}", True), (f"Result: {RESULT_ACCEPT}", True)]
        ),
    )
    args = argparse.Namespace(run=str(run_dir), recursion_limit=50)
    exit_code = agentgraph_cli.cmd_redrive(args)
    assert exit_code == 0

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        final_state = compiled.get_state(config).values

    assert final_state[OUTCOME_KEY] == OUTCOME_SUCCESS
    assert final_state[IMPLEMENT_REQUIREMENTS_NODE][ATTEMPT_COUNT_KEY] == 2
