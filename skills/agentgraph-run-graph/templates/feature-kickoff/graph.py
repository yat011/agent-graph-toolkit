"""feature-kickoff — full feature-delivery graph.

    01_create_feature_branch -> 02_planner -> 03_tech_plan_reviewer
                                               |-[accepted]-----------------------> 04_load_phases
                                               |-[rejected, attempted < 3]-------> 02_planner
                                               |-[reject×3]---------------------> pause_node
                                                 (redrive planner, reset)
                                               `-[manual / unrecognized]--------> pause_node
                                                 (redrive reviewer, reset)

    04_load_phases
     |-[accepted, env working]-> pick_next_phase <-> run_one_phase (compiled standard-phase)
     |                            `-[all items done]-------------------------------> 06_additional_test
     `-[manual, env down]---------------------------------------------------------> pause_node
                                                                                    (redrive load_phases)

    06_additional_test (script, no Worker)
     |-[accepted]---------------> 08_final_reviewer
     |-[rejected, fix unused]---> 07_integration_fix -> 06_additional_test
     |-[rejected, fix used]-----> pause_node (redrive integration_fix)
     `-[manual / incomplete]----> pause_node (redrive additional_test)

    08_final_reviewer
     |-[accepted]---------------> 09_success
     `-[reject / manual]--------> pause_node (redrive final_reviewer)

Gates pause with `interrupt()` instead of routing to END. Nested standard-phase runs share
this graph's checkpointer under a per-item thread_id. checkpoint_ns in the interrupt payload is metadata only — do not pass LangGraph checkpoint_ns on a separately compiled child invoke.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.constants import (
    ADDITIONAL_TEST_NODE,
    CREATE_FEATURE_BRANCH_NODE,
    FINAL_REVIEWER_NODE,
    HALTED_KEY,
    INTEGRATION_FIX_NODE,
    LOAD_PHASES_NODE,
    PAUSE_NODE,
    PICK_NEXT_PHASE_NODE,
    PLANNER_NODE,
    RESULT_IMPLEMENTED,
    RESULT_KEY,
    RUN_ONE_PHASE_NODE,
    SUCCESS_NODE,
    TECH_PLAN_REVIEWER_NODE,
)
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.nodes.common import pause
from agentgraph_engine.routing import GateConfig, gate_route, matches_result_keyword
from agentgraph_engine.states.feature_kickoff import FeatureKickoffState

from .nodes import (
    STANDARD_PHASE_GRAPH_PATH,
    additional_test,
    create_feature_branch,
    final_reviewer,
    integration_fix,
    load_phases,
    make_run_one_phase,
    pick_next_phase,
    planner,
    success,
    tech_plan_reviewer,
)

TECH_REVIEW_GATE = GateConfig(
    retry_target=PLANNER_NODE,
    max_retry_attempts=3,
)

LOAD_PHASES_GATE = GateConfig()

ADDITIONAL_TEST_GATE = GateConfig(
    retry_target=INTEGRATION_FIX_NODE,
    max_retry_attempts=1,
)

FINAL_REVIEWER_GATE = GateConfig()


def _record(state: dict, node_id: str) -> dict:
    value = state.get(node_id)
    return value if isinstance(value, dict) else {}


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
        accept_target=LOAD_PHASES_NODE,
        manual_target=PAUSE_NODE,
    )


def route_after_load_phases(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return PAUSE_NODE
    return gate_route(
        state,
        LOAD_PHASES_GATE,
        LOAD_PHASES_NODE,
        accept_target=PICK_NEXT_PHASE_NODE,
        manual_target=PAUSE_NODE,
    )


def route_after_additional_test(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return PAUSE_NODE
    return gate_route(
        state,
        ADDITIONAL_TEST_GATE,
        ADDITIONAL_TEST_NODE,
        accept_target=FINAL_REVIEWER_NODE,
        manual_target=PAUSE_NODE,
    )


def route_after_integration_fix(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return PAUSE_NODE
    line = (_record(state, INTEGRATION_FIX_NODE).get(RESULT_KEY) or "").strip()
    if matches_result_keyword(line, RESULT_IMPLEMENTED):
        return ADDITIONAL_TEST_NODE
    return PAUSE_NODE


def route_after_final_reviewer(state: FeatureKickoffState) -> str:
    if state.get(HALTED_KEY):
        return PAUSE_NODE
    return gate_route(
        state,
        FINAL_REVIEWER_GATE,
        FINAL_REVIEWER_NODE,
        accept_target=SUCCESS_NODE,
        manual_target=PAUSE_NODE,
    )


def build_graph(checkpointer=None):
    phase_graph = get_build_graph(load_graph_module(STANDARD_PHASE_GRAPH_PATH))(
        checkpointer=checkpointer
    )
    graph = StateGraph(FeatureKickoffState)
    graph.add_node(CREATE_FEATURE_BRANCH_NODE, create_feature_branch)
    graph.add_node(PLANNER_NODE, planner)
    graph.add_node(TECH_PLAN_REVIEWER_NODE, tech_plan_reviewer)
    graph.add_node(LOAD_PHASES_NODE, load_phases)
    graph.add_node(
        PICK_NEXT_PHASE_NODE,
        pick_next_phase,
        destinations=(
            RUN_ONE_PHASE_NODE,
            PAUSE_NODE,
            ADDITIONAL_TEST_NODE,
            PICK_NEXT_PHASE_NODE,
        ),
    )
    graph.add_node(
        RUN_ONE_PHASE_NODE,
        make_run_one_phase(phase_graph),
        destinations=(PICK_NEXT_PHASE_NODE, PAUSE_NODE),
    )
    graph.add_node(ADDITIONAL_TEST_NODE, additional_test)
    graph.add_node(INTEGRATION_FIX_NODE, integration_fix)
    graph.add_node(FINAL_REVIEWER_NODE, final_reviewer)
    graph.add_node(SUCCESS_NODE, success)
    graph.add_node(
        PAUSE_NODE,
        pause,
        destinations=(
            CREATE_FEATURE_BRANCH_NODE,
            PLANNER_NODE,
            TECH_PLAN_REVIEWER_NODE,
            LOAD_PHASES_NODE,
            PICK_NEXT_PHASE_NODE,
            RUN_ONE_PHASE_NODE,
            ADDITIONAL_TEST_NODE,
            INTEGRATION_FIX_NODE,
            FINAL_REVIEWER_NODE,
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
            LOAD_PHASES_NODE: LOAD_PHASES_NODE,
            PLANNER_NODE: PLANNER_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_conditional_edges(
        LOAD_PHASES_NODE,
        route_after_load_phases,
        {
            PICK_NEXT_PHASE_NODE: PICK_NEXT_PHASE_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_conditional_edges(
        ADDITIONAL_TEST_NODE,
        route_after_additional_test,
        {
            FINAL_REVIEWER_NODE: FINAL_REVIEWER_NODE,
            INTEGRATION_FIX_NODE: INTEGRATION_FIX_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_conditional_edges(
        INTEGRATION_FIX_NODE,
        route_after_integration_fix,
        {
            ADDITIONAL_TEST_NODE: ADDITIONAL_TEST_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_conditional_edges(
        FINAL_REVIEWER_NODE,
        route_after_final_reviewer,
        {
            SUCCESS_NODE: SUCCESS_NODE,
            PAUSE_NODE: PAUSE_NODE,
        },
    )
    graph.add_edge(SUCCESS_NODE, END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
