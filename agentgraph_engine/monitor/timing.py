"""Per-node duration derived from the parent checkpoint chain's `ts` diffs."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

from agentgraph_engine.constants import (
    CURRENT_ITEM_INDEX_KEY,
    CURRENT_ITEM_KEY,
    HALT_REASON_KEY,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    ITEM_KEY,
    MAP_PHASE_STATES_KEY,
    OUTCOME_KEY,
    REDRIVE_MESSAGE_KEY,
    REDRIVE_NODE_KEY,
    RESET_ATTEMPTS_KEY,
    RUN_DIR_KEY,
    SPEC_PATH_KEY,
    PLAN_PATH_KEY,
    WORKER_CLI_KEY,
)
from agentgraph_engine.monitor.checkpointer import open_readonly_checkpointer
from agentgraph_engine.pause import INTERRUPT_PARENT_NODE_KEY, NESTED_CHECKPOINT_NS_STATE_KEY

_BRANCH_TO_PREFIX = "branch:to:"

# Top-level state fields shared across graphs (states/base.py, states/feature_kickoff.py,
# states/standard_phase.py, pause.py) as opposed to a node's own `{node_id: {...}}` record
# channel, which is what `updated_channels` uses to mark "this node ran".
_GRAPH_LEVEL_CHANNELS = frozenset(
    {
        RUN_DIR_KEY,
        HALTED_KEY,
        HALT_REASON_KEY,
        HALTED_AT_NODE_KEY,
        REDRIVE_NODE_KEY,
        RESET_ATTEMPTS_KEY,
        REDRIVE_MESSAGE_KEY,
        OUTCOME_KEY,
        WORKER_CLI_KEY,
        SPEC_PATH_KEY,
        PLAN_PATH_KEY,
        MAP_PHASE_STATES_KEY,
        CURRENT_ITEM_KEY,
        CURRENT_ITEM_INDEX_KEY,
        ITEM_KEY,
        INTERRUPT_PARENT_NODE_KEY,
        NESTED_CHECKPOINT_NS_STATE_KEY,
    }
)


class NodeTiming(TypedDict):
    node: str
    duration: timedelta


def _node_names(updated_channels: list[str] | None) -> list[str]:
    if not updated_channels:
        return []
    return [
        channel
        for channel in updated_channels
        if not channel.startswith(_BRANCH_TO_PREFIX) and channel not in _GRAPH_LEVEL_CHANNELS
    ]


def node_timings(checkpoint_path: Path, thread_id: str) -> list[NodeTiming]:
    """Ordered `{node, duration}` list for `thread_id`'s `checkpoint_ns == ""` chain.

    Walks each checkpoint's `parent_checkpoint_id`, diffs its `ts` against the parent's
    `ts`, and attributes the interval to the node named in the later checkpoint's
    `updated_channels`. A checkpoint with no parent (the sole `source == "input"` row) or
    an unparseable `ts` on either side contributes no interval, without raising.
    """
    with open_readonly_checkpointer(checkpoint_path) as saver:
        tuples = list(
            saver.list({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        )
    by_id = {tup.config["configurable"]["checkpoint_id"]: tup for tup in tuples}
    ordered = sorted(tuples, key=lambda tup: tup.config["configurable"]["checkpoint_id"])

    timings: list[NodeTiming] = []
    for tup in ordered:
        if tup.parent_config is None:
            continue
        parent_tuple = by_id.get(tup.parent_config["configurable"]["checkpoint_id"])
        if parent_tuple is None:
            continue
        try:
            later_ts = datetime.fromisoformat(tup.checkpoint["ts"])
            earlier_ts = datetime.fromisoformat(parent_tuple.checkpoint["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = later_ts - earlier_ts
        for node in _node_names(tup.checkpoint.get("updated_channels")):
            timings.append({"node": node, "duration": duration})
    return timings
