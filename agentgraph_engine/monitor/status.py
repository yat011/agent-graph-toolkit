"""Five-bucket Run status and fleet/detail view models from a parent snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from agentgraph_engine.constants import (
    CURRENT_ITEM_INDEX_KEY,
    HALT_REASON_KEY,
    HALT_UNMET_DEPENDENCIES,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    ITEMS_KEY,
    LOAD_PHASES_NODE,
    OUTCOME_KEY,
    RUN_ONE_PHASE_NODE,
)
from agentgraph_engine.monitor.checkpointer import open_readonly_checkpointer
from agentgraph_engine.monitor.discovery import DiscoveredRun
from agentgraph_engine.monitor.graph_resolve import CHILD_GRAPH_NAME, compiled_graph_for
from agentgraph_engine.pause import interrupt_payload_from_snapshot
from agentgraph_engine.runs import thread_config

STATUS_RUNNING = "Running"
STATUS_PAUSED_AWAITING_REDRIVE = "Paused-awaiting-redrive"
STATUS_BLOCKED = "Blocked"
STATUS_FAILED = "Failed"
STATUS_COMPLETED = "Completed"

_OUTCOME_FAIL = "fail"
_OUTCOME_SUCCESS = frozenset({"success", "pass"})


class FleetRow(TypedDict):
    run_id: str
    graph_name: str
    status: str
    current_node: str | None


class ChildThread(TypedDict):
    thread_id: str


class DetailView(FleetRow):
    children: list[ChildThread]


def has_open_interrupt(snapshot: object) -> bool:
    """True when the snapshot has an open interrupt (spec + Plan)."""
    if getattr(snapshot, "interrupts", None):
        return True
    for task in getattr(snapshot, "tasks", None) or ():
        if getattr(task, "interrupts", None):
            return True
    return interrupt_payload_from_snapshot(snapshot) is not None


def classify_status(snapshot: object) -> str:
    """Map a `StateSnapshot` onto the five Monitor buckets, in Plan order."""
    values = getattr(snapshot, "values", None) or {}
    nxt = getattr(snapshot, "next", None) or ()
    halt_reason = values.get(HALT_REASON_KEY)
    open_interrupt = has_open_interrupt(snapshot)
    if open_interrupt and halt_reason == HALT_UNMET_DEPENDENCIES:
        return STATUS_BLOCKED
    if open_interrupt:
        return STATUS_PAUSED_AWAITING_REDRIVE
    outcome = values.get(OUTCOME_KEY)
    next_empty = not nxt
    if next_empty and (
        outcome == _OUTCOME_FAIL
        or (
            values.get(HALTED_KEY)
            and halt_reason
            and outcome not in _OUTCOME_SUCCESS
        )
    ):
        return STATUS_FAILED
    if next_empty:
        return STATUS_COMPLETED
    return STATUS_RUNNING


def current_node_label(snapshot: object) -> str | None:
    """First `next` element, else `halted_at_node`; item n of m on `run_one_phase_node`."""
    nxt = getattr(snapshot, "next", None) or ()
    values = getattr(snapshot, "values", None) or {}
    node = nxt[0] if nxt else values.get(HALTED_AT_NODE_KEY)
    if node != RUN_ONE_PHASE_NODE:
        return node
    index = values.get(CURRENT_ITEM_INDEX_KEY)
    items = (values.get(LOAD_PHASES_NODE) or {}).get(ITEMS_KEY) or []
    if index is None or not items:
        return node
    return f"item {index} of {len(items)}"


def fleet_row_from_snapshot(run_id: str, graph_name: str, snapshot: object) -> FleetRow:
    return {
        "run_id": run_id,
        "graph_name": graph_name,
        "status": classify_status(snapshot),
        "current_node": current_node_label(snapshot),
    }


def _load_parent_snapshot(run: DiscoveredRun) -> object:
    with open_readonly_checkpointer(run["checkpoint_path"]) as saver:
        compiled = compiled_graph_for(run, run["graph_name"], saver)
        return compiled.get_state(thread_config(run["run_id"]))


def child_snapshot(run: DiscoveredRun, thread_id: str) -> object:
    """`get_state` for a nested `{run_id}:item-*` thread, via its own standard-phase graph."""
    with open_readonly_checkpointer(run["checkpoint_path"]) as saver:
        compiled = compiled_graph_for(run, CHILD_GRAPH_NAME, saver)
        return compiled.get_state(thread_config(thread_id))


def child_row(run: DiscoveredRun, thread_id: str) -> FleetRow:
    """A child thread's own status/current-node, for drill-in past the parent row."""
    return fleet_row_from_snapshot(thread_id, CHILD_GRAPH_NAME, child_snapshot(run, thread_id))


def child_thread_ids(checkpoint_path: Path, run_id: str) -> list[str]:
    """Other `thread_id`s in the sqlite that start with `{run_id}:`. Not the parent."""
    prefix = f"{run_id}:"
    with open_readonly_checkpointer(checkpoint_path) as saver:
        with saver.cursor(transaction=False) as cur:
            cur.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ? ORDER BY thread_id",
                (f"{prefix}%",),
            )
            return [row[0] for row in cur.fetchall()]


def fleet_row(run: DiscoveredRun) -> FleetRow:
    return fleet_row_from_snapshot(run["run_id"], run["graph_name"], _load_parent_snapshot(run))


def fleet_rows(runs: list[DiscoveredRun]) -> list[FleetRow]:
    return [fleet_row(run) for run in runs]


def detail_view(run: DiscoveredRun) -> DetailView:
    row = fleet_row(run)
    children: list[ChildThread] = [
        {"thread_id": thread_id}
        for thread_id in child_thread_ids(run["checkpoint_path"], run["run_id"])
    ]
    return {**row, "children": children}
