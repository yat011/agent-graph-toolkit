"""Per-node duration from the parent checkpoint chain."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from agentgraph_engine.monitor.timing import node_timings
from agentgraph_engine.runs import checkpoint_path_for, open_checkpointer

_T0 = "2024-05-04T06:32:42.235444+00:00"
_T1 = "2024-05-04T06:33:10.500000+00:00"
_T2 = "2024-05-04T06:34:00.000000+00:00"

_INPUT_METADATA = {"source": "input", "step": -1, "writes": {}}
_LOOP_METADATA = {"source": "loop", "step": 0, "writes": {}}


def _checkpoint(checkpoint_id: str, ts: str, updated_channels=None) -> dict:
    checkpoint = {
        "ts": ts,
        "id": checkpoint_id,
        "channel_values": {},
    }
    if updated_channels is not None:
        checkpoint["updated_channels"] = updated_channels
    return checkpoint


def _put(saver, thread_id: str, checkpoint: dict, *, checkpoint_ns: str = "", parent_id=None, metadata=None):
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    if parent_id is not None:
        config["configurable"]["checkpoint_id"] = parent_id
    saver.put(config, checkpoint, metadata or _LOOP_METADATA, {})


def _open(agent_works_root: Path, graph_name: str, run_id: str) -> Path:
    return checkpoint_path_for(graph_name, run_id, agent_works_root)


def test_two_checkpoint_chain_attributes_duration_to_node_in_updated_channels(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_two-checkpoint"

    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _checkpoint("a1", _T0), metadata=_INPUT_METADATA)
        _put(
            saver,
            run_id,
            _checkpoint("a2", _T1, updated_channels=["create_feature_branch_node"]),
            parent_id="a1",
        )

    timings = node_timings(_open(agent_works_root, graph_name, run_id), run_id)

    expected_duration = datetime.fromisoformat(_T1) - datetime.fromisoformat(_T0)
    assert timings == [{"node": "create_feature_branch_node", "duration": expected_duration}]


def test_non_empty_checkpoint_ns_rows_are_ignored_for_parent_thread_durations(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_ns-filter"

    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _checkpoint("b1", _T0), metadata=_INPUT_METADATA)
        _put(
            saver,
            run_id,
            _checkpoint("b2", _T1, updated_channels=["planner_node"]),
            parent_id="b1",
        )
        # Same thread_id, different checkpoint_ns — must not steal or add to the parent's
        # duration list, even though its parent_checkpoint_id points at the "" ns chain.
        _put(
            saver,
            run_id,
            _checkpoint("b3", _T2, updated_channels=["run_one_task_node"]),
            checkpoint_ns="run_tasks_node:deadbeef",
            parent_id="b2",
        )

    timings = node_timings(_open(agent_works_root, graph_name, run_id), run_id)

    expected_duration = datetime.fromisoformat(_T1) - datetime.fromisoformat(_T0)
    assert timings == [{"node": "planner_node", "duration": expected_duration}]


def test_input_only_run_returns_empty_list_not_error(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_input-only"

    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _checkpoint("c1", _T0), metadata=_INPUT_METADATA)

    timings = node_timings(_open(agent_works_root, graph_name, run_id), run_id)

    assert timings == []


def test_malformed_ts_skips_only_that_interval(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_malformed-ts"

    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _checkpoint("d1", _T0), metadata=_INPUT_METADATA)
        _put(
            saver,
            run_id,
            _checkpoint("d2", _T1, updated_channels=["planner_node"]),
            parent_id="d1",
        )
        _put(
            saver,
            run_id,
            _checkpoint(
                "d3", "not-a-timestamp", updated_channels=["tech_plan_reviewer_node"]
            ),
            parent_id="d2",
        )

    timings = node_timings(_open(agent_works_root, graph_name, run_id), run_id)

    expected_duration = datetime.fromisoformat(_T1) - datetime.fromisoformat(_T0)
    assert timings == [{"node": "planner_node", "duration": expected_duration}]


def test_graph_level_and_branch_channels_are_not_attributed(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_filtered-channels"

    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _checkpoint("e1", _T0), metadata=_INPUT_METADATA)
        _put(
            saver,
            run_id,
            _checkpoint(
                "e2",
                _T1,
                updated_channels=[
                    "run_dir",
                    "outcome",
                    "branch:to:planner_node",
                    "create_feature_branch_node",
                ],
            ),
            parent_id="e1",
        )

    timings = node_timings(_open(agent_works_root, graph_name, run_id), run_id)

    expected_duration = datetime.fromisoformat(_T1) - datetime.fromisoformat(_T0)
    assert timings == [{"node": "create_feature_branch_node", "duration": expected_duration}]


def test_child_thread_timing_uses_the_same_walker_on_the_child_thread_id(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_child-thread"
    child_thread_id = f"{run_id}:item-1"

    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _checkpoint("f1", _T0), metadata=_INPUT_METADATA)
        _put(
            saver,
            child_thread_id,
            _checkpoint("g1", _T0),
            metadata=_INPUT_METADATA,
        )
        _put(
            saver,
            child_thread_id,
            _checkpoint("g2", _T1, updated_channels=["implement_requirements_node"]),
            parent_id="g1",
        )

    timings = node_timings(_open(agent_works_root, graph_name, run_id), child_thread_id)

    expected_duration = datetime.fromisoformat(_T1) - datetime.fromisoformat(_T0)
    assert timings == [
        {"node": "implement_requirements_node", "duration": expected_duration}
    ]
