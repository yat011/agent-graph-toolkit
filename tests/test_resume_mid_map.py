"""`agentgraph resume` must not drop a pending Command(goto=...)-pushed task.

Regression for a real bug found while running feature-kickoff on the agentgraph-monitor spec:
`cmd_resume` called `compiled.update_state(config, {"worker_cli": ...})` as its own standalone
step, then `compiled.invoke(None, config)`. When the Run's pending next node was reached via a
dynamic `Command(goto=...)` push (as `pick_next_phase` -> `run_one_phase` uses, not a static
`add_conditional_edges` wiring), that intermediate update_state() checkpoint silently dropped the
pending push task. `invoke(None)` then found nothing left to do and returned immediately, with
`outcome` never set — the Run looked "finished" (`next: []`) while later map items and the final
review never ran.

This reproduces that exact shape — a Run paused *without* an author `interrupt()` (so no
`__interrupt__` key), sitting on a Command-routed `run_one_task_node` pending task for a second
map item, exactly as if the process had been killed mid-`invoke()` between map items — then
exercises `agentgraph_cli.cmd_resume` against a brand-new `SqliteSaver` over the same file (a new
process picking the Run back up), and asserts the second item actually runs to completion instead
of being silently swallowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentgraph_engine import cli as agentgraph_cli
from agentgraph_engine.constants import (
    OUTCOME_KEY,
    OUTCOME_SUCCESS,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RUN_ONE_PHASE_NODE,
)
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.runs import open_checkpointer, run_dir_for, thread_config

from tests.test_feature_kickoff_graph import (
    GRAPH_PATH,
    NODES_MODULE,
    _fake_additional_test,
    _fake_git,
    _kickoff_state,
    _script_executor,
    _seed_additional_test,
    _seed_plan_files,
)


def _two_dependent_items() -> list[dict]:
    return [
        {"id": "t1", "title": "First", "description": "d1", "dependencies": [], "review": "always"},
        {"id": "t2", "title": "Second", "description": "d2", "dependencies": ["t1"], "review": "always"},
    ]


def _patch_kickoff_nodes(monkeypatch) -> None:
    load_graph_module(GRAPH_PATH)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_git", _fake_git)
    monkeypatch.setattr(sys.modules[NODES_MODULE], "_run_additional_test", _fake_additional_test)
    monkeypatch.setattr(
        "agentgraph_engine.review_policy.commit_dirty_tree",
        lambda message, **kwargs: False,
    )


def _kickoff_build_graph():
    return get_build_graph(load_graph_module(GRAPH_PATH))


def _resume_args(run_dir: Path, worker_cli: str) -> object:
    import argparse

    return argparse.Namespace(
        run=str(run_dir),
        resume_value=None,
        recursion_limit=80,
        cli=worker_cli,
    )


def test_resume_after_mid_map_process_restart_runs_remaining_item(
    monkeypatch, tmp_path, capsys
):
    """A Run paused mid-map (no interrupt, just a pending Command-routed task) must have its
    second item actually dispatched by `cmd_resume` on a new process, and must reach a real
    `outcome`, not fall through to a false `next: []` / `outcome: None` terminal."""
    _patch_kickoff_nodes(monkeypatch)
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_resume-mid-map"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)
    _tasks_json, spec_path = _seed_plan_files(tmp_path, _two_dependent_items())
    _seed_additional_test(run_dir)
    config = {**thread_config(run_id), "recursion_limit": 80}

    # --- Pass 1 (process #1): drive up to, then past, the interrupt_before fence exactly once,
    # landing on a pending run_one_task_node dispatch for item t2 — item t1 already succeeded,
    # no author interrupt() anywhere in this state. ---
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
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp1:
        compiled1 = _kickoff_build_graph()(checkpointer=cp1)
        first = compiled1.invoke(
            _kickoff_state(run_dir, spec_path),
            config=config,
            interrupt_before=[RUN_ONE_PHASE_NODE],
        )
        assert "__interrupt__" not in first
        second = compiled1.invoke(None, config=config, interrupt_before=[RUN_ONE_PHASE_NODE])
        assert "__interrupt__" not in second
        snap = compiled1.get_state(config)
        assert snap.next == (RUN_ONE_PHASE_NODE,)
        assert snap.values.get(OUTCOME_KEY) is None

    assert (run_dir / "05_run_phases" / "item-1" / "04_success").exists()
    assert not (run_dir / "05_run_phases" / "item-2").exists()

    # --- Pass 2 (process #2, new SqliteSaver): the real `agentgraph resume --cli claude`
    # call, exactly as a Coordinating agent would issue after a killed process. ---
    monkeypatch.setattr(
        "agentgraph_engine.dispatch._run_subprocess",
        _script_executor(
            [
                (f"Result: {RESULT_IMPLEMENTED}", True),
                (f"Result: {RESULT_ACCEPT}", True),
                (f"Result: {RESULT_ACCEPT}", True),
            ]
        ),
    )
    exit_code = agentgraph_cli.cmd_resume(_resume_args(run_dir, "claude"))
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed[OUTCOME_KEY] == OUTCOME_SUCCESS, (
        "resume must reach the real success terminal, not a false "
        "halted=False/outcome=None dead end"
    )

    assert (run_dir / "05_run_phases" / "item-2" / "04_success").exists(), (
        "item t2's pending run_one_task_node dispatch must not be silently dropped by resume"
    )

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp2:
        compiled2 = _kickoff_build_graph()(checkpointer=cp2)
        final_snap = compiled2.get_state(thread_config(run_id))

    assert final_snap.next == ()
    assert final_snap.values.get(OUTCOME_KEY) == OUTCOME_SUCCESS
    assert final_snap.values.get("worker_cli") == "claude"
