"""agentgraph_engine — LangGraph-based execution engine for agent-graph-toolkit.

This package holds the mechanics shared by every Graph: dispatching Workers via a headless CLI
(`dispatch.py`), Run/checkpoint path conventions (`runs.py`), dynamic loading of a `graph.py` by
path (`graph_loader.py`), shared constants (`constants.py`), composed per-node state records
(`states/`), gate routing (`routing.py`), and the shared `halted` terminal (`nodes/common.py`).
Graphs themselves (feature-kickoff, standard-phase) are plain LangGraph `StateGraph` code living
under `skills/agentgraph-run-graph/templates/`, not inside this package.
"""
