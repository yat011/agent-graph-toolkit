"""Nested feature-kickoff redrive must resume the child standard-task thread.

A paused map item (e.g. item-1 implement) is a *child* thread `{run_id}:item-N`.
`agentgraph redrive` after a new process / new SqliteSaver must Command(resume) that
child, not END the parent and not restart the map at Task 1.

In-memory same-process coverage lives in test_item_one_redrive_still_stopped.
This file is the CLI / new-process seam that that test cannot catch.

Pause resets attempt_count, so a redriven implement may overwrite attempt-1/output.md.
Dispatch count / output.md body are the seam, not the number of attempt folders.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentgraph_engine import cli as agentgraph_cli
from agentgraph_engine.constants import (
    HALT_MANUAL_REQUESTED,
    HALT_RETRIES_EXHAUSTED,
    IMPLEMENT_REQUIREMENTS_NODE,
    OUTCOME_KEY,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RESULT_STOPPED,
    RUN_ONE_TASK_NODE,
)
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.pause import (
    INTERRUPT_CHECKPOINT_NS_KEY,
    INTERRUPT_PARENT_NODE_KEY,
    INTERRUPT_REDRIVE_NODE_KEY,
    interrupt_payload_from_result,
    interrupt_payload_from_snapshot,
)
from agentgraph_engine.runs import open_checkpointer, run_dir_for, thread_config

from tests.test_feature_kickoff_graph import (
    GRAPH_PATH,
    NODES_MODULE,
    _assert_paused,
    _fake_additional_test,
    _fake_git,
    _kickoff_state,
    _script_executor,
    _seed_additional_test,
    _seed_plan_files,
)


def _two_items() -> list[dict]:
    return [
        {"id": "t1", "title": "First", "description": "d1", "dependencies": []},
        {"id": "t2", "title": "Second", "description": "d2", "dependencies": ["t1"]},
    ]


def _patch_kickoff_nodes(monkeypatch) -> None:
    load_graph_module(GRAPH_PATH)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_git", _fake_git)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", _fake_additional_test)


def _kickoff_build_graph():
    return get_build_graph(load_graph_module(GRAPH_PATH))


def _implement_output(run_dir: Path) -> Path:
    return run_dir / "05_run_tasks" / "item-1" / "02_implement_requirements" / "attempt-1" / "output.md"


def _counting_executor(steps):
    inner = _script_executor(steps)
    calls = {"n": 0}

    def executor(argv, input_text, timeout):
        calls["n"] += 1
        return inner(argv, input_text, timeout)

    return executor, calls


def _cmd_redrive_args(run_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(run=str(run_dir), recursion_limit=80, message=None, cli=None)


def test_sqlite_new_process_nested_redrive_resumes_child_implement(monkeypatch, tmp_path):
    """Close the checkpointer, rebuild the graph, cmd_redrive: child implement runs again."""
    _patch_kickoff_nodes(monkeypatch)
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_nested-sqlite-redrive"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)
    _tasks_json, spec_path = _seed_plan_files(tmp_path, _two_items())
    _seed_additional_test(run_dir)
    config = {**thread_config(run_id), "recursion_limit": 80}

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
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp1:
        compiled1 = _kickoff_build_graph()(checkpointer=cp1)
        first = compiled1.invoke(_kickoff_state(run_dir, spec_path), config=config)

    payload = _assert_paused(
        first,
        reason=HALT_MANUAL_REQUESTED,
        redrive=IMPLEMENT_REQUIREMENTS_NODE,
        reset=True,
    )
    assert payload[INTERRUPT_PARENT_NODE_KEY] == RUN_ONE_TASK_NODE
    assert payload[INTERRUPT_CHECKPOINT_NS_KEY] == "item-1"
    first_output = _implement_output(run_dir).read_text(encoding="utf-8")
    assert "missing capability" in first_output
    assert not (run_dir / "05_run_tasks" / "item-2").exists()

    redrive_exec, redrive_calls = _counting_executor(
        [(f"Result: {RESULT_STOPPED} — still missing", True)]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", redrive_exec)
    exit_code = agentgraph_cli.cmd_redrive(_cmd_redrive_args(run_dir))
    assert exit_code == 0
    assert redrive_calls["n"] >= 1, "child implement must dispatch again after new-process redrive"
    assert "still missing" in _implement_output(run_dir).read_text(encoding="utf-8")
    assert not (run_dir / "05_run_tasks" / "item-2").exists()

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp2:
        compiled2 = _kickoff_build_graph()(checkpointer=cp2)
        snap = compiled2.get_state(thread_config(run_id))

    assert snap.next, "parent must stay paused on the nested child, not END"
    assert snap.values.get(OUTCOME_KEY) is None
    resumed = interrupt_payload_from_snapshot(snap)
    assert resumed is not None
    assert resumed[INTERRUPT_REDRIVE_NODE_KEY] == IMPLEMENT_REQUIREMENTS_NODE
    assert resumed[INTERRUPT_PARENT_NODE_KEY] == RUN_ONE_TASK_NODE


def test_sqlite_new_process_retries_exhausted_redrive_runs_implement_again(
    monkeypatch, tmp_path
):
    """Two failed implement dispatches (no output.md) then redrive actually implements."""
    _patch_kickoff_nodes(monkeypatch)
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_nested-retries-exhausted"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)
    _tasks_json, spec_path = _seed_plan_files(tmp_path, _two_items())
    _seed_additional_test(run_dir)
    config = {**thread_config(run_id), "recursion_limit": 80}

    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                ("Result: plan written", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (None, False),
                (None, False),
            ]
        ),
    )
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp1:
        compiled1 = _kickoff_build_graph()(checkpointer=cp1)
        first = compiled1.invoke(_kickoff_state(run_dir, spec_path), config=config)

    payload = interrupt_payload_from_result(first)
    assert payload is not None
    assert payload["reason"] == HALT_RETRIES_EXHAUSTED
    assert payload[INTERRUPT_REDRIVE_NODE_KEY] == IMPLEMENT_REQUIREMENTS_NODE
    assert payload[INTERRUPT_PARENT_NODE_KEY] == RUN_ONE_TASK_NODE
    item1_impl = run_dir / "05_run_tasks" / "item-1" / "02_implement_requirements"
    assert not list(item1_impl.glob("attempt-*/output.md"))
    assert not (run_dir / "05_run_tasks" / "item-2").exists()

    redrive_exec, redrive_calls = _counting_executor(
        [
            (f"Result: {RESULT_IMPLEMENTED}", True),
            (f"Result: {RESULT_ACCEPT}", True),
            (f"Result: {RESULT_STOPPED} — t2 not yet", True),
        ]
    )
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", redrive_exec)
    exit_code = agentgraph_cli.cmd_redrive(_cmd_redrive_args(run_dir))
    assert exit_code == 0
    assert redrive_calls["n"] >= 1, "redrive must dispatch implement again"
    written = list(item1_impl.glob("attempt-*/output.md"))
    assert written, "redrive must run implement and write output.md"
    assert any(RESULT_IMPLEMENTED in p.read_text(encoding="utf-8") for p in written)
    assert (run_dir / "05_run_tasks" / "item-1" / "04_success").exists()

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp2:
        compiled2 = _kickoff_build_graph()(checkpointer=cp2)
        snap = compiled2.get_state(thread_config(run_id))

    assert snap.values.get(OUTCOME_KEY) is None
    assert snap.next, "must not END with a silent null outcome after nested redrive"
