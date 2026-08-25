"""Node functions shared by every graph."""

from __future__ import annotations

from agentgraph_engine.pause import pause as pause


def halted(state: dict) -> dict:
    """No-op terminal used by hello_graph. Production templates pause via `pause` instead."""
    return {}
