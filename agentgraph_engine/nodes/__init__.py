"""Engine-shared node functions. Template-specific nodes live next to each template's graph.py."""

from __future__ import annotations

from agentgraph_engine.nodes.common import halted, pause

__all__ = ["halted", "pause"]
