"""Shared graph.py resolution for one Run: derive its `--agent-works-root` from `run_dir` and
compile a named graph against a checkpointer. Used both for a Run's own graph (status, topology)
and for a nested child thread's graph, which is always `standard-task` regardless of the parent's
`graph_name` (`nodes.py:645-680` dispatches every child as a standard-task Run).
"""

from __future__ import annotations

from pathlib import Path

from agentgraph_engine.graph_loader import get_build_graph, load_graph_module, resolve_graph_path
from agentgraph_engine.monitor.discovery import DiscoveredRun

CHILD_GRAPH_NAME = "standard-task"


def agent_works_root_for(run: DiscoveredRun) -> Path:
    return Path(run["run_dir"]).parent.parent.parent


def compiled_graph_for(run: DiscoveredRun, graph_name: str, checkpointer: object) -> object:
    agent_works_root = agent_works_root_for(run)
    path = resolve_graph_path(graph_name, project_graphs_root=agent_works_root / "graphs")
    build_graph = get_build_graph(load_graph_module(path))
    return build_graph(checkpointer=checkpointer)
