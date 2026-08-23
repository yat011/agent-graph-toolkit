"""`agentgraph` CLI — start/resume/status/redrive a Run (deliverable e).

The Python-engine equivalent of the retired run-graph.js's resolve-run/next/status commands
(see skills/agentgraph-run-graph/ENGINE-CLI.md for the full command surface). Unlike that engine,
dispatch judgment (which Worker to call, how to branch) lives in the compiled Graph's own Python
code, not in a coordinating LLM reading this CLI's output — so this CLI's job is only to
start/resume the LangGraph run and report its terminal state. No provider API key is read,
accepted, or forwarded anywhere in this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentgraph_engine.graph_loader import GraphLoadError, get_build_graph, load_graph_module, resolve_graph_path
from agentgraph_engine.runs import new_run_id, open_checkpointer, run_dir_for, thread_config

DEFAULT_AGENT_WORKS_ROOT = Path("agent_works")

# halt_reason values written by agentgraph_engine.routing's shared gate router (via
# classify_gate) when a gate's manual/exhausted classification lands on a manual/blocked
# terminal -- as opposed to "retries_exhausted" (an ordinary technical dispatch failure, whose
# redrive must NOT reset attempt counts: the node just gets re-attempted with its existing
# count intact, unchanged from before this feature existed). Only a genuine gate-manual halt
# means "a human fixed the real problem — restart this loop cleanly."
GATE_HALT_REASONS = {"manual_requested", "reject_attempts_exhausted", "unrecognized_result_exhausted"}


def _load_build_graph(graph_name: str):
    path = resolve_graph_path(graph_name)
    module = load_graph_module(path)
    return get_build_graph(module)


def _run_id_from_path(run_path: Path) -> str:
    return run_path.name


def _graph_name_from_path(run_path: Path) -> str:
    # run_path = agent_works/{graph_name}/runs/{run_id}
    return run_path.parent.parent.name


def _summarize(final_state: dict) -> dict:
    summary = {
        "halted": bool(final_state.get("halted")),
        "halt_reason": final_state.get("halt_reason"),
        "outcome": final_state.get("outcome"),
    }
    if "__interrupt__" in final_state:
        summary["interrupted"] = True
        summary["interrupt_value"] = [i.value for i in final_state["__interrupt__"]]
    return summary


def cmd_start(args: argparse.Namespace) -> int:
    agent_works_root = Path(args.agent_works_root) if args.agent_works_root else DEFAULT_AGENT_WORKS_ROOT
    run_id = new_run_id(slug=args.slug, graph_name=args.graph)
    run_dir = run_dir_for(args.graph, run_id, agent_works_root)
    run_dir.mkdir(parents=True, exist_ok=True)

    initial_state = {"run_dir": str(run_dir)}
    if args.spec:
        initial_state["spec_path"] = args.spec
    if args.input_json:
        initial_state.update(json.loads(Path(args.input_json).read_text(encoding="utf-8")))

    build_graph = _load_build_graph(args.graph)
    with open_checkpointer(args.graph, run_id, agent_works_root) as checkpointer:
        compiled = build_graph(checkpointer=checkpointer)
        result = compiled.invoke(initial_state, config={**thread_config(run_id), "recursion_limit": args.recursion_limit})
        print(json.dumps({"run_path": str(run_dir), "run_id": run_id, **_summarize(result)}))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    run_path = Path(args.run).resolve()
    graph_name = _graph_name_from_path(run_path)
    run_id = _run_id_from_path(run_path)
    agent_works_root = run_path.parent.parent.parent

    build_graph = _load_build_graph(graph_name)
    with open_checkpointer(graph_name, run_id, agent_works_root) as checkpointer:
        compiled = build_graph(checkpointer=checkpointer)
        config = {**thread_config(run_id), "recursion_limit": args.recursion_limit}
        resume_input = None
        if args.resume_value is not None:
            from langgraph.types import Command

            resume_input = Command(resume=args.resume_value)
        result = compiled.invoke(resume_input, config=config)
        print(json.dumps({"run_path": str(run_path), "run_id": run_id, **_summarize(result)}))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run_path = Path(args.run).resolve()
    graph_name = _graph_name_from_path(run_path)
    run_id = _run_id_from_path(run_path)
    agent_works_root = run_path.parent.parent.parent

    build_graph = _load_build_graph(graph_name)
    with open_checkpointer(graph_name, run_id, agent_works_root) as checkpointer:
        compiled = build_graph(checkpointer=checkpointer)
        snapshot = compiled.get_state(thread_config(run_id))
        print(
            json.dumps(
                {
                    "run_path": str(run_path),
                    "next": list(snapshot.next),
                    "values": {k: v for k, v in snapshot.values.items() if k not in ("items",)},
                }
            )
        )
    return 0


def cmd_redrive(args: argparse.Namespace) -> int:
    """Reset a halted Run's failing node and re-attempt it fresh, without re-running anything
    upstream of it. Implemented via LangGraph's checkpoint-history "time travel": find the most
    recent checkpoint whose `.next` is exactly the node that halted (`halted_at_node`, set by
    every halting node in the ported graphs), then fork forward from that checkpoint with the
    halt fields cleared — see the empirical verification in gates/e-cli.md / PLAN.md's status
    log for why this (rather than the `as_node` predecessor trick) was chosen: it needs no
    per-graph predecessor map and works uniformly for any node, including ones with multiple
    incoming edges (e.g. a loop-back target).

    When the halt being redriven is one of `GATE_HALT_REASONS` (a gate's reject-exhausted,
    explicit `manual`, or unrecognized-exhausted classification — see agentgraph_engine.routing),
    every `*_attempt_count`/`*_self_retry_count` field is also reset to 0: a human fixing the
    real problem is a clean restart of that loop, not one more automatic attempt continuing the
    old count. An ordinary technical-failure halt (`halt_reason == "retries_exhausted"`) is left
    exactly as before — no counters reset, node just re-attempted with its existing count intact.
    """
    run_path = Path(args.run).resolve()
    graph_name = _graph_name_from_path(run_path)
    run_id = _run_id_from_path(run_path)
    agent_works_root = run_path.parent.parent.parent

    build_graph = _load_build_graph(graph_name)
    with open_checkpointer(graph_name, run_id, agent_works_root) as checkpointer:
        compiled = build_graph(checkpointer=checkpointer)
        config = thread_config(run_id)
        current = compiled.get_state(config)
        if not current.values.get("halted"):
            print(json.dumps({"status": "error", "error": "nothing to redrive — run is not halted"}))
            return 1
        node_id = current.values.get("halted_at_node")
        if not node_id:
            print(json.dumps({"status": "error", "error": "halted_at_node not recorded — cannot redrive"}))
            return 1

        target = None
        for snapshot in compiled.get_state_history(config):
            if snapshot.next == (node_id,):
                target = snapshot
                break
        if target is None:
            print(json.dumps({"status": "error", "error": f"no checkpoint found before node '{node_id}'"}))
            return 1

        updates = {"halted": False, "halt_reason": None, "halted_at_node": None}
        if current.values.get("halt_reason") in GATE_HALT_REASONS:
            # A human resolved a manual/rejected gate, not a technical crash — reset every
            # attempt/self-retry counter so the redriven loop restarts clean at zero, per the
            # gate router's reset-on-redrive convention, rather than continuing to count from
            # where the exhausted attempt left off.
            updates.update(
                {
                    k: 0
                    for k, v in target.values.items()
                    if (k.endswith("_attempt_count") or k.endswith("_self_retry_count")) and isinstance(v, int)
                }
            )
        compiled.update_state(target.config, updates)
        result = compiled.invoke(None, config={**config, "recursion_limit": args.recursion_limit})
        print(json.dumps({"run_path": str(run_path), "run_id": run_id, "redriven_node": node_id, **_summarize(result)}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph", description="Run/resume/redrive a LangGraph agent-graph Run.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start a fresh Run of a named template graph.")
    p_start.add_argument("--graph", required=True, help="Template graph name (feature-kickoff, standard-task, ...).")
    p_start.add_argument("--slug", default=None, help="Short kebab-case slug for the run folder name.")
    p_start.add_argument("--spec", default=None, help="Path to the input spec (feature-kickoff).")
    p_start.add_argument("--input-json", default=None, help="Path to a JSON file merged into the initial state.")
    p_start.add_argument("--agent-works-root", default=None)
    p_start.add_argument("--recursion-limit", type=int, default=200)
    p_start.set_defaults(func=cmd_start)

    p_resume = sub.add_parser("resume", help="Resume a Run from its checkpoint (e.g. after an interrupt()).")
    p_resume.add_argument("--run", required=True, help="Path to the run folder (agent_works/{graph}/runs/{run_id}).")
    p_resume.add_argument("--resume-value", default=None, help="Value passed to Command(resume=...) if the run is paused at an interrupt().")
    p_resume.add_argument("--recursion-limit", type=int, default=200)
    p_resume.set_defaults(func=cmd_resume)

    p_status = sub.add_parser("status", help="Print a Run's current checkpointed state.")
    p_status.add_argument("--run", required=True)
    p_status.set_defaults(func=cmd_status)

    p_redrive = sub.add_parser("redrive", help="Re-attempt a halted Run's failing node fresh.")
    p_redrive.add_argument("--run", required=True)
    p_redrive.add_argument("--recursion-limit", type=int, default=200)
    p_redrive.set_defaults(func=cmd_redrive)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (GraphLoadError, FileNotFoundError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
