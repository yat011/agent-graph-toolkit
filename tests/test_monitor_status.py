"""Five-bucket Run status, current node, and nested-child exclusion on the fleet row."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt, PregelTask, StateSnapshot, interrupt

from agentgraph_engine.constants import (
    CURRENT_ITEM_INDEX_KEY,
    HALT_MANUAL_REQUESTED,
    HALT_REASON_KEY,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_RETRIES_EXHAUSTED,
    HALT_UNMET_DEPENDENCIES,
    HALT_UNRECOGNIZED_RESULT,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    ITEMS_KEY,
    LOAD_PHASES_NODE,
    OUTCOME_KEY,
    RUN_ONE_PHASE_NODE,
)
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.monitor.discovery import discover_runs
from agentgraph_engine.monitor.status import (
    current_node_label,
    detail_view,
    fleet_row_from_snapshot,
    fleet_rows,
)
from agentgraph_engine.runs import open_checkpointer, thread_config

MINI_GRAPH_PY = '''
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class S(TypedDict, total=False):
    x: int

def _inc(state):
    return {"x": state.get("x", 0) + 1}

def build_graph(checkpointer=None):
    g = StateGraph(S)
    g.add_node("inc", _inc)
    g.add_edge(START, "inc")
    g.add_edge("inc", END)
    return g.compile(checkpointer=checkpointer)
'''

GATE_NODE = "gate_node"
FINISH_NODE = "finish_node"


class _GateState(TypedDict, total=False):
    items: list
    outcome: str
    fail: bool
    gate_node: dict


def _gate(state):
    ack = interrupt({"message": "fixture graph paused at gate"})
    return {GATE_NODE: {"ack": ack}}


def _finish(state):
    return {OUTCOME_KEY: "fail" if state.get("fail") else "pass"}


def _build_gate_graph(checkpointer=None):
    graph = StateGraph(_GateState)
    graph.add_node(GATE_NODE, _gate)
    graph.add_node(FINISH_NODE, _finish)
    graph.add_edge(START, GATE_NODE)
    graph.add_edge(GATE_NODE, FINISH_NODE)
    graph.add_edge(FINISH_NODE, END)
    return graph.compile(checkpointer=checkpointer)


def _snapshot(*, values=None, nxt=(), interrupts=(), tasks=()):
    return StateSnapshot(
        values=values or {},
        next=tuple(nxt),
        config={"configurable": {"thread_id": "t"}},
        metadata=None,
        created_at=None,
        parent_config=None,
        tasks=tuple(tasks),
        interrupts=tuple(interrupts),
    )


def _interrupt(value=None):
    return Interrupt(value=value if value is not None else {"message": "paused"})


def test_snapshot_next_nonempty_without_interrupt_is_running():
    snapshot = _snapshot(nxt=("greet_node",))
    row = fleet_row_from_snapshot("run-a", "fixture-graph", snapshot)
    assert row["status"] == "Running"
    assert row["current_node"] == "greet_node"
    assert row["run_id"] == "run-a"
    assert row["graph_name"] == "fixture-graph"


def test_gate_interrupt_is_paused_awaiting_redrive():
    from langgraph.checkpoint.memory import InMemorySaver

    compiled = _build_gate_graph(InMemorySaver())
    config = thread_config("fixture-gate")
    compiled.invoke({ITEMS_KEY: ["x"]}, config=config)
    snapshot = compiled.get_state(config)
    assert snapshot.next == (GATE_NODE,)
    row = fleet_row_from_snapshot("fixture-gate", "fixture-graph", snapshot)
    assert row["status"] == "Paused-awaiting-redrive"
    assert row["current_node"] == GATE_NODE


def test_unmet_dependencies_interrupt_is_blocked():
    snapshot = _snapshot(
        values={HALT_REASON_KEY: HALT_UNMET_DEPENDENCIES},
        nxt=("pause_node",),
        interrupts=(_interrupt(),),
    )
    assert fleet_row_from_snapshot("r", "feature-kickoff", snapshot)["status"] == "Blocked"


def test_named_halt_reasons_with_interrupt_are_paused_awaiting_redrive():
    for reason in (
        HALT_MANUAL_REQUESTED,
        HALT_RETRIES_EXHAUSTED,
        HALT_REJECT_ATTEMPTS_EXHAUSTED,
        HALT_UNRECOGNIZED_RESULT,
    ):
        snapshot = _snapshot(
            values={HALT_REASON_KEY: reason},
            nxt=("pause_node",),
            interrupts=(_interrupt(),),
        )
        assert (
            fleet_row_from_snapshot("r", "feature-kickoff", snapshot)["status"]
            == "Paused-awaiting-redrive"
        ), reason


def test_unknown_halt_reason_plus_interrupt_is_paused_not_blocked():
    snapshot = _snapshot(
        values={HALT_REASON_KEY: "not_a_real_halt_reason"},
        nxt=("pause_node",),
        interrupts=(_interrupt(),),
    )
    assert (
        fleet_row_from_snapshot("r", "feature-kickoff", snapshot)["status"]
        == "Paused-awaiting-redrive"
    )


def test_task_interrupts_count_as_open_interrupt_even_when_snapshot_interrupts_empty():
    task = PregelTask(
        id="t1",
        name="pause_node",
        path=(),
        interrupts=(_interrupt({"message": "from-task"}),),
    )
    snapshot = _snapshot(nxt=("pause_node",), tasks=(task,))
    assert fleet_row_from_snapshot("r", "g", snapshot)["status"] == "Paused-awaiting-redrive"


def test_fail_outcome_is_failed():
    from langgraph.checkpoint.memory import InMemorySaver

    compiled = _build_gate_graph(InMemorySaver())
    config = thread_config("fixture-fail")
    compiled.invoke({ITEMS_KEY: ["a", "b"], "fail": True}, config=config)
    compiled.invoke(Command(resume="go"), config=config)
    snapshot = compiled.get_state(config)
    assert snapshot.next == ()
    assert snapshot.values.get(OUTCOME_KEY) == "fail"
    row = fleet_row_from_snapshot("fixture-fail", "fixture-graph", snapshot)
    assert row["status"] == "Failed"


def test_halted_technical_failure_is_failed():
    snapshot = _snapshot(
        values={
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: "dispatch_worker_node",
        },
    )
    row = fleet_row_from_snapshot("fixture-halt", "fixture-graph", snapshot)
    assert row["status"] == "Failed"
    assert row["current_node"] == "dispatch_worker_node"


def test_finished_success_is_completed():
    for outcome in ("success", "pass"):
        snapshot = _snapshot(values={OUTCOME_KEY: outcome})
        assert fleet_row_from_snapshot("r", "g", snapshot)["status"] == "Completed"


def test_run_one_phase_node_current_node_is_item_n_of_m():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    snapshot = _snapshot(
        values={
            CURRENT_ITEM_INDEX_KEY: 2,
            LOAD_PHASES_NODE: {ITEMS_KEY: items},
        },
        nxt=(RUN_ONE_PHASE_NODE,),
    )
    assert current_node_label(snapshot) == "item 2 of 3"


def test_parent_sqlite_with_item_thread_yields_one_fleet_row_and_child_on_detail(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "mini-status"
    run_id = "20260101T000000_nested-status"
    graph_py = agent_works_root / "graphs" / graph_name / "graph.py"
    graph_py.parent.mkdir(parents=True)
    graph_py.write_text(MINI_GRAPH_PY, encoding="utf-8")

    ns_checkpoint = {
        "ts": "2024-05-04T06:35:00.000000+00:00",
        "id": "cccccccc-cccc-cccc-cccc-ccccccccccc1",
        "channel_values": {"x": 8},
    }
    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        compiled = get_build_graph(load_graph_module(graph_py))(checkpointer=saver)
        compiled.invoke({"x": 0}, config=thread_config(run_id))
        compiled.invoke({"x": 1}, config=thread_config(f"{run_id}:item-1"))
        saver.put(
            {
                "configurable": {
                    "thread_id": run_id,
                    "checkpoint_ns": "run_tasks_node:deadbeef",
                }
            },
            ns_checkpoint,
            {"source": "loop", "step": 99, "writes": {}},
            {},
        )

    hits = discover_runs(agent_works_root)
    rows = fleet_rows(hits)
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["graph_name"] == graph_name

    detail = detail_view(hits[0])
    child_ids = [child["thread_id"] for child in detail["children"]]
    assert f"{run_id}:item-1" in child_ids
    assert run_id not in child_ids
    assert child_ids == [f"{run_id}:item-1"]
