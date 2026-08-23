"""hello_graph — minimal worked example exercising every primitive this engine supports:

    greet (sequence) -> fan_out (sequential map/fan-out) -> checkpoint_gate (interrupt(), used by
    the checkpoint-resume proof in tests/test_checkpoint_resume.py) -> dispatch_worker (one real
    headless-CLI Worker dispatch) -> checker (branch node) -> pass_terminal | fail_terminal

Lives inside the engine package itself, not under skills/agentgraph-run-graph/templates/ (which
is reserved for the two production graphs) and not under agent_works/ (which is per-project
run-instance data, not source). Loaded the same way any real template would be, via
`agentgraph_engine.graph_loader`.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.constants import (
    HALTED_KEY,
    HALTED_NODE,
)
from agentgraph_engine.nodes.common import halted

from .nodes import (
    CHECKER_NODE,
    CHECKPOINT_GATE_NODE,
    DISPATCH_WORKER_NODE,
    FAIL_TERMINAL_NODE,
    FAN_OUT_NODE,
    GREET_NODE,
    OK_KEY,
    PASS_TERMINAL_NODE,
    checker,
    checkpoint_gate,
    dispatch_worker,
    fail_terminal,
    fan_out,
    greet,
    pass_terminal,
)
from .state import HelloState


def route_after_worker(state: HelloState) -> str:
    return HALTED_NODE if state.get(HALTED_KEY) else CHECKER_NODE


def route_after_checker(state: HelloState) -> str:
    return PASS_TERMINAL_NODE if (state.get(CHECKER_NODE) or {}).get(OK_KEY) else FAIL_TERMINAL_NODE


def build_graph(checkpointer=None):
    graph = StateGraph(HelloState)
    graph.add_node(GREET_NODE, greet)
    graph.add_node(FAN_OUT_NODE, fan_out)
    graph.add_node(CHECKPOINT_GATE_NODE, checkpoint_gate)
    graph.add_node(DISPATCH_WORKER_NODE, dispatch_worker)
    graph.add_node(CHECKER_NODE, checker)
    graph.add_node(PASS_TERMINAL_NODE, pass_terminal)
    graph.add_node(FAIL_TERMINAL_NODE, fail_terminal)
    graph.add_node(HALTED_NODE, halted)

    graph.add_edge(START, GREET_NODE)
    graph.add_edge(GREET_NODE, FAN_OUT_NODE)
    graph.add_edge(FAN_OUT_NODE, CHECKPOINT_GATE_NODE)
    graph.add_edge(CHECKPOINT_GATE_NODE, DISPATCH_WORKER_NODE)
    graph.add_conditional_edges(
        DISPATCH_WORKER_NODE,
        route_after_worker,
        {CHECKER_NODE: CHECKER_NODE, HALTED_NODE: HALTED_NODE},
    )
    graph.add_conditional_edges(
        CHECKER_NODE,
        route_after_checker,
        {PASS_TERMINAL_NODE: PASS_TERMINAL_NODE, FAIL_TERMINAL_NODE: FAIL_TERMINAL_NODE},
    )
    graph.add_edge(PASS_TERMINAL_NODE, END)
    graph.add_edge(FAIL_TERMINAL_NODE, END)
    graph.add_edge(HALTED_NODE, END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
