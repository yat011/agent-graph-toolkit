"""Tests for the shared gate router's redrive/reset behavior (agentgraph_engine.cli.cmd_redrive
+ agentgraph_engine.routing): a human resolving a gate's manual/exhausted halt via `agentgraph
redrive` restarts the loop with its attempt/self-retry counters reset to zero, landing back at
the gate's loop-back target -- never continuing the old count. Uses a fake `_run_subprocess`
(no real `claude` CLI calls), same seam as the other graph tests.
"""

import argparse
import json
import subprocess
from pathlib import Path

from agentgraph_engine import cli as agentgraph_cli
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

    # Drive 03_review's gate to reject-exhausted (3 implement/reject cycles) -> manual_flag,
    # halted, halted_at_node == "implement_requirements" (review's loop-back target).
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: implemented", True),
                ("Result: rejected — bad", True),
                ("Result: implemented", True),
                ("Result: rejected — bad", True),
                ("Result: implemented", True),
                ("Result: rejected — bad", True),
                ("manual flag summary\nResult: flagged", True),
            ]
        ),
    )
    config = thread_config(run_id)
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        halted_state = compiled.invoke(
            {"run_dir": str(run_dir), "item": {"title": "t", "description": "d"}},
            config={**config, "recursion_limit": 50},
        )

    assert halted_state["implement_attempt_count"] == 3
    assert halted_state["review_attempt_count"] == 3
    assert halted_state["halted"] is True
    assert halted_state["halt_reason"] == "reject_attempts_exhausted"
    assert halted_state["halted_at_node"] == "implement_requirements"

    # A human "fixes the real problem" and redrives: fresh implement -> accepted -> success.
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor([("Result: implemented", True), ("Result: accepted", True)]),
    )
    args = argparse.Namespace(run=str(run_dir), recursion_limit=50)
    exit_code = agentgraph_cli.cmd_redrive(args)
    assert exit_code == 0

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        final_state = compiled.get_state(config).values

    assert final_state["outcome"] == "success"
    # The redriven loop restarted clean at zero -- one fresh implement/review pass, not a
    # continuation of the exhausted count (which would read 4 here, not 1).
    assert final_state["implement_attempt_count"] == 1
    assert final_state["review_attempt_count"] == 1
    assert final_state["halted"] is False
    assert final_state["halted_at_node"] is None


def test_redrive_of_a_plain_technical_failure_does_not_reset_attempt_count(monkeypatch, tmp_path):
    """A `retries_exhausted` halt (an ordinary CLI dispatch failure, unrelated to the gate-manual
    mechanism) keeps its pre-existing redrive behavior exactly: the node's attempt count is left
    alone, continuing from where it was -- not reset to zero the way a gate-manual halt's redrive
    is. Sets up a SECOND visit to implement_requirements (after one accepted-reject loop) so its
    attempt count is non-zero going into the technical failure, making "continues" vs. "reset"
    actually observable.
    """
    graph_name = "standard-task"
    run_id = "20260101T000000_technical"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: implemented", True),  # implement attempt 1
                ("Result: rejected — bad", True),  # review attempt 1 -> loop back
                (None, False),  # implement attempt 2, dispatch_with_retry try 1/2 -> fails
                (None, False),  # implement attempt 2, dispatch_with_retry try 2/2 -> fails -> halt
            ]
        ),
    )
    config = thread_config(run_id)
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        halted_state = compiled.invoke(
            {"run_dir": str(run_dir), "item": {"title": "t", "description": "d"}},
            config={**config, "recursion_limit": 50},
        )

    assert halted_state["halted"] is True
    assert halted_state["halt_reason"] == "retries_exhausted"
    assert halted_state["halted_at_node"] == "implement_requirements"
    assert halted_state["implement_attempt_count"] == 2

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor([("Result: verified", True)]),
    )
    args = argparse.Namespace(run=str(run_dir), recursion_limit=50)
    exit_code = agentgraph_cli.cmd_redrive(args)
    assert exit_code == 0

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _standard_task_build_graph()(checkpointer=cp)
        final_state = compiled.get_state(config).values

    assert final_state["outcome"] == "success"
    # Continues the count (1 -> 2), never reset to 0 -- this is the pre-existing technical-halt
    # redrive path, untouched by the new gate-manual reset-on-redrive behavior.
    assert final_state["implement_attempt_count"] == 2
