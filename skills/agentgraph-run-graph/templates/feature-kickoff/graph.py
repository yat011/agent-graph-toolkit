"""feature-kickoff — full feature-delivery graph.

    01_create_feature_branch -> 02_planner -> 03_tech_plan_reviewer
                                               |-[accepted]-----------------------> 04_load_tasks
                                               |-[rejected, attempted < 3]-------> 02_planner
                                               |-[reject×3]---------------------> pause_node
                                                 (redrive planner, reset)
                                               `-[manual / unrecognized]--------> pause_node
                                                 (redrive reviewer, reset)

    04_load_tasks
     |-[accepted, env working]-> pick_next_task <-> run_one_task (compiled standard-task)
     |                            `-[all items done]-------------------------------> 06_final_review
     `-[manual, env down]---------------------------------------------------------> pause_node
                                                                                    (redrive load_tasks)

    06_final_review
     |-[accepted]---------------> 09_success
     `-[manual / fail]----------> pause_node (redrive final_review)

Gates pause with `interrupt()` instead of routing to END. Nested standard-task runs share
this graph's checkpointer under a per-item thread_id. checkpoint_ns in the interrupt payload is metadata only — do not pass LangGraph checkpoint_ns on a separately compiled child invoke.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.constants import (
    CREATE_FEATURE_BRANCH_NODE,
    FINAL_REVIEW_NODE,
    HALTED_KEY,
    LOAD_TASKS_NODE,
    PAUSE_NODE,
    PICK_NEXT_TASK_NODE,
    PLANNER_NODE,
    RUN_ONE_TASK_NODE,
    SUCCESS_NODE,
    TECH_PLAN_REVIEWER_NODE,
)
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.nodes.common import pause
from agentgraph_engine.routing import GateConfig, gate_route
from agentgraph_engine.states.feature_kickoff import FeatureKickoffState

from .nodes import (
    STANDARD_TASK_GRAPH_PATH,
    create_feature_branch,
    final_review,
    load_tasks,
    make_run_one_task,
    pick_next_task,
    planner,
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
    return PAUSE_NODE if state.get(HALTED_KEY) else PLANNER_NODE


def route_after_planner(state: FeatureKickoffState) -> str:
    return PAUSE_NODE if state.get(HALTED_KEY) else TECH_PLAN_REVIEWER_NODE


def route_after_tech_review(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return PAUSE_NODE
    return gate_route(
        state,
        TECH_REVIEW_GATE,
        TECH_PLAN_REVIEWER_NODE,
        accept_target=LOAD_TASKS_NODE,
        manual_target=PAUSE_NODE,
    )


def route_after_load_tasks(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return PAUSE_NODE
    return gate_route(
        state,
        LOAD_TASKS_GATE,
        LOAD_TASKS_NODE,
        accept_target=PICK_NEXT_TASK_NODE,
        manual_target=PAUSE_NODE,
    )


def route_after_final_review(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return PAUSE_NODE
    return gate_route(
        state,
        FINAL_REVIEW_GATE,
        FINAL_REVIEW_NODE,
        accept_target=SUCCESS_NODE,
        manual_target=PAUSE_NODE,
    )


def build_graph(checkpointer=None):
    task_graph = get_build_graph(load_graph_module(STANDARD_TASK_GRAPH_PATH))(
        checkpointer=checkpointer
    )
    graph = StateGraph(FeatureKickoffState)
    graph.add_node(CREATE_FEATURE_BRANCH_NODE, create_feature_branch)
    graph.add_node(PLANNER_NODE, planner)
    graph.add_node(TECH_PLAN_REVIEWER_NODE, tech_plan_reviewer)
    graph.add_node(LOAD_TASKS_NODE, load_tasks)
    graph.add_node(
        PICK_NEXT_TASK_NODE,
        pick_next_task,
        destinations=(
            RUN_ONE_TASK_NODE,
            PAUSE_NODE,
            FINAL_REVIEW_NODE,
            PICK_NEXT_TASK_NODE,
        ),
    )
    graph.add_node(
        RUN_ONE_TASK_NODE,
        make_run_one_task(task_graph),
        destinations=(PICK_NEXT_TASK_NODE,),
    )
    graph.add_node(FINAL_REVIEW_NODE, final_review)
    graph.add_node(SUCCESS_NODE, success)
    graph.add_node(
        PAUSE_NODE,
        pause,
        destinations=(
            CREATE_FEATURE_BRANCH_NODE,
            PLANNER_NODE,
            TECH_PLAN_REVIEWER_NODE,
            LOAD_TASKS_NODE,
            PICK_NEXT_TASK_NODE,
            FINAL_REVIEW_NODE,
        ),
    )

    graph.add_edge(START, CREATE_FEATURE_BRANCH_NODE)
    graph.add_conditional_edges(
        CREATE_FEATURE_BRANCH_NODE,
        route_after_branch,
        {PLANNER_NODE: PLANNER_NODE, PAUSE_NODE: PAUSE_NODE},
    )
    graph.add_conditional_edges(
        PLANNER_NODE,
        route_after_planner,
        {TECH_PLAN_REVIEWER_NODE: TECH_PLAN_REVIEWER_NODE, PAUSE_NODE: PAUSE_NODE},
    )
    graph.add_conditional_edges(
        TECH_PLAN_REVIEWER_NODE,
        route_after_tech_review,
        {
            LOAD_TASKS_NODE: LOAD_TASKS_NODE,
            PLANNER_NODE: PLANNER_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_conditional_edges(
        LOAD_TASKS_NODE,
        route_after_load_tasks,
        {
            PICK_NEXT_TASK_NODE: PICK_NEXT_TASK_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_conditional_edges(
        FINAL_REVIEW_NODE,
        route_after_final_review,
        {
            SUCCESS_NODE: SUCCESS_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_edge(SUCCESS_NODE, END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
