"""feature-kickoff — full feature-delivery graph.

    01_create_feature_branch -> 02_planner -> 03_tech_plan_reviewer
                                               |-[accepted]-----------------------> 04_load_tasks
                                               |-[rejected, attempted 3x]---------> 07_blocked_plan_rejected
                                               `-[rejected, attempted < 3x]-------> 02_planner (loop back)

    04_load_tasks
     |-[accepted, env working]-> 05_run_tasks (sequential map: standard-task per task)
     |                            |-[any item 05_manual_flag]------------------------> 08_needs_manual_review
     |                            `-[no item flagged]--------------------------------> 06_final_review
     |                                                                                   |-[script exit 0]-> 09_success
     |                                                                                   `-[script fail/missing]-> 08_needs_manual_review
     `-[manual, env down]--------------------------------------------------------------------------------> 08_needs_manual_review

    07_blocked_plan_rejected   (terminal)
    08_needs_manual_review     (terminal)
    09_success                 (terminal)

05_run_tasks embeds the standard-task template as a reusable, compiled sub-StateGraph — one full
recursion of standard-task per task item, dispatched strictly sequentially (no concurrent
dispatch), honoring each item's `dependencies` list: a permanently-blocked item (a missing
dependency id, or one that finished at manual_flag) is left `blocked` rather than halting the
whole map; a genuine cycle (nothing ready, something still waiting) halts with
`unmet_dependencies`. After the map, any item that finished at `manual_flag` skips
06_final_review and routes to 08_needs_manual_review. An unrecognized `Result:` line on a gate
routes to manual immediately (`halt_reason: unrecognized_result`).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.constants import (
    BLOCKED_PLAN_REJECTED_NODE,
    CREATE_FEATURE_BRANCH_NODE,
    FINAL_REVIEW_NODE,
    HALTED_KEY,
    HALTED_NODE,
    LOAD_TASKS_NODE,
    MAP_TASK_STATES_KEY,
    NEEDS_MANUAL_REVIEW_NODE,
    OUTCOME_KEY,
    OUTCOME_MANUAL_FLAG,
    PLANNER_NODE,
    RUN_TASKS_NODE,
    SUCCESS_NODE,
    TECH_PLAN_REVIEWER_NODE,
)
from agentgraph_engine.nodes.common import halted
from agentgraph_engine.routing import GateConfig, gate_route
from agentgraph_engine.states.feature_kickoff import FeatureKickoffState

from .nodes import (
    blocked_plan_rejected,
    create_feature_branch,
    final_review,
    load_tasks,
    needs_manual_review,
    planner,
    run_tasks,
    success,
    tech_plan_reviewer,
)

TECH_REVIEW_GATE = GateConfig(
    retry_target=PLANNER_NODE,
    max_retry_attempts=3,
)

LOAD_TASKS_GATE = GateConfig()

FINAL_REVIEW_GATE = GateConfig()


def route_after_branch(state: FeatureKickoffState) -> str:
    return HALTED_NODE if state.get(HALTED_KEY) else PLANNER_NODE


def route_after_planner(state: FeatureKickoffState) -> str:
    return HALTED_NODE if state.get(HALTED_KEY) else TECH_PLAN_REVIEWER_NODE


def route_after_tech_review(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return HALTED_NODE
    return gate_route(
        state,
        TECH_REVIEW_GATE,
        TECH_PLAN_REVIEWER_NODE,
        accept_target=LOAD_TASKS_NODE,
        manual_target=BLOCKED_PLAN_REJECTED_NODE,
    )


def route_after_load_tasks(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return HALTED_NODE
    return gate_route(
        state,
        LOAD_TASKS_GATE,
        LOAD_TASKS_NODE,
        accept_target=RUN_TASKS_NODE,
        manual_target=NEEDS_MANUAL_REVIEW_NODE,
    )


def _map_has_manual_flag(state: FeatureKickoffState) -> bool:
    for child in state.get(MAP_TASK_STATES_KEY) or []:
        if child.get(OUTCOME_KEY) == OUTCOME_MANUAL_FLAG:
            return True
    return False


def route_after_run_tasks(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return HALTED_NODE
    if _map_has_manual_flag(state):
        return NEEDS_MANUAL_REVIEW_NODE
    return FINAL_REVIEW_NODE


def route_after_final_review(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return HALTED_NODE
    return gate_route(
        state,
        FINAL_REVIEW_GATE,
        FINAL_REVIEW_NODE,
        accept_target=SUCCESS_NODE,
        manual_target=NEEDS_MANUAL_REVIEW_NODE,
    )


def build_graph(checkpointer=None):
    graph = StateGraph(FeatureKickoffState)
    graph.add_node(CREATE_FEATURE_BRANCH_NODE, create_feature_branch)
    graph.add_node(PLANNER_NODE, planner)
    graph.add_node(TECH_PLAN_REVIEWER_NODE, tech_plan_reviewer)
    graph.add_node(LOAD_TASKS_NODE, load_tasks)
    graph.add_node(RUN_TASKS_NODE, run_tasks)
    graph.add_node(FINAL_REVIEW_NODE, final_review)
    graph.add_node(BLOCKED_PLAN_REJECTED_NODE, blocked_plan_rejected)
    graph.add_node(NEEDS_MANUAL_REVIEW_NODE, needs_manual_review)
    graph.add_node(SUCCESS_NODE, success)
    graph.add_node(HALTED_NODE, halted)

    graph.add_edge(START, CREATE_FEATURE_BRANCH_NODE)
    graph.add_conditional_edges(
        CREATE_FEATURE_BRANCH_NODE,
        route_after_branch,
        {PLANNER_NODE: PLANNER_NODE, HALTED_NODE: HALTED_NODE},
    )
    graph.add_conditional_edges(
        PLANNER_NODE,
        route_after_planner,
        {TECH_PLAN_REVIEWER_NODE: TECH_PLAN_REVIEWER_NODE, HALTED_NODE: HALTED_NODE},
    )
    graph.add_conditional_edges(
        TECH_PLAN_REVIEWER_NODE,
        route_after_tech_review,
        {
            LOAD_TASKS_NODE: LOAD_TASKS_NODE,
            PLANNER_NODE: PLANNER_NODE,
            BLOCKED_PLAN_REJECTED_NODE: BLOCKED_PLAN_REJECTED_NODE,
            HALTED_NODE: HALTED_NODE,
        },
    )
    graph.add_conditional_edges(
        LOAD_TASKS_NODE,
        route_after_load_tasks,
        {
            RUN_TASKS_NODE: RUN_TASKS_NODE,
            NEEDS_MANUAL_REVIEW_NODE: NEEDS_MANUAL_REVIEW_NODE,
            HALTED_NODE: HALTED_NODE,
        },
    )
    graph.add_conditional_edges(
        RUN_TASKS_NODE,
        route_after_run_tasks,
        {
            FINAL_REVIEW_NODE: FINAL_REVIEW_NODE,
            NEEDS_MANUAL_REVIEW_NODE: NEEDS_MANUAL_REVIEW_NODE,
            HALTED_NODE: HALTED_NODE,
        },
    )
    graph.add_conditional_edges(
        FINAL_REVIEW_NODE,
        route_after_final_review,
        {
            SUCCESS_NODE: SUCCESS_NODE,
            NEEDS_MANUAL_REVIEW_NODE: NEEDS_MANUAL_REVIEW_NODE,
            HALTED_NODE: HALTED_NODE,
        },
    )
    graph.add_edge(BLOCKED_PLAN_REJECTED_NODE, END)
    graph.add_edge(NEEDS_MANUAL_REVIEW_NODE, END)
    graph.add_edge(SUCCESS_NODE, END)
    graph.add_edge(HALTED_NODE, END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
