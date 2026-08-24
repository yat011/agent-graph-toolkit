"""Demonstrated resume-from-checkpoint proof (deliverable i).

This is a REAL executed proof, not an API-exists claim: a hello_graph run is driven to its
`interrupt()` pause using one `SqliteSaver` instance backed by a real on-disk `.sqlite` file,
that checkpointer is then closed entirely (simulating a process exit/crash), and a SECOND,
independent `SqliteSaver` instance opened against the very same file resumes the run — proving
persistence actually round-trips through disk, not through an in-memory object that happened to
survive. `greet`/`fan_out` (the nodes before the interrupt) are call-counted and asserted to run
exactly once each, proving the resume continues from the checkpoint rather than restarting cold.
"""

from agentgraph_engine.constants import (
    ITEMS_KEY,
    OUTCOME_KEY,
    RUN_DIR_KEY,
)

import json
import sqlite3
import subprocess
from pathlib import Path

from langgraph.types import Command

from agentgraph_engine.examples.hello_graph.nodes import (
    CHECKPOINT_GATE_NODE,
    FAN_OUT_NODE,
    GREET_NODE,
    RESULTS_KEY,
)
from agentgraph_engine.dispatch import OUTPUT_PATH_LINE_PREFIX
from agentgraph_engine.graph_loader import load_graph_module
from agentgraph_engine.runs import checkpoint_path_for, open_checkpointer, run_dir_for

GRAPH_PATH = (
    Path(__file__).resolve().parent.parent / "agentgraph_engine" / "examples" / "hello_graph" / "graph.py"
)

MARKER = OUTPUT_PATH_LINE_PREFIX


def _write_output(input_text: str, content: str) -> None:
    path_line = next(line for line in input_text.splitlines() if line.startswith(MARKER))
    out_path = Path(path_line[len(MARKER) :].strip())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


def _ok_executor():
    def executor(argv, input_text, timeout):
        content = "Said hello.\nResult: greeted"
        _write_output(input_text, content)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": content}), stderr="")

    return executor


def test_real_sqlite_checkpoint_survives_simulated_process_restart_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _ok_executor())

    module = load_graph_module(GRAPH_PATH)
    call_counts = {GREET_NODE: 0, FAN_OUT_NODE: 0}
    orig_greet, orig_fan_out = module.greet, module.fan_out

    def counting_greet(state):
        call_counts[GREET_NODE] += 1
        return orig_greet(state)

    def counting_fan_out(state):
        call_counts[FAN_OUT_NODE] += 1
        return orig_fan_out(state)

    module.greet = counting_greet
    module.fan_out = counting_fan_out

    graph_name = "hello-demo"
    run_id = "20260101T000000_resume-proof"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)
    config = {"configurable": {"thread_id": run_id}}

    # --- Pass 1: run to the interrupt(), using checkpointer instance #1. ---
    with open_checkpointer(graph_name, run_id, agent_works_root) as cp1:
        compiled1 = module.build_graph(checkpointer=cp1)
        r1 = compiled1.invoke({RUN_DIR_KEY: str(run_dir), ITEMS_KEY: ["p", "q"]}, config=config)
        assert "__interrupt__" in r1, "expected the graph to pause at checkpoint_gate's interrupt()"
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
        compiled2 = module.build_graph(checkpointer=cp2)
        r2 = compiled2.invoke(Command(resume="go"), config=config)
        assert r2[OUTCOME_KEY] == "pass"
        assert r2[FAN_OUT_NODE][RESULTS_KEY] == ["P", "Q"]

    # greet/fan_out must NOT have re-run: the resume continued from the checkpoint, not cold.
    assert call_counts == {GREET_NODE: 1, FAN_OUT_NODE: 1}


def test_status_via_cli_module_reports_pending_node_before_resume(monkeypatch, tmp_path):
    """A `status` check between the two passes (as a human/CLI would do) sees the graph paused
    exactly at checkpoint_gate, not silently finished or restarted."""
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _ok_executor())
    module = load_graph_module(GRAPH_PATH)

    graph_name = "hello-demo-status"
    run_id = "20260101T000000_status-check"
    agent_works_root = tmp_path / "agent_works"
    run_dir = run_dir_for(graph_name, run_id, agent_works_root)
    config = {"configurable": {"thread_id": run_id}}

    with open_checkpointer(graph_name, run_id, agent_works_root) as cp:
        compiled = module.build_graph(checkpointer=cp)
        compiled.invoke({RUN_DIR_KEY: str(run_dir), ITEMS_KEY: ["x"]}, config=config)
        snapshot = compiled.get_state(config)
        assert snapshot.next == (CHECKPOINT_GATE_NODE,)
