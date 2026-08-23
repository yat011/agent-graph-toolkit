"""hello_graph — minimal worked example (deliverable h) exercising every primitive this engine
supports in one small graph:

    greet (sequence) -> fan_out (sequential map/fan-out) -> checkpoint_gate (interrupt(), used by
    the checkpoint-resume proof in tests/test_checkpoint_resume.py) -> dispatch_worker (one real
    headless-CLI Worker dispatch) -> checker (branch node) -> pass_terminal | fail_terminal

Lives inside the engine package itself, not under skills/agentgraph-run-graph/templates/ (which
is reserved for the two production graphs this migration ports) and not under agent_works/ (which
is per-project run-instance data, not source) — it's a maintained, tested example loaded the same
way (via agentgraph_engine.graph_loader) any real template would be, proving the loader and
dispatch mechanics generically rather than only against the two production graphs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agentgraph_engine.dispatch import dispatch_with_retry


class HelloState(TypedDict, total=False):
    run_dir: str
    name: Optional[str]
    items: list

    greeting: Optional[str]
    fan_out_results: list

    gate_ack: Optional[str]

    worker_ok: bool
    worker_result_line: Optional[str]

    checker_ok: bool
    halted: bool
    halt_reason: Optional[str]
    halted_at_node: Optional[str]
    outcome: Optional[str]  # "pass" | "fail"


def greet(state: HelloState) -> dict:
    return {"greeting": f"Hello, {state.get('name') or 'world'}!"}


def fan_out(state: HelloState) -> dict:
    """Sequential map/fan-out: transforms each item one at a time, in order (no concurrent
    dispatch, per this migration's settled design)."""
    items = state.get("items") or ["alpha", "beta", "gamma"]
    results = []
    for item in items:
        results.append(str(item).upper())
    return {"fan_out_results": results}


def checkpoint_gate(state: HelloState) -> dict:
    """The sole interrupt() point in this example — used to demonstrate real checkpoint-resume
    (deliverable i): the first `.invoke()` call pauses here and persists state via the
    checkpointer; a later `.invoke(Command(resume=...))` against the same thread_id continues
    from exactly this point without re-running `greet`/`fan_out`.
    """
    ack = interrupt({"message": "hello_graph paused after fan-out — resume to continue"})
    return {"gate_ack": ack}


def dispatch_worker(state: HelloState) -> dict:
    """The one CLI-dispatched Worker node in this example (headless `claude -p` dispatch, via the
    shared agentgraph_engine.dispatch module)."""
    run_dir = Path(state["run_dir"])
    output_path = run_dir / "dispatch_worker" / "attempt-1" / "output.md"
    result = dispatch_with_retry(
        retry=1,
        role="general-purpose",
        task_prompt=(
            "Say hello in one short sentence. End your output with a single-line "
            "`Result: greeted` conclusion."
        ),
        output_path=output_path,
        model="cheap",
    )
    if not result.ok:
        return {
            "worker_ok": False,
            "halted": True,
            "halt_reason": "retries_exhausted",
            "halted_at_node": "dispatch_worker",
        }
    return {"worker_ok": True, "worker_result_line": result.result_line}


def route_after_worker(state: HelloState) -> str:
    return "halted" if state.get("halted") else "checker"


def checker(state: HelloState) -> dict:
    """Checker/branch node: plain-code decision, no LLM judgment."""
    items = state.get("items") or ["alpha", "beta", "gamma"]
    results = state.get("fan_out_results") or []
    line = (state.get("worker_result_line") or "").strip()
    ok = (
        len(results) == len(items)
        and results == [str(i).upper() for i in items]
        and bool(state.get("worker_ok"))
        and line.startswith("greeted")
    )
    return {"checker_ok": ok}


def route_after_checker(state: HelloState) -> str:
    return "pass_terminal" if state.get("checker_ok") else "fail_terminal"


def pass_terminal(state: HelloState) -> dict:
    return {"outcome": "pass"}


def fail_terminal(state: HelloState) -> dict:
    return {"outcome": "fail"}


def _halted(state: HelloState) -> dict:
    return {}


def build_graph(checkpointer=None):
    graph = StateGraph(HelloState)
    graph.add_node("greet", greet)
    graph.add_node("fan_out", fan_out)
    graph.add_node("checkpoint_gate", checkpoint_gate)
    graph.add_node("dispatch_worker", dispatch_worker)
    graph.add_node("checker", checker)
    graph.add_node("pass_terminal", pass_terminal)
    graph.add_node("fail_terminal", fail_terminal)
    graph.add_node("halted", _halted)

    graph.add_edge(START, "greet")
    graph.add_edge("greet", "fan_out")
    graph.add_edge("fan_out", "checkpoint_gate")
    graph.add_edge("checkpoint_gate", "dispatch_worker")
    graph.add_conditional_edges(
        "dispatch_worker", route_after_worker, {"checker": "checker", "halted": "halted"}
    )
    graph.add_conditional_edges(
        "checker", route_after_checker, {"pass_terminal": "pass_terminal", "fail_terminal": "fail_terminal"}
    )
    graph.add_edge("pass_terminal", END)
    graph.add_edge("fail_terminal", END)
    graph.add_edge("halted", END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
