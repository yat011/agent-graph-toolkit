"""`agentgraph` CLI — start/resume/status/redrive a Run.

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

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    GATE_HALT_REASONS,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    HALT_REASON_KEY,
    ITEMS_KEY,
    OUTCOME_KEY,
    RUN_DIR_KEY,
    SPEC_PATH_KEY,
    WORKER_CLI_CLAUDE,
    WORKER_CLI_CURSOR,
    WORKER_CLI_GROK,
    WORKER_CLI_KEY,
)
from langgraph.types import Command

from agentgraph_engine.dispatch import RolePromptError, preflight_role_prompts
from agentgraph_engine.graph_loader import GraphLoadError, get_build_graph, load_graph_module, resolve_graph_path
from agentgraph_engine.constants import REDRIVE_MESSAGE_KEY
from agentgraph_engine.pause import (
    INTERRUPT_REDRIVE_NODE_KEY,
    interrupt_payload_from_snapshot,
    reset_nested_attempt_records,
    resume_value_for_redrive,
)

from agentgraph_engine.runs import new_run_id, open_checkpointer, run_dir_for, thread_config
from agentgraph_engine.worker_cli import WorkerCliError, resolve_worker_cli

WORKER_CLI_CHOICES = (WORKER_CLI_CLAUDE, WORKER_CLI_GROK, WORKER_CLI_CURSOR)

DEFAULT_AGENT_WORKS_ROOT = Path("agent_works")


def _resolve_worker_cli_from_args(args: argparse.Namespace) -> str:
    return resolve_worker_cli(cli_flag=getattr(args, "cli", None))


def _load_build_graph(graph_name: str):
    path = resolve_graph_path(graph_name)
    module = load_graph_module(path)
    return get_build_graph(module)


def _preflight_graph(graph_name: str) -> None:
    preflight_role_prompts(resolve_graph_path(graph_name))


def _run_id_from_path(run_path: Path) -> str:
    return run_path.name


def _graph_name_from_path(run_path: Path) -> str:
    # run_path = agent_works/{graph_name}/runs/{run_id}
    return run_path.parent.parent.name


def _summarize(final_state: dict) -> dict:
    summary = {
        HALTED_KEY: bool(final_state.get(HALTED_KEY)),
        HALT_REASON_KEY: final_state.get(HALT_REASON_KEY),
        OUTCOME_KEY: final_state.get(OUTCOME_KEY),
    }
    if "__interrupt__" in final_state:
        summary["interrupted"] = True
        summary["interrupt_value"] = [i.value for i in final_state["__interrupt__"]]
    return summary


def cmd_start(args: argparse.Namespace) -> int:
    worker_cli = _resolve_worker_cli_from_args(args)
    agent_works_root = Path(args.agent_works_root) if args.agent_works_root else DEFAULT_AGENT_WORKS_ROOT
    run_id = new_run_id(slug=args.slug, graph_name=args.graph)
    run_dir = run_dir_for(args.graph, run_id, agent_works_root)
    run_dir.mkdir(parents=True, exist_ok=True)

    initial_state = {RUN_DIR_KEY: str(run_dir)}
    if args.spec:
        initial_state[SPEC_PATH_KEY] = args.spec
    if args.input_json:
        initial_state.update(json.loads(Path(args.input_json).read_text(encoding="utf-8")))
    initial_state[WORKER_CLI_KEY] = worker_cli

    build_graph = _load_build_graph(args.graph)
    _preflight_graph(args.graph)
    with open_checkpointer(args.graph, run_id, agent_works_root) as checkpointer:
        compiled = build_graph(checkpointer=checkpointer)
        result = compiled.invoke(initial_state, config={**thread_config(run_id), "recursion_limit": args.recursion_limit})
        print(json.dumps({"run_path": str(run_dir), "run_id": run_id, **_summarize(result)}))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    worker_cli = _resolve_worker_cli_from_args(args)
    run_path = Path(args.run).resolve()
    graph_name = _graph_name_from_path(run_path)
    run_id = _run_id_from_path(run_path)
    agent_works_root = run_path.parent.parent.parent

    build_graph = _load_build_graph(graph_name)
    _preflight_graph(graph_name)
    with open_checkpointer(graph_name, run_id, agent_works_root) as checkpointer:
        compiled = build_graph(checkpointer=checkpointer)
        config = {**thread_config(run_id), "recursion_limit": args.recursion_limit}
        compiled.update_state(thread_config(run_id), {WORKER_CLI_KEY: worker_cli})
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
                    "values": {k: v for k, v in snapshot.values.items() if k not in (ITEMS_KEY,)},
                }
            )
        )
    return 0


def _reset_nested_attempt_records(values: dict) -> dict:
    """Zero every nested node record's attempt_count. Delegates to pause.reset_nested_attempt_records."""
    return reset_nested_attempt_records(values)


