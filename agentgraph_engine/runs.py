"""Run id / path conventions and the SqliteSaver checkpointer (deliverable c).

One sqlite checkpoint file per Run at `agent_works/{graph_name}/runs/{run_id}/checkpoints.sqlite`,
with `thread_id == run_id` (CONTEXT.md's "Run" definition). `run_id` mirrors the old engine's
`{timestamp}_{slug}` run-folder convention. Note this path deliberately has no `graphs/` segment
between `agent_works` and `{graph_name}` — that folder layer belonged to the retired
graph.md/run-graph.js engine and is not part of the new convention.
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from langgraph.checkpoint.sqlite import SqliteSaver

DEFAULT_AGENT_WORKS_ROOT = Path("agent_works")


def slugify(text: str) -> str:
    """Non-alphanumeric runs collapsed to a single `-`, leading/trailing `-` trimmed."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug or "run"


def new_run_id(*, slug: Optional[str] = None, graph_name: Optional[str] = None) -> str:
    """`{timestamp}_{slug}`, falling back to `{timestamp}_{graph_name}` (or bare timestamp)."""
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    tail = slug or graph_name
    return f"{ts}_{slugify(tail)}" if tail else ts


def run_dir_for(
    graph_name: str, run_id: str, agent_works_root: Path = DEFAULT_AGENT_WORKS_ROOT
) -> Path:
    return agent_works_root / graph_name / "runs" / run_id


def checkpoint_path_for(
    graph_name: str, run_id: str, agent_works_root: Path = DEFAULT_AGENT_WORKS_ROOT
) -> Path:
    return run_dir_for(graph_name, run_id, agent_works_root) / "checkpoints.sqlite"


def node_output_path(run_dir: Path, node_id: str, attempt: int, filename: str = "output.md") -> Path:
    """`{run_dir}/{node_id}/attempt-{n}/{filename}` — the per-node artifact convention, for
    on-disk audit and path-based context passing between nodes (downstream nodes read a path,
    never get prior output pasted into their prompt).
    """
    return run_dir / node_id / f"attempt-{attempt}" / filename


@contextmanager
def open_checkpointer(
    graph_name: str, run_id: str, agent_works_root: Path = DEFAULT_AGENT_WORKS_ROOT
) -> Iterator[SqliteSaver]:
    """Context manager yielding the SqliteSaver for one Run, creating its folder if needed."""
    path = checkpoint_path_for(graph_name, run_id, agent_works_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        yield saver


def thread_config(run_id: str) -> dict:
    """LangGraph `config` dict for a Run — `thread_id == run_id` (CONTEXT.md's "Run" definition)."""
    return {"configurable": {"thread_id": run_id}}
