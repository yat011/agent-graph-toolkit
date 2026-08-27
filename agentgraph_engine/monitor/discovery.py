"""Discover Run directories under an agent_works root. One hit per Run folder."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TypedDict

from agentgraph_engine.monitor.checkpointer import open_readonly_checkpointer
from agentgraph_engine.runs import checkpoint_path_for, run_dir_for


class DiscoveredRun(TypedDict):
    graph_name: str
    run_id: str
    run_dir: Path
    checkpoint_path: Path


def discover_runs(agent_works_root: Path) -> list[DiscoveredRun]:
    """Return `{graph_name, run_id, run_dir, checkpoint_path}` for each `*/runs/*/`
    directory whose `checkpoints.sqlite` has a `thread_id == run_id` row.

    Missing sqlite, unreadable sqlite, or no parent-thread row: omit, do not raise.
    Extra `thread_id` / `checkpoint_ns` pairs in the same file are not extra hits.
    """
    root = Path(agent_works_root)
    hits: list[DiscoveredRun] = []
    if not root.is_dir():
        return hits
    for candidate in sorted(root.glob("*/runs/*")):
        if not candidate.is_dir():
            continue
        graph_name = candidate.parent.parent.name
        run_id = candidate.name
        checkpoint_path = checkpoint_path_for(graph_name, run_id, root)
        if not checkpoint_path.is_file():
            continue
        if not _has_parent_thread_row(checkpoint_path, run_id):
            continue
        hits.append(
            {
                "graph_name": graph_name,
                "run_id": run_id,
                "run_dir": run_dir_for(graph_name, run_id, root),
                "checkpoint_path": checkpoint_path,
            }
        )
    return hits


def _has_parent_thread_row(path: Path, run_id: str) -> bool:
    try:
        with open_readonly_checkpointer(path) as saver:
            with saver.cursor(transaction=False) as cur:
                cur.execute(
                    "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1",
                    (run_id,),
                )
                return cur.fetchone() is not None
    except (sqlite3.Error, OSError):
        return False
