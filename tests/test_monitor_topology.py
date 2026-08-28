"""Per-Run ASCII topology with current-node highlight."""

from __future__ import annotations

from agentgraph_engine.examples.hello_graph.graph import graph
from agentgraph_engine.examples.hello_graph.nodes import (
    CHECKER_NODE,
    CHECKPOINT_GATE_NODE,
    FAN_OUT_NODE,
    GREET_NODE,
)
from agentgraph_engine.monitor.topology import topology_ascii


def test_ascii_contains_known_node_ids():
    ascii_art = topology_ascii(graph, {"current_node": None})
    assert GREET_NODE in ascii_art
    assert FAN_OUT_NODE in ascii_art
    assert CHECKPOINT_GATE_NODE in ascii_art


def test_current_node_label_is_wrapped_and_others_are_not():
    ascii_art = topology_ascii(graph, {"current_node": CHECKPOINT_GATE_NODE})
    assert f"*{CHECKPOINT_GATE_NODE}*" in ascii_art
    assert f"*{GREET_NODE}*" not in ascii_art
    assert f"*{CHECKER_NODE}*" not in ascii_art


def test_different_current_nodes_produce_different_strings():
    greet_view = topology_ascii(graph, {"current_node": GREET_NODE})
    gate_view = topology_ascii(graph, {"current_node": CHECKPOINT_GATE_NODE})
    assert greet_view != gate_view


def test_missing_or_unknown_current_node_returns_unhighlighted_ascii():
    baseline = topology_ascii(graph, {"current_node": None})
    unknown = topology_ascii(graph, {"current_node": "item 2 of 3"})
    missing = topology_ascii(graph, {})
    assert unknown == baseline
    assert missing == baseline
