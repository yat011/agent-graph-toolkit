"""Headless Textual pilot coverage for the fleet TUI: Completed filter, nested-child detail,
and a read-only poll tick that picks up a new Run without restarting the app."""

from __future__ import annotations

import asyncio
import filecmp
import sqlite3
from pathlib import Path

from textual.widgets import DataTable, Static

from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.monitor.app import DetailScreen, FleetScreen, MonitorApp
from agentgraph_engine.runs import checkpoint_path_for, open_checkpointer, thread_config

MINI_GRAPH_PY = '''
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class S(TypedDict, total=False):
    x: int

def _a(state):
    return {"x": state.get("x", 0) + 1}

def _b(state):
    return {"x": state.get("x", 0) + 10}

def build_graph(checkpointer=None):
    g = StateGraph(S)
    g.add_node("node_a", _a)
    g.add_node("node_b", _b)
    g.add_edge(START, "node_a")
    g.add_edge("node_a", "node_b")
    g.add_edge("node_b", END)
    return g.compile(checkpointer=checkpointer, interrupt_after=["node_a"])
'''

GRAPH_NAME = "monitor-mini"


def _write_graph(agent_works_root: Path) -> Path:
    graph_py = agent_works_root / "graphs" / GRAPH_NAME / "graph.py"
    graph_py.parent.mkdir(parents=True, exist_ok=True)
    graph_py.write_text(MINI_GRAPH_PY, encoding="utf-8")
    return graph_py


def _seed_running(agent_works_root: Path, graph_py: Path, run_id: str) -> None:
    with open_checkpointer(GRAPH_NAME, run_id, agent_works_root) as saver:
        compiled = get_build_graph(load_graph_module(graph_py))(checkpointer=saver)
        compiled.invoke({"x": 0}, config=thread_config(run_id))


def _seed_completed(agent_works_root: Path, graph_py: Path, run_id: str) -> None:
    with open_checkpointer(GRAPH_NAME, run_id, agent_works_root) as saver:
        compiled = get_build_graph(load_graph_module(graph_py))(checkpointer=saver)
        compiled.invoke({"x": 0}, config=thread_config(run_id))
        compiled.invoke(None, config=thread_config(run_id))


def test_completed_run_hidden_until_toggle(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_py = _write_graph(agent_works_root)
    running_id = "20260101T000000_running"
    completed_id = "20260101T000001_completed"
    _seed_running(agent_works_root, graph_py, running_id)
    _seed_completed(agent_works_root, graph_py, completed_id)

    async def scenario():
        app = MonitorApp(agent_works_root=agent_works_root, interval=3)
        async with app.run_test() as pilot:
            screen = app.screen
            assert isinstance(screen, FleetScreen)
            visible_ids = {run["run_id"] for run, _ in screen._visible()}
            assert visible_ids == {running_id}

            await pilot.press("c")
            visible_ids = {run["run_id"] for run, _ in screen._visible()}
            assert visible_ids == {running_id, completed_id}

            await pilot.press("c")
            visible_ids = {run["run_id"] for run, _ in screen._visible()}
            assert visible_ids == {running_id}

    asyncio.run(scenario())


CHILD_GRAPH_PY = '''
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class S(TypedDict, total=False):
    y: int

def _p(state):
    return {"y": state.get("y", 0) + 1}

def _q(state):
    return {"y": state.get("y", 0) + 100}

def build_graph(checkpointer=None):
    g = StateGraph(S)
    g.add_node("child_node_p", _p)
    g.add_node("child_node_q", _q)
    g.add_edge(START, "child_node_p")
    g.add_edge("child_node_p", "child_node_q")
    g.add_edge("child_node_q", END)
    return g.compile(checkpointer=checkpointer, interrupt_after=["child_node_p"])
'''


def _write_child_graph(agent_works_root: Path) -> Path:
    graph_py = agent_works_root / "graphs" / "standard-phase" / "graph.py"
    graph_py.parent.mkdir(parents=True, exist_ok=True)
    graph_py.write_text(CHILD_GRAPH_PY, encoding="utf-8")
    return graph_py


def _seed_child_running(
    agent_works_root: Path, child_graph_py: Path, run_id: str, child_thread_id: str
) -> None:
    with open_checkpointer(GRAPH_NAME, run_id, agent_works_root) as saver:
        compiled = get_build_graph(load_graph_module(child_graph_py))(checkpointer=saver)
        compiled.invoke({"y": 0}, config=thread_config(child_thread_id))


def test_enter_on_parent_shows_child_in_detail_not_as_fleet_row(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_py = _write_graph(agent_works_root)
    child_graph_py = _write_child_graph(agent_works_root)
    run_id = "20260101T000002_nested"
    child_thread_id = f"{run_id}:item-1"
    _seed_running(agent_works_root, graph_py, run_id)
    _seed_child_running(agent_works_root, child_graph_py, run_id, child_thread_id)
    checkpoint_path = checkpoint_path_for(GRAPH_NAME, run_id, agent_works_root)

    async def scenario():
        app = MonitorApp(agent_works_root=agent_works_root, interval=3)
        async with app.run_test() as pilot:
            screen = app.screen
            assert isinstance(screen, FleetScreen)
            visible = screen._visible()
            assert [run["run_id"] for run, _ in visible] == [run_id]

            await pilot.press("enter")
            await pilot.pause()
            detail = app.screen
            assert isinstance(detail, DetailScreen)
            assert detail.thread_id is None
            body_text = str(detail.query_one("#detail-body", Static).content)
            assert f"{run_id}:item-1" not in body_text
            table = detail.query_one("#children-table", DataTable)
            assert table.row_count == 1
            row_key = list(table.rows.keys())[0]
            assert row_key.value == child_thread_id
            assert table.get_row(row_key)[1] == "Running"

            await pilot.press("enter")
            await pilot.pause()
            child_detail = app.screen
            assert isinstance(child_detail, DetailScreen)
            assert child_detail.thread_id == child_thread_id
            child_body = str(child_detail.query_one("#detail-body", Static).content)
            assert f"Run: {child_thread_id}" in child_body
            assert "Status: Running" in child_body
            assert "child_node_q" in child_body

    asyncio.run(scenario())
    assert checkpoint_path.is_file()


def test_poll_tick_picks_up_new_run_without_restart_and_leaves_existing_sqlite_untouched(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_py = _write_graph(agent_works_root)
    existing_id = "20260101T000003_existing"
    _seed_running(agent_works_root, graph_py, existing_id)
    existing_path = checkpoint_path_for(GRAPH_NAME, existing_id, agent_works_root)
    before_snapshot = tmp_path / "before.sqlite"
    before_snapshot.write_bytes(existing_path.read_bytes())
    with sqlite3.connect(str(existing_path)) as conn:
        row_count_before = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]

    async def scenario():
        app = MonitorApp(agent_works_root=agent_works_root, interval=3)
        async with app.run_test() as pilot:
            screen = app.screen
            assert isinstance(screen, FleetScreen)
            assert {run["run_id"] for run, _ in screen._visible()} == {existing_id}

            new_id = "20260101T000004_new"
            _seed_running(agent_works_root, graph_py, new_id)

            screen.refresh_fleet()
            await pilot.pause()
            assert {run["run_id"] for run, _ in screen._visible()} == {existing_id, new_id}

    asyncio.run(scenario())
    assert filecmp.cmp(before_snapshot, existing_path, shallow=False)
    with sqlite3.connect(str(existing_path)) as conn:
        row_count_after = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    assert row_count_after == row_count_before
