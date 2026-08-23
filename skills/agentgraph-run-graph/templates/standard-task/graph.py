"""standard-task — per-task subgraph (ported from the retired agent_works/graphs/standard-task/graph.md).

Loaded dynamically via `agentgraph_engine.graph_loader` — never copied into a project's
`agent_works/` (CONTEXT.md's "Template graph"). Faithfully reproduces the old graph's nodes,
branch conditions, and retry counts:

    02_implement_requirements
     |-[verified]---------------------------> 04_success (skip review)
     |-[implemented]-------------------------> 03_review
     `-[stopped without completing]----------> 05_manual_flag

    03_review
     |-[accepted]-----------------------------> 04_success
     |-[rejected, attempts < 3]---------------> 02_implement_requirements  (loop back)
     `-[rejected, attempts = 3]----------------> 05_manual_flag

Branch matching is plain Python string-matching against the `Result:` line each node's prompt
requires -- never an LLM judgment call. A `claude` CLI dispatch that fails/errors after
exhausting `retry` attempts routes to the shared `halted` terminal, same as any other technical
failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.dispatch import dispatch_with_retry
from agentgraph_engine.routing import MANUAL, GateConfig, classify_gate, gate_route, next_self_retry_count
from agentgraph_engine.runs import node_output_path

# 03_review's gate: accepted -> success; rejected (attempts < 3) -> loop back to
# implement_requirements; rejected (attempts >= 3), `manual`, or an unrecognized Result: line
# (self-retry budget exhausted) -> manual_flag. `max_self_attempts=1` mirrors this node's own
# `dispatch_with_retry(retry=1)` budget -- one self-retry chance before falling to manual.
REVIEW_GATE = GateConfig(
    result_key="review_result_line",
    accept_keyword="accepted",
    reject_keyword="rejected",
    retry_target="implement_requirements",
    retry_attempt_key="implement_attempt_count",
    max_retry_attempts=3,
    self_node="review",
    self_attempt_key="review_self_retry_count",
    max_self_attempts=1,
)


class StandardTaskState(TypedDict, total=False):
    run_dir: str  # this subgraph run's own folder (nested under a parent map item, or top-level)
    item: dict  # title, description, test_cases, kind, test_scope, full_suite, dependencies

    implement_attempt_count: int
    implement_result_line: Optional[str]

    review_attempt_count: int
    review_self_retry_count: int
    review_result_line: Optional[str]
    review_result_line_route: Optional[str]
    review_result_line_halt_reason: Optional[str]

    halted: bool
    halt_reason: Optional[str]
    halted_at_node: Optional[str]  # which node halted, for CLI `redrive` (see cli.py)
    outcome: Optional[str]  # "success" | "manual_flag"


def _prior_attempt_paths(run_dir: Path, node_id: str, before_attempt: int) -> list[Path]:
    """Existing attempt-N/output.md paths for `node_id`, oldest first, strictly before
    `before_attempt` -- used to build the sticky-research / loop-back context a retry needs.
    """
    node_dir = run_dir / node_id
    if not node_dir.exists():
        return []
    paths = []
    for n in range(1, before_attempt):
        p = node_output_path(run_dir, node_id, n)
        if p.exists():
            paths.append(p)
    return paths


def _implement_prompt(state: StandardTaskState, attempt: int, run_dir: Path) -> str:
    item = state["item"]
    lines = [
        f"Implement the requirements for this task: {item.get('title', '')} — "
        f"{item.get('description', '')}",
        f"Test cases to cover (at minimum): {item.get('test_cases', [])}",
        f"kind: {item.get('kind', 'implement')}",
        f"test_scope: {item.get('test_scope', '')}",
        f"full_suite: {item.get('full_suite', False)}",
        f"dependencies: {item.get('dependencies', [])}",
        "",
        "No external search. Every codebase fact you rely on comes from reading files in this "
        "repo. If something is not in the repo, stop and end output.md with "
        "`Result: stopped — <short reason>` rather than searching the web or spawning a "
        "researcher/explore subagent.",
        "",
        "Follow this project's own conventions (SOLID/DRY, no duplicate code, no defensive null "
        "checks, and whatever file/language scope restrictions it declares).",
        "",
        "You must build/compile and run this task's scoped tests before finishing — do not hand "
        "off untested code for review to discover failures in. Scope: test_scope above if set; "
        "otherwise only the tests for the files this task owns. Do not run the unfiltered "
        "project suite unless full_suite is true or kind is verify. Report the actual pass/fail "
        "counts from your own scoped run, never an unverified claim.",
        "",
        "Only stop short of green and say so explicitly if a failure genuinely requires a design "
        "decision beyond a mechanical fix, and only after you've actually run the scoped tests "
        "and root-caused it — never as a substitute for running them. If the build/test "
        "environment itself is unavailable, say so explicitly rather than treating it as an "
        "implementation gap.",
        "",
        "End output.md with a single-line `Result: verified` when kind is verify (or full_suite "
        "is true and you changed no product files) — skips review, routes to success. End with "
        "`Result: implemented` when kind is implement/default (or full_suite is true and you did "
        "change product files) — routes to review. Otherwise end with "
        "`Result: stopped — <short reason>` so this routes straight to manual review.",
    ]
    prior_review = _prior_attempt_paths(run_dir, "03_review", attempt)
    prior_self = _prior_attempt_paths(run_dir, "02_implement_requirements", attempt)
    if prior_review or prior_self:
        lines += [
            "",
            "This is a retry after a previously rejected review. Read the latest review output "
            f"first: {prior_review[-1] if prior_review else '(none found)'}. Also read this same "
            "node's own immediately preceding attempt (sticky-research convention) — treat file "
            "paths, line numbers, and facts already established there as still valid unless the "
            f"rejection specifically contradicts them: {prior_self[-1] if prior_self else '(none found)'}. "
            "Scope fresh investigation to exactly what the rejection's findings require re-checking.",
        ]
    return "\n".join(lines)


def _review_prompt(run_dir: Path, attempt: int) -> str:
    node_dir = run_dir / "02_implement_requirements"
    latest = None
    if node_dir.exists():
        attempts = sorted(node_dir.glob("attempt-*/output.md"))
        latest = attempts[-1] if attempts else None
    return "\n".join(
        [
            "Read only: the `Result:` line of the latest 02_implement_requirements output.md in "
            f"this run's folder ({latest if latest else '(not found)'}) — open the rest only if "
            "that line is missing or the verdict is unclear — plus `git status`/`git diff` of "
            "product files this task owns.",
            "",
            "Do not re-run tests — trust the implementer's counts unless the diff makes them "
            "implausible.",
            "",
            "Checklist (one short paragraph each): SOLID/DRY (reuse existing types, no duplicate "
            "framework); no new defensive null checks; diff matches the item requirements; "
            "off-limits files have zero diff; tests/miss-path only if the implement Result: line "
            "cites them.",
            "",
            "Accept or reject. End output.md with a single-line `Result: accepted` or "
            "`Result: rejected — <short reason>` conclusion — or, only if you judge this "
            "situation needs a human right now rather than another automatic attempt, "
            "`Result: manual — <reason>`.",
            "",
            "If and only if the result is accepted, stage and commit exactly the files "
            "`git status` currently shows as modified/untracked at that point, with commit "
            "message `<task-id>: <title>`.",
        ]
    )


def _manual_flag_prompt() -> str:
    return (
        "This run needs manual attention. Read whichever of "
        "02_implement_requirements/attempt-*/output.md and 03_review/attempt-*/output.md exist "
        "in this run's folder. Write output.md summarizing why the run couldn't complete "
        "automatically and what a human should check next. Also save this summary as a manual "
        "follow-up checklist under agent_works/manual_actions/, if this project uses that "
        "convention."
    )


def implement_requirements(state: StandardTaskState) -> dict:
    run_dir = Path(state["run_dir"])
    attempt = state.get("implement_attempt_count", 0) + 1
    output_path = node_output_path(run_dir, "02_implement_requirements", attempt)
    result = dispatch_with_retry(
        retry=1,
        role="code-writer",
        task_prompt=_implement_prompt(state, attempt, run_dir),
        output_path=output_path,
    )
    if not result.ok:
        return {
            "implement_attempt_count": attempt,
            "halted": True,
            "halt_reason": "retries_exhausted",
            "halted_at_node": "implement_requirements",
        }
    return {
        "implement_attempt_count": attempt,
        "implement_result_line": result.result_line,
    }


def route_after_implement(state: StandardTaskState) -> str:
    if state.get("halted"):
        return "halted"
    line = (state.get("implement_result_line") or "").strip()
    if line.startswith("verified"):
        return "success"
    if line.startswith("implemented"):
        return "review"
    return "manual_flag"  # "stopped — ..." or anything unmatched (default)


def review(state: StandardTaskState) -> dict:
    run_dir = Path(state["run_dir"])
    attempt = state.get("review_attempt_count", 0) + 1
    self_retry_count = next_self_retry_count(state, REVIEW_GATE)
    output_path = node_output_path(run_dir, "03_review", attempt)
    result = dispatch_with_retry(
        retry=1,
        role="reviewer",
        task_prompt=_review_prompt(run_dir, attempt),
        output_path=output_path,
    )
    if not result.ok:
        return {
            "review_attempt_count": attempt,
            "halted": True,
            "halt_reason": "retries_exhausted",
            "halted_at_node": "review",
        }
    updates = {
        "review_attempt_count": attempt,
        "review_self_retry_count": self_retry_count,
        "review_result_line": result.result_line,
    }
    updates.update(classify_gate({**state, **updates}, REVIEW_GATE))
    return updates


def route_after_review(state: StandardTaskState) -> str:
    if state.get("halted"):
        return "halted"
    return gate_route(state, REVIEW_GATE, accept_target="success", manual_target="manual_flag")


def success(state: StandardTaskState) -> dict:
    run_dir = Path(state["run_dir"])
    output_path = node_output_path(run_dir, "04_success", 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "Synthesized receipt: requirements were implemented and passed review, or kind: verify / "
        "full_suite skipped review.\n\nResult: success\n",
        encoding="utf-8",
    )
    return {"outcome": "success"}


def manual_flag(state: StandardTaskState) -> dict:
    run_dir = Path(state["run_dir"])
    output_path = node_output_path(run_dir, "05_manual_flag", 1)
    result = dispatch_with_retry(
        retry=0, role="general-purpose", task_prompt=_manual_flag_prompt(), output_path=output_path,
        model="cheap",
    )
    if not result.ok:
        return {
            "halted": True,
            "halt_reason": "retries_exhausted",
            "halted_at_node": "manual_flag",
            "outcome": "manual_flag",
        }
    # Reachable either from 02_implement_requirements' own "stopped"/unmatched routing (out of
    # the shared router's scope -- a worker's semantic judgment call, not an accept/reject/manual
    # gate) or from 03_review's gate (reject-exhausted / `manual` / unrecognized-exhausted). Only
    # the latter is a redrive-able halt: a human resolving it restarts the review loop cleanly at
    # its loop-back target (02_implement_requirements), attempt counts reset to zero.
    if state.get("review_result_line_route") == MANUAL:
        return {
            "outcome": "manual_flag",
            "halted": True,
            "halt_reason": state.get("review_result_line_halt_reason", "manual_review_needed"),
            "halted_at_node": "implement_requirements",
        }
    return {"outcome": "manual_flag"}


def _halted(state: StandardTaskState) -> dict:
    return {}


def build_graph(checkpointer=None):
    graph = StateGraph(StandardTaskState)
    graph.add_node("implement_requirements", implement_requirements)
    graph.add_node("review", review)
    graph.add_node("success", success)
    graph.add_node("manual_flag", manual_flag)
    graph.add_node("halted", _halted)

    graph.add_edge(START, "implement_requirements")
    graph.add_conditional_edges(
        "implement_requirements",
        route_after_implement,
        {"success": "success", "review": "review", "manual_flag": "manual_flag", "halted": "halted"},
    )
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "success": "success",
            "implement_requirements": "implement_requirements",
            "review": "review",
            "manual_flag": "manual_flag",
            "halted": "halted",
        },
    )
    graph.add_edge("success", END)
    graph.add_edge("manual_flag", END)
    graph.add_edge("halted", END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
