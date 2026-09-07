"""Demonstrated resume-from-checkpoint proof (deliverable i).

This is a REAL executed proof, not an API-exists claim: an inline fixture graph is driven
to its `interrupt()` pause using one `SqliteSaver` instance backed by a real on-disk
`.sqlite` file, that checkpointer is then closed entirely (simulating a process
exit/crash), and a SECOND, independent `SqliteSaver` instance opened against the very
same file resumes the run — proving persistence actually round-trips through disk, not
through an in-memory object that happened to survive. `greet`/`fan_out` (the nodes
before the interrupt) are call-counted and asserted to run exactly once each, proving
the resume continues from the checkpoint rather than restarting cold.
"""

import sqlite3
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agentgraph_engine.constants import (
    ITEMS_KEY,
    OUTCOME_KEY,
)
from agentgraph_engine.runs import checkpoint_path_for, open_checkpointer, thread_config

GREET_NODE = "greet_node"
FAN_OUT_NODE = "fan_out_node"
CHECKPOINT_GATE_NODE = "checkpoint_gate_node"
PASS_NODE = "pass_node"
RESULTS_KEY = "results"


class _State(TypedDict, total=False):
    items: list
    outcome: str
    greet_node: dict
    fan_out_node: dict
    checkpoint_gate_node: dict


def _greet(state):
    return {GREET_NODE: {"greeting": "hello"}}


def _fan_out(state):
    items = state.get(ITEMS_KEY) or []
    return {FAN_OUT_NODE: {RESULTS_KEY: [str(item).upper() for item in items]}}


def _gate(state):
    ack = interrupt({"message": "fixture graph paused at gate — resume to continue"})
    return {CHECKPOINT_GATE_NODE: {"ack": ack}}


def _finish(state):
    return {OUTCOME_KEY: "pass"}


def _build_graph(checkpointer, call_counts):
    def counting_greet(state):
        call_counts[GREET_NODE] += 1
        return _greet(state)

    def counting_fan_out(state):
        call_counts[FAN_OUT_NODE] += 1
        return _fan_out(state)

    graph = StateGraph(_State)
    graph.add_node(GREET_NODE, counting_greet)
    graph.add_node(FAN_OUT_NODE, counting_fan_out)
    graph.add_node(CHECKPOINT_GATE_NODE, _gate)
    graph.add_node(PASS_NODE, _finish)
    graph.add_edge(START, GREET_NODE)
    graph.add_edge(GREET_NODE, FAN_OUT_NODE)
    graph.add_edge(FAN_OUT_NODE, CHECKPOINT_GATE_NODE)
    graph.add_edge(CHECKPOINT_GATE_NODE, PASS_NODE)
    graph.add_edge(PASS_NODE, END)
    return graph.compile(checkpointer=checkpointer)


def test_real_sqlite_checkpoint_survives_simulated_process_restart_and_resumes(tmp_path):
    graph_name = "fixture-demo"
    run_id = "20260101T000000_resume-proof"
    agent_works_root = tmp_path / "agent_works"
    config = thread_config(run_id)
    call_counts = {GREET_NODE: 0, FAN_OUT_NODE: 0}

    # --- Pass 1: run to the interrupt(), using checkpointer instance #1. ---
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp1:
        compiled1 = _build_graph(cp1, call_counts)
        r1 = compiled1.invoke({ITEMS_KEY: ["p", "q"]}, config=config)
        assert "__interrupt__" in r1, "expected the graph to pause at the gate's interrupt()"
        assert OUTCOME_KEY not in r1

    assert call_counts == {GREET_NODE: 1, FAN_OUT_NODE: 1}

    # --- Prove real on-disk persistence, not an in-memory artifact. ---
    sqlite_path = checkpoint_path_for(graph_name, run_id, agent_works_root)
    assert sqlite_path.exists()
    assert sqlite_path.stat().st_size > 0
    con = sqlite3.connect(str(sqlite_path))
    tables = [row[0] for row in con.execute("select name from sqlite_master where type='table'").fetchall()]
    assert tables, "expected SqliteSaver to have created at least one table on disk"
    checkpoint_rows = 0
    for table in tables:
        if "checkpoint" in table.lower():
            checkpoint_rows += con.execute(f"select count(*) from {table}").fetchone()[0]
    con.close()
    assert checkpoint_rows > 0, "expected at least one persisted checkpoint row on disk"

    # --- Pass 2: brand-new, independent SqliteSaver instance over the SAME file — simulates a
    # fresh process picking the run back up "cold" from disk, per the bug-fix/continuation
    # pattern this whole migration exists to support. ---
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp2:
        compiled2 = _build_graph(cp2, call_counts)
        r2 = compiled2.invoke(Command(resume="go"), config=config)
        assert r2[OUTCOME_KEY] == "pass"
        assert r2[FAN_OUT_NODE][RESULTS_KEY] == ["P", "Q"]

    # greet/fan_out must NOT have re-run: the resume continued from the checkpoint, not cold.
    assert call_counts == {GREET_NODE: 1, FAN_OUT_NODE: 1}


def test_status_via_cli_module_reports_pending_node_before_resume(tmp_path):
    """A `status` check between the two passes (as a human/CLI would do) sees the graph paused
    exactly at the gate, not silently finished or restarted."""
    graph_name = "fixture-demo-status"
    run_id = "20260101T000000_status-check"
    agent_works_root = tmp_path / "agent_works"
    config = thread_config(run_id)
    call_counts = {GREET_NODE: 0, FAN_OUT_NODE: 0}

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = _build_graph(cp, call_counts)
        compiled.invoke({ITEMS_KEY: ["x"]}, config=config)
        snapshot = compiled.get_state(config)
        assert snapshot.next == (CHECKPOINT_GATE_NODE,)
