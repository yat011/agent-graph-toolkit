"""standard-phase graph state — one nested record per node."""

from __future__ import annotations

from typing import Optional

from agentgraph_engine.states.base import BaseGraphState, BasicNodeState, GateNodeState


class StandardPhaseState(BaseGraphState, total=False):
    item: dict
    spec_path: Optional[str]
    plan_path: Optional[str]
    previous_handoff_path: Optional[str]
    review_line_threshold: Optional[int]
    implement_requirements_node: BasicNodeState
    review_node: GateNodeState
    skip_review_commit_node: BasicNodeState
