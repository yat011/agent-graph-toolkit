"""standard-task — per-task subgraph.

Loaded dynamically via `agentgraph_engine.graph_loader` — never copied into a project's
`agent_works/` (CONTEXT.md's "Template graph").

    02_implement_requirements
     |-[implemented]-------------------------> 03_review
     `-[stopped / anything else]-------------> 05_manual_flag

    03_review
     |-[accepted]-----------------------------> 04_success
     |-[rejected, attempts < 3]---------------> 02_implement_requirements  (loop back)
     `-[rejected, attempts = 3]----------------> 05_manual_flag

Branch matching is plain Python string-matching against the `Result:` line each node's prompt
requires — never an LLM judgment call. A `claude` CLI dispatch that fails/errors after
exhausting `retry` attempts routes to the shared `halted_node` terminal, same as any other
technical failure. An unrecognized `Result:` line on a gate routes to manual immediately
(`halt_reason: unrecognized_result`).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.constants import (
    HALTED_KEY,
    HALTED_NODE,
    IMPLEMENT_REQUIREMENTS_NODE,
    MANUAL_FLAG_NODE,
    RESULT_IMPLEMENTED,
    RESULT_KEY,
    REVIEW_NODE,
    SUCCESS_NODE,
)
from agentgraph_engine.nodes.common import halted
from agentgraph_engine.routing import GateConfig, gate_route, matches_result_keyword
from agentgraph_engine.states.standard_task import StandardTaskState

from .nodes import implement_requirements, manual_flag, review, success

REVIEW_GATE = GateConfig(
    retry_target=IMPLEMENT_REQUIREMENTS_NODE,
    max_retry_attempts=3,
)


def route_after_implement(state: StandardTaskState) -> str:
    if state.get(HALTED_KEY):
        return HALTED_NODE
    line = ((state.get(IMPLEMENT_REQUIREMENTS_NODE) or {}).get(RESULT_KEY) or "").strip()
    if matches_result_keyword(line, RESULT_IMPLEMENTED):
        return REVIEW_NODE
    return MANUAL_FLAG_NODE


def route_after_review(state: StandardTaskState) -> str:
    if state.get(HALTED_KEY):
        return HALTED_NODE
    return gate_route(
        state,
        REVIEW_GATE,
        REVIEW_NODE,
        accept_target=SUCCESS_NODE,
        manual_target=MANUAL_FLAG_NODE,
    )


def build_graph(checkpointer=None):
    graph = StateGraph(StandardTaskState)
    graph.add_node(IMPLEMENT_REQUIREMENTS_NODE, implement_requirements)
    graph.add_node(REVIEW_NODE, review)
    graph.add_node(SUCCESS_NODE, success)
    graph.add_node(MANUAL_FLAG_NODE, manual_flag)
    graph.add_node(HALTED_NODE, halted)

    graph.add_edge(START, IMPLEMENT_REQUIREMENTS_NODE)
    graph.add_conditional_edges(
        IMPLEMENT_REQUIREMENTS_NODE,
        route_after_implement,
        {
            REVIEW_NODE: REVIEW_NODE,
            MANUAL_FLAG_NODE: MANUAL_FLAG_NODE,
            HALTED_NODE: HALTED_NODE,
        },
    )
    graph.add_conditional_edges(
        REVIEW_NODE,
        route_after_review,
        {
            SUCCESS_NODE: SUCCESS_NODE,
            IMPLEMENT_REQUIREMENTS_NODE: IMPLEMENT_REQUIREMENTS_NODE,
            MANUAL_FLAG_NODE: MANUAL_FLAG_NODE,
            HALTED_NODE: HALTED_NODE,
        },
    )
    graph.add_edge(SUCCESS_NODE, END)
    graph.add_edge(MANUAL_FLAG_NODE, END)
    graph.add_edge(HALTED_NODE, END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
