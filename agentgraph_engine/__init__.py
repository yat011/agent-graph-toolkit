"""agentgraph_engine — LangGraph-based execution engine for agent-graph-toolkit.

This package holds only the mechanics shared by every ported Graph: dispatching Workers via a
headless CLI (`dispatch.py`), Run/checkpoint path conventions (`runs.py`), and dynamic loading of
a `graph.py` template by path (`graph_loader.py`). Graphs themselves (feature-kickoff,
standard-task) are plain LangGraph `StateGraph` code living under
`skills/agentgraph-run-graph/templates/`, not inside this package.
"""
