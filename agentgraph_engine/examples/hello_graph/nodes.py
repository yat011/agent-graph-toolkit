"""hello_graph node functions."""

from __future__ import annotations

from pathlib import Path

from langgraph.types import interrupt

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    HALT_REASON_KEY,
    HALT_RETRIES_EXHAUSTED,
    ITEMS_KEY,
    MODEL_CHEAP,
    OUTCOME_KEY,
    OUTPUT_PATH_KEY,
    RESULT_KEY,
    ROLE_GENERAL_PURPOSE,
    RUN_DIR_KEY,
)
from agentgraph_engine.dispatch import attach_usage, dispatch_with_retry
from agentgraph_engine.routing import matches_result_keyword
from .state import HelloState

GREET_NODE = "greet_node"
FAN_OUT_NODE = "fan_out_node"
CHECKPOINT_GATE_NODE = "checkpoint_gate_node"
DISPATCH_WORKER_NODE = "dispatch_worker_node"
CHECKER_NODE = "checker_node"
PASS_TERMINAL_NODE = "pass_terminal_node"
FAIL_TERMINAL_NODE = "fail_terminal_node"

OK_KEY = "ok"
RESULTS_KEY = "results"
GREETING_KEY = "greeting"
NAME_KEY = "name"
OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
RESULT_GREETED = "greeted"


def _record(state: dict, node_id: str) -> dict:
    value = state.get(node_id)
    return value if isinstance(value, dict) else {}


def greet(state: HelloState) -> dict:
    return {GREET_NODE: {ATTEMPT_COUNT_KEY: 1, GREETING_KEY: f"Hello, {state.get(NAME_KEY) or 'world'}!"}}


def fan_out(state: HelloState) -> dict:
    """Sequential map/fan-out: transforms each item one at a time, in order (no concurrent
    dispatch).
    """
    items = state.get(ITEMS_KEY) or ["alpha", "beta", "gamma"]
    results = [str(item).upper() for item in items]
    return {FAN_OUT_NODE: {ATTEMPT_COUNT_KEY: 1, RESULTS_KEY: results}}


def checkpoint_gate(state: HelloState) -> dict:
    """The sole interrupt() point in this example — used to demonstrate checkpoint-resume."""
    ack = interrupt({"message": "hello_graph paused after fan-out — resume to continue"})
    return {CHECKPOINT_GATE_NODE: {ATTEMPT_COUNT_KEY: 1, RESULT_KEY: ack}}


def dispatch_worker(state: HelloState) -> dict:
    """The one CLI-dispatched Worker node in this example."""
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = run_dir / "dispatch_worker" / "attempt-1" / "output.md"
    record: dict = {ATTEMPT_COUNT_KEY: 1, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=0,
        role=ROLE_GENERAL_PURPOSE,
        task_prompt=f"""Say hello in one short sentence. End your output with a single-line `Result: {RESULT_GREETED}` conclusion.
""",
        output_path=output_path,
        model=MODEL_CHEAP,
    )
    attach_usage(record, result)
    if not result.ok:
        record[OK_KEY] = False
        return {
            DISPATCH_WORKER_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: DISPATCH_WORKER_NODE,
        }
    record[OK_KEY] = True
    record[RESULT_KEY] = result.result_line
    return {DISPATCH_WORKER_NODE: record}


def checker(state: HelloState) -> dict:
    """Checker/branch node: plain-code decision, no LLM judgment."""
    items = state.get(ITEMS_KEY) or ["alpha", "beta", "gamma"]
    results = _record(state, FAN_OUT_NODE).get(RESULTS_KEY) or []
    worker = _record(state, DISPATCH_WORKER_NODE)
    line = (worker.get(RESULT_KEY) or "").strip()
    ok = (
        len(results) == len(items)
        and results == [str(i).upper() for i in items]
        and bool(worker.get(OK_KEY))
        and matches_result_keyword(line, RESULT_GREETED)
    )
    return {CHECKER_NODE: {ATTEMPT_COUNT_KEY: 1, OK_KEY: ok}}


def pass_terminal(state: HelloState) -> dict:
    return {OUTCOME_KEY: OUTCOME_PASS}


def fail_terminal(state: HelloState) -> dict:
    return {OUTCOME_KEY: OUTCOME_FAIL}
