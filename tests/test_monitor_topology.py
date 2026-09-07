"""Per-Run ASCII topology with current-node highlight."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.monitor.topology import topology_ascii

GREET_NODE = "greet_node"
FAN_OUT_NODE = "fan_out_node"
GATE_NODE = "gate_node"


class _State(TypedDict, total=False):
    x: int
    greet_node: dict
    fan_out_node: dict
    gate_node: dict


def _build_graph():
    def _greet(state):
        return {GREET_NODE: {"ok": True}}

    def _fan_out(state):
        return {FAN_OUT_NODE: {"ok": True}}

    def _gate(state):
        return {GATE_NODE: {"ok": True}}

    graph = StateGraph(_State)
    graph.add_node(GREET_NODE, _greet)
    graph.add_node(FAN_OUT_NODE, _fan_out)
    graph.add_node(GATE_NODE, _gate)
    graph.add_edge(START, GREET_NODE)
    graph.add_edge(GREET_NODE, FAN_OUT_NODE)
    graph.add_edge(FAN_OUT_NODE, GATE_NODE)
    graph.add_edge(GATE_NODE, END)
    return graph.compile()


graph = _build_graph()


def test_ascii_contains_known_node_ids():
    ascii_art = topology_ascii(graph, {"current_node": None})
    assert GREET_NODE in ascii_art
    assert FAN_OUT_NODE in ascii_art
    assert GATE_NODE in ascii_art


def test_current_node_label_is_wrapped_and_others_are_not():
    ascii_art = topology_ascii(graph, {"current_node": GATE_NODE})
    assert f"*{GATE_NODE}*" in ascii_art
    assert f"*{GREET_NODE}*" not in ascii_art
    assert f"*{FAN_OUT_NODE}*" not in ascii_art


def test_different_current_nodes_produce_different_strings():
    greet_view = topology_ascii(graph, {"current_node": GREET_NODE})
    gate_view = topology_ascii(graph, {"current_node": GATE_NODE})
    assert greet_view != gate_view


def test_missing_or_unknown_current_node_returns_unhighlighted_ascii():
    baseline = topology_ascii(graph, {"current_node": None})
    unknown = topology_ascii(graph, {"current_node": "item 2 of 3"})
    missing = topology_ascii(graph, {})
    assert unknown == baseline
    assert missing == baseline
