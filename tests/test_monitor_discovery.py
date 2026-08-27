"""Read-only checkpointer opener and Run directory discovery."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agentgraph_engine.monitor.checkpointer import open_readonly_checkpointer
from agentgraph_engine.monitor.discovery import discover_runs
from agentgraph_engine.runs import (
    checkpoint_path_for,
    open_checkpointer,
    run_dir_for,
    thread_config,
)

_PARENT_CHECKPOINT = {
    "ts": "2024-05-04T06:32:42.235444+00:00",
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1",
    "channel_values": {"k": 1},
}
_LATEST_CHECKPOINT = {
    "ts": "2024-05-04T06:33:00.000000+00:00",
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2",
    "channel_values": {"k": 2},
}
_METADATA = {"source": "input", "step": -1, "writes": {}}


def _put(saver, thread_id: str, checkpoint: dict, checkpoint_ns: str = "") -> None:
    saver.put(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}},
        checkpoint,
        _METADATA,
        {},
    )


def _seed_parent(agent_works_root: Path, graph_name: str, run_id: str) -> Path:
    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _PARENT_CHECKPOINT)
    return checkpoint_path_for(graph_name, run_id, agent_works_root)


def _schema_and_counts(conn: sqlite3.Connection) -> tuple[list[str], dict[str, int]]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    return tables, counts


def test_discover_runs_returns_one_hit_for_parent_checkpoint(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "hello-demo"
    run_id = "20260101T000000_parent-hit"
    _seed_parent(agent_works_root, graph_name, run_id)

    hits = discover_runs(agent_works_root)

    assert hits == [
        {
            "graph_name": graph_name,
            "run_id": run_id,
            "run_dir": run_dir_for(graph_name, run_id, agent_works_root),
            "checkpoint_path": checkpoint_path_for(graph_name, run_id, agent_works_root),
        }
    ]


def test_discover_runs_omits_missing_sqlite_and_empty_parent_thread(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    missing_graph, missing_run = "g-missing", "20260101T000000_no-sqlite"
    run_dir_for(missing_graph, missing_run, agent_works_root).mkdir(parents=True)

    empty_graph, empty_run = "g-empty", "20260101T000000_empty"
    with open_checkpointer(empty_graph, empty_run, agent_works_root):
        pass

    corrupt_graph, corrupt_run = "g-corrupt", "20260101T000000_corrupt"
    corrupt_path = checkpoint_path_for(corrupt_graph, corrupt_run, agent_works_root)
    corrupt_path.parent.mkdir(parents=True)
    corrupt_path.write_bytes(b"not a sqlite database")

    hits = discover_runs(agent_works_root)

    assert hits == []


def test_readonly_opener_reads_latest_parent_under_concurrent_wal_writer(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "hello-wal"
    run_id = "20260101T000000_wal"
    sqlite_path = checkpoint_path_for(graph_name, run_id, agent_works_root)

    with open_checkpointer(graph_name, run_id, agent_works_root) as writer:
        _put(writer, run_id, _PARENT_CHECKPOINT)
        _put(writer, run_id, _LATEST_CHECKPOINT)
        wal = Path(str(sqlite_path) + "-wal")
        shm = Path(str(sqlite_path) + "-shm")
        assert wal.exists() or shm.exists()
        tables_before, counts_before = _schema_and_counts(writer.conn)
        writer_latest = writer.get_tuple(thread_config(run_id))
        assert writer_latest is not None
        assert writer_latest.checkpoint["id"] == _LATEST_CHECKPOINT["id"]
        assert writer_latest.checkpoint["channel_values"] == {"k": 2}

        with open_readonly_checkpointer(sqlite_path) as reader:
            got = reader.get_tuple(thread_config(run_id))
            assert got is not None
            assert got.checkpoint["id"] == _LATEST_CHECKPOINT["id"]
            assert got.checkpoint["channel_values"] == {"k": 2}

        tables_after, counts_after = _schema_and_counts(writer.conn)
        still = writer.get_tuple(thread_config(run_id))
        assert still is not None
        assert still.checkpoint["id"] == _LATEST_CHECKPOINT["id"]
        assert still.checkpoint["channel_values"] == {"k": 2}
        assert tables_after == tables_before
        assert counts_after == counts_before

    tables_closed, counts_closed = _schema_and_counts(sqlite3.connect(str(sqlite_path)))
    assert tables_closed == tables_before
    assert counts_closed == counts_before


def test_discover_runs_one_hit_when_file_has_extra_thread_and_ns(tmp_path):
    agent_works_root = tmp_path / "agent_works"
    graph_name = "feature-kickoff"
    run_id = "20260101T000000_nested"
    extra_checkpoint = {
        "ts": "2024-05-04T06:34:00.000000+00:00",
        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1",
        "channel_values": {"k": 9},
    }
    ns_checkpoint = {
        "ts": "2024-05-04T06:35:00.000000+00:00",
        "id": "cccccccc-cccc-cccc-cccc-ccccccccccc1",
        "channel_values": {"k": 8},
    }
    with open_checkpointer(graph_name, run_id, agent_works_root) as saver:
        _put(saver, run_id, _PARENT_CHECKPOINT)
        _put(saver, f"{run_id}:item-1", extra_checkpoint)
        _put(saver, run_id, ns_checkpoint, checkpoint_ns="run_tasks_node:deadbeef")

    hits = discover_runs(agent_works_root)

    assert len(hits) == 1
    assert hits[0]["graph_name"] == graph_name
    assert hits[0]["run_id"] == run_id
    assert hits[0]["run_dir"] == run_dir_for(graph_name, run_id, agent_works_root)
    assert hits[0]["checkpoint_path"] == checkpoint_path_for(
        graph_name, run_id, agent_works_root
    )


def test_readonly_opener_does_not_mkdir(tmp_path):
    missing = tmp_path / "absent-dir" / "checkpoints.sqlite"
    try:
        with open_readonly_checkpointer(missing) as saver:
            saver.get_tuple(thread_config("nope"))
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("expected OperationalError opening missing sqlite")
    assert not (tmp_path / "absent-dir").exists()
