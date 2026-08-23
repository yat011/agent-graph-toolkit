"""standard-task graph state — one nested record per node."""

from __future__ import annotations

from agentgraph_engine.states.base import BaseGraphState, BasicNodeState, GateNodeState


class StandardTaskState(BaseGraphState, total=False):
    item: dict
    implement_requirements_node: BasicNodeState
    review_node: GateNodeState
