"""feature-kickoff graph state — one nested record per node."""

from __future__ import annotations

from typing import Optional

from agentgraph_engine.states.base import BaseGraphState, BasicNodeState, GateNodeState
from agentgraph_engine.states.standard_task import StandardTaskState


class LoadTasksNodeState(GateNodeState, total=False):
    items: list


class FinalReviewNodeState(GateNodeState, total=False):
    stderr: Optional[str]
    stdout: Optional[str]
    returncode: Optional[int]


class FeatureKickoffState(BaseGraphState, total=False):
    spec_path: Optional[str]
    create_feature_branch_node: BasicNodeState
    planner_node: BasicNodeState
    tech_plan_reviewer_node: GateNodeState
    load_tasks_node: LoadTasksNodeState
    final_review_node: FinalReviewNodeState
    map_task_states: list[StandardTaskState]