def cmd_redrive(args: argparse.Namespace) -> int:
    """Resume a paused Run (interrupt) or re-attempt a halted hello_graph sink.

    If the checkpoint has interrupts, `Command(resume=...)` continues the pause node,
    which then `Command(goto=...)`s. Nested payloads with `parent_node` re-enter that
    parent node; `redrive_node` in the printed JSON is the payload label, not a parent jump.
    Every pause zeroes nested `attempt_count`. `--message` is stored as `redrive_message`
    and injected into the target node's Worker prompt.

    Time-travel fallback remains for graphs that still END on a technical halt (hello_graph).
    """
    worker_cli = _resolve_worker_cli_from_args(args)
    run_path = Path(args.run).resolve()
    graph_name = _graph_name_from_path(run_path)
    run_id = _run_id_from_path(run_path)
    agent_works_root = run_path.parent.parent.parent

    build_graph = _load_build_graph(graph_name)
    _preflight_graph(graph_name)
    with open_checkpointer(graph_name, run_id, agent_works_root) as checkpointer:
        compiled = build_graph(checkpointer=checkpointer)
        config = thread_config(run_id)
        current = compiled.get_state(config)
        payload = interrupt_payload_from_snapshot(current)
        message = getattr(args, "message", None)
        if payload:
            result = compiled.invoke(
                Command(
                    resume=resume_value_for_redrive(message),
                    update={WORKER_CLI_KEY: worker_cli},
                ),
                config={**config, "recursion_limit": args.recursion_limit},
            )
            node_id = payload.get(INTERRUPT_REDRIVE_NODE_KEY) or current.values.get(HALTED_AT_NODE_KEY)
            print(json.dumps({"run_path": str(run_path), "run_id": run_id, "redriven_node": node_id, **_summarize(result)}))
            return 0

        if not current.values.get(HALTED_KEY):
            print(json.dumps({"status": "error", "error": "nothing to redrive — run is not halted or paused"}))
            return 1
        node_id = current.values.get(HALTED_AT_NODE_KEY)
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

        updates = {
            HALTED_KEY: False,
            HALT_REASON_KEY: None,
            HALTED_AT_NODE_KEY: None,
            WORKER_CLI_KEY: worker_cli,
            REDRIVE_MESSAGE_KEY: message,
        }
        updates.update(_reset_nested_attempt_records(target.values))
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
    _add_cli_flag(p_start)
    p_start.set_defaults(func=cmd_start)

    p_resume = sub.add_parser("resume", help="Resume a Run from its checkpoint (e.g. after an interrupt()).")
    p_resume.add_argument("--run", required=True, help="Path to the run folder (agent_works/{graph}/runs/{run_id}).")
    p_resume.add_argument("--resume-value", default=None, help="Value passed to Command(resume=...) if the run is paused at an interrupt().")
    p_resume.add_argument("--recursion-limit", type=int, default=200)
    _add_cli_flag(p_resume)
    p_resume.set_defaults(func=cmd_resume)

    p_status = sub.add_parser("status", help="Print a Run's current checkpointed state.")
    p_status.add_argument("--run", required=True)
    p_status.set_defaults(func=cmd_status)

    p_redrive = sub.add_parser("redrive", help="Re-attempt a halted Run's failing node fresh.")
    p_redrive.add_argument("--run", required=True)
    p_redrive.add_argument("--recursion-limit", type=int, default=200)
    p_redrive.add_argument(
        "--message",
        default=None,
        help="Optional note injected into the redrive target node's Worker prompt (e.g. tell a reviewer a finding is non-blocking).",
    )
    _add_cli_flag(p_redrive)
    p_redrive.set_defaults(func=cmd_redrive)

    return parser


def _add_cli_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cli",
        choices=WORKER_CLI_CHOICES,
        default=None,
        help=(
            "Worker CLI for this process (claude, grok, or cursor). "
            "Overrides ~/.agents/agentgraph.json. Default: claude."
        ),
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (GraphLoadError, FileNotFoundError, WorkerCliError, RolePromptError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
