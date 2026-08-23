"""hello_graph composed state records."""

from __future__ import annotations

from typing import Optional

from agentgraph_engine.states.base import BaseGraphState, BasicNodeState


class GreetState(BasicNodeState, total=False):
    greeting: str


class FanOutState(BasicNodeState, total=False):
    results: list


class DispatchWorkerState(BasicNodeState, total=False):
    ok: bool


class CheckerState(BasicNodeState, total=False):
    ok: bool


class HelloState(BaseGraphState, total=False):
    name: Optional[str]
    items: list
    greet_node: GreetState
    fan_out_node: FanOutState
    checkpoint_gate_node: BasicNodeState
    dispatch_worker_node: DispatchWorkerState
    checker_node: CheckerState
