"""feature-kickoff graph state — one nested record per node."""

from __future__ import annotations

from typing import Optional

from agentgraph_engine.states.base import BaseGraphState, BasicNodeState, GateNodeState
from agentgraph_engine.states.standard_phase import StandardPhaseState


class LoadPhasesNodeState(GateNodeState, total=False):
    items: list


class AdditionalTestNodeState(GateNodeState, total=False):
    stderr: Optional[str]
    stdout: Optional[str]
    returncode: Optional[int]


class FeatureKickoffState(BaseGraphState, total=False):
    spec_path: Optional[str]
    plan_path: Optional[str]
    review_line_threshold: Optional[int]
    create_feature_branch_node: BasicNodeState
    planner_node: BasicNodeState
    tech_plan_reviewer_node: GateNodeState
    load_phases_node: LoadPhasesNodeState
    additional_test_node: AdditionalTestNodeState
    integration_fix_node: BasicNodeState
    final_reviewer_node: GateNodeState
    map_phase_states: list[StandardPhaseState]
    current_item: Optional[dict]
    current_item_index: Optional[int]
    parent_node: Optional[str]
    nested_checkpoint_ns: Optional[str]
