"""feature-kickoff — full feature-delivery graph (ported from the retired
agent_works/graphs/feature-kickoff/graph.md).

    01_create_feature_branch -> 02_planner -> 03_tech_plan_reviewer
                                               |-[Approve]------------------------> 04_load_tasks
                                               |-[Reject, attempted 3x]-----------> 07_blocked_plan_rejected
                                               `-[Reject, attempted < 3x]---------> 02_planner (loop back)

    04_load_tasks
     |-[loaded, env working]-> 05_run_tasks (sequential map: standard-task per task) -> 06_final_review
     |                                                                                   |-[passed]--------> 09_success
     |                                                                                   `-[issues]--------> 08_needs_manual_review
     `-[env down]--------------------------------------------------------------------------------------> 08_needs_manual_review

    07_blocked_plan_rejected   (terminal)
    08_needs_manual_review     (terminal)
    09_success                 (terminal)

05_run_tasks embeds the standard-task template as a reusable, compiled sub-StateGraph — one full
recursion of standard-task per task item, dispatched strictly sequentially (no concurrent
dispatch), honoring each item's `dependencies` list: a permanently-blocked item (a missing
dependency id, or one that finished at manual_flag) is left `blocked` rather than halting the
whole map; a genuine cycle (nothing ready, something still waiting) halts with
`unmet_dependencies`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agentgraph_engine.dispatch import dispatch_with_retry
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.routing import MANUAL, GateConfig, classify_gate, gate_route, next_self_retry_count
from agentgraph_engine.runs import node_output_path

STANDARD_TASK_GRAPH_PATH = (
    Path(__file__).resolve().parent.parent / "standard-task" / "graph.py"
)

# 03_tech_plan_reviewer's gate: Approve -> load_tasks; Reject (attempts < 3) -> loop back to
# planner; Reject (attempts >= 3), `manual`, or an unrecognized Result: line (self-retry budget
# exhausted) -> blocked_plan_rejected. `max_self_attempts=0` mirrors this node's own
# `dispatch_with_retry(retry=0)` budget -- no self-retry chance, straight to manual.
TECH_REVIEW_GATE = GateConfig(
    result_key="tech_review_result_line",
    accept_keyword="Approve",
    reject_keyword="Reject",
    retry_target="planner",
    retry_attempt_key="planner_attempt_count",
    max_retry_attempts=3,
    self_node="tech_plan_reviewer",
    self_attempt_key="tech_review_self_retry_count",
    max_self_attempts=0,
)

# 04_load_tasks's gate: this node has no loop-back edge and no accept/reject keyword pair in the
# original graph.md (it's a mechanical env-check, not a worker judgment call) -- degenerate form:
# one accept condition, everything else (env-down, or a garbled Result: line alike) is
# "unrecognized" from the router's perspective, self-retried up to this node's own
# `dispatch_with_retry(retry=1)` budget before falling to needs_manual_review.
LOAD_TASKS_GATE = GateConfig(
    result_key="load_tasks_result_line",
    accept_keyword="environment working",
    self_node="load_tasks",
    self_attempt_key="load_tasks_self_retry_count",
    max_self_attempts=1,
)

# 06_final_review's gate: same degenerate shape as load_tasks. `max_self_attempts=0` mirrors this
# node's own `dispatch_with_retry(retry=0)` budget.
FINAL_REVIEW_GATE = GateConfig(
    result_key="final_review_result_line",
    accept_keyword="passed",
    self_node="final_review",
    self_attempt_key="final_review_self_retry_count",
    max_self_attempts=0,
)


class FeatureKickoffState(TypedDict, total=False):
    run_dir: str
    spec_path: Optional[str]

    branch_result_line: Optional[str]

    planner_attempt_count: int
    plan_output_path: Optional[str]

    tech_review_attempt_count: int
    tech_review_self_retry_count: int
    tech_review_result_line: Optional[str]
    tech_review_result_line_route: Optional[str]
    tech_review_result_line_halt_reason: Optional[str]

    load_tasks_self_retry_count: int
    load_tasks_result_line: Optional[str]
    load_tasks_result_line_route: Optional[str]
    load_tasks_result_line_halt_reason: Optional[str]
    items: list

    task_outcomes: list

    final_review_self_retry_count: int
    final_review_result_line: Optional[str]
    final_review_result_line_route: Optional[str]
    final_review_result_line_halt_reason: Optional[str]

    halted: bool
    halt_reason: Optional[str]
    halted_at_node: Optional[str]  # which node halted, for CLI `redrive` (see cli.py)
    outcome: Optional[str]  # "blocked" | "manual_review" | "success"


# ---------------------------------------------------------------------------
# 01_create_feature_branch
# ---------------------------------------------------------------------------


def _branch_prompt(state: FeatureKickoffState) -> str:
    spec_hint = (
        f"Use this spec path: {state['spec_path']}"
        if state.get("spec_path")
        else "No spec path was given — read the most recently modified file under "
        "agent_works/specs/. If none exists, do not guess: write output.md stating no spec was "
        "found and stop."
    )
    return "\n".join(
        [
            spec_hint,
            "",
            "Derive a short kebab-case slug from the spec's subject/title.",
            "",
            "Check the current branch name and `git status` first, before running any git "
            "command that changes state: if the current branch is already feature/{slug}, treat "
            "it as already done. Otherwise check for uncommitted changes outside "
            "agent_works/ — if anything is uncommitted there, do not touch it or switch "
            "branches; write output.md explaining the working tree is dirty and stop. Otherwise, "
            "if a branch named feature/{slug} already exists, check it out (no -b); only use "
            "`git checkout -b feature/{slug}` when that branch doesn't exist yet. Never force "
            "anything, never touch remotes.",
            "",
            "Write output.md containing: the spec file path, the derived slug, and the branch "
            "name. End with a single-line `Result: branch ready` conclusion.",
        ]
    )


def create_feature_branch(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    output_path = node_output_path(run_dir, "01_create_feature_branch", 1)
    result = dispatch_with_retry(
        retry=1,
        role="general-purpose",
        task_prompt=_branch_prompt(state),
        output_path=output_path,
        model="cheap",
    )
    if not result.ok:
        return {"halted": True, "halt_reason": "retries_exhausted", "halted_at_node": "create_feature_branch"}
    return {"branch_result_line": result.result_line}


def route_after_branch(state: FeatureKickoffState) -> str:
    return "halted" if state.get("halted") else "planner"


# ---------------------------------------------------------------------------
# 02_planner
# ---------------------------------------------------------------------------


def _planner_prompt(state: FeatureKickoffState, run_dir: Path, attempt: int) -> str:
    lines = [
        "Produce a tech plan + task breakdown only — not a spec. The input spec named by "
        "01_create_feature_branch's output.md is already approved ground truth: read it in "
        "full, but do not rewrite or re-derive it.",
        "",
        "Follow agentgraph-vertical-slice-tasks for task sizing (cut vertical, prefactor "
        "first, dependencies as blocking edges). Do not dispatch your own agent definition's "
        "parallel researcher subagents in this context — that research already happened before "
        "the spec was approved.",
        "",
        "Write the tech plan under agent_works/plans/{feature-slug}.md (starting with a `Spec: "
        "agent_works/specs/{slug}.md` line) and the same task list as machine-readable JSON at "
        "agent_works/plans/{feature-slug}.tasks.json` (id, title, description, test_cases, "
        "dependencies, and optional kind/test_scope/full_suite fields).",
        "",
        "End output.md with the plan file path and the tasks JSON file path, each on their own "
        "line, then a single-line `Result: plan written` conclusion.",
    ]
    reviewer_dir = run_dir / "03_tech_plan_reviewer"
    if attempt > 1 and reviewer_dir.exists():
        attempts = sorted(reviewer_dir.glob("attempt-*/output.md"))
        if attempts:
            lines += [
                "",
                f"A previous attempt was rejected — read its findings first: {attempts[-1]}. "
                "Revise the plan and task list to explicitly address every rejection reason "
                "(sticky-research convention: treat facts you already established as still "
                "valid unless the rejection specifically contradicts them).",
            ]
    return "\n".join(lines)


def planner(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    attempt = state.get("planner_attempt_count", 0) + 1
    output_path = node_output_path(run_dir, "02_planner", attempt)
    result = dispatch_with_retry(
        retry=1,
        role="planner",
        task_prompt=_planner_prompt(state, run_dir, attempt),
        output_path=output_path,
    )
    if not result.ok:
        return {
            "planner_attempt_count": attempt,
            "halted": True,
            "halt_reason": "retries_exhausted",
            "halted_at_node": "planner",
        }
    return {
        "planner_attempt_count": attempt,
        "plan_output_path": str(output_path),
    }


def route_after_planner(state: FeatureKickoffState) -> str:
    return "halted" if state.get("halted") else "tech_plan_reviewer"


# ---------------------------------------------------------------------------
# 03_tech_plan_reviewer
# ---------------------------------------------------------------------------


def _tech_review_prompt(state: FeatureKickoffState) -> str:
    return "\n".join(
        [
            f"Read 02_planner's latest output.md ({state.get('plan_output_path')}) for the plan "
            "file path and tasks JSON file path, then read both in full, plus the spec they "
            "reference.",
            "",
            "Scope this review to the plan and tasks, not the spec's own decisions. Fact-check "
            "every codebase claim the plan depends on that isn't covered by a passing "
            "Verified-Evidence citation. Follow agentgraph-vertical-slice-tasks when judging "
            "task size and sequencing.",
            "",
            "End output.md with your standard `Verdict: Approve` / `Verdict: Reject — <reason>` "
            "conclusion, then restate it as this graph's `Result:` line: exactly `Result: "
            "Approve`, `Result: Reject — <reason>`, or — only if you judge this situation needs "
            "a human right now rather than another automatic attempt — `Result: manual — "
            "<reason>`.",
        ]
    )


def tech_plan_reviewer(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    # Give every dispatch of this node its own folder — its own attempt count, distinct from
    # planner's (a self-retry on an unrecognized Result: line re-dispatches this node without a
    # new planner attempt).
    attempt = state.get("tech_review_attempt_count", 0) + 1
    self_retry_count = next_self_retry_count(state, TECH_REVIEW_GATE)
    output_path = node_output_path(run_dir, "03_tech_plan_reviewer", attempt)
    result = dispatch_with_retry(
        retry=0,
        role="tech-plan-reviewer",
        task_prompt=_tech_review_prompt(state),
        output_path=output_path,
    )
    if not result.ok:
        return {
            "tech_review_attempt_count": attempt,
            "halted": True,
            "halt_reason": "retries_exhausted",
            "halted_at_node": "tech_plan_reviewer",
        }
    updates = {
        "tech_review_attempt_count": attempt,
        "tech_review_self_retry_count": self_retry_count,
        "tech_review_result_line": result.result_line,
    }
    updates.update(classify_gate({**state, **updates}, TECH_REVIEW_GATE))
    return updates


def route_after_tech_review(state: FeatureKickoffState) -> str:
    if state.get("halted"):
        return "halted"
    return gate_route(state, TECH_REVIEW_GATE, accept_target="load_tasks", manual_target="blocked_plan_rejected")


# ---------------------------------------------------------------------------
# 04_load_tasks
# ---------------------------------------------------------------------------


def _parse_plan_output_paths(plan_output_path: Optional[str]) -> Optional[str]:
    """02_planner's output.md ends with the plan path and tasks JSON path, each on their own
    line, then a Result: line — the tasks JSON path is the second-to-last non-empty line.
    """
    if not plan_output_path or not Path(plan_output_path).exists():
        return None
    lines = [l.strip() for l in Path(plan_output_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    candidates = [l for l in lines if l.endswith(".tasks.json")]
    return candidates[-1] if candidates else None


def _load_tasks_prompt(state: FeatureKickoffState) -> str:
    return "\n".join(
        [
            "Verify the project's build/test environment is working once, for the whole "
            "batch. If it is not working, do not attempt to load the task list — end "
            "output.md with `Result: environment not working` and stop.",
            "",
            f"If working, read 02_planner's latest output.md ({state.get('plan_output_path')}) "
            "for the tasks JSON file path, and read that JSON file. Write the task list to "
            "items.json in this node's attempt folder as a JSON array (copied verbatim).",
            "",
            "End output.md with a one-line summary of how many tasks were loaded, then a "
            "single-line `Result: environment working` conclusion.",
        ]
    )


def load_tasks(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    self_retry_count = next_self_retry_count(state, LOAD_TASKS_GATE)
    output_path = node_output_path(run_dir, "04_load_tasks", 1)
    result = dispatch_with_retry(
        retry=1,
        role="general-purpose",
        task_prompt=_load_tasks_prompt(state),
        output_path=output_path,
        model="cheap",
    )
    if not result.ok:
        return {"halted": True, "halt_reason": "retries_exhausted", "halted_at_node": "load_tasks"}

    items: list = []
    line = (result.result_line or "").strip()
    if line.startswith("environment working"):
        items_path = output_path.parent / "items.json"
        if not items_path.exists():
            # Fall back to reading straight from the plan's tasks JSON, per the same convention,
            # in case the dispatched worker didn't also copy it into items.json.
            tasks_json_path = _parse_plan_output_paths(state.get("plan_output_path"))
            if tasks_json_path and Path(tasks_json_path).exists():
                import json

                items = json.loads(Path(tasks_json_path).read_text(encoding="utf-8"))
                items_path.parent.mkdir(parents=True, exist_ok=True)
                items_path.write_text(Path(tasks_json_path).read_text(encoding="utf-8"), encoding="utf-8")
        else:
            import json

            items = json.loads(items_path.read_text(encoding="utf-8"))

    updates = {
        "load_tasks_self_retry_count": self_retry_count,
        "load_tasks_result_line": result.result_line,
        "items": items,
    }
    updates.update(classify_gate({**state, **updates}, LOAD_TASKS_GATE))
    return updates


def route_after_load_tasks(state: FeatureKickoffState) -> str:
    if state.get("halted"):
        return "halted"
    return gate_route(state, LOAD_TASKS_GATE, accept_target="run_tasks", manual_target="needs_manual_review")


# ---------------------------------------------------------------------------
# 05_run_tasks — sequential map/fan-out over standard-task subgraph
# ---------------------------------------------------------------------------


def _item_already_done(item_run_dir: Path) -> Optional[str]:
    """On-disk idempotency check so a resumed 05_run_tasks doesn't re-dispatch an item whose
    nested standard-task run already reached a terminal on a prior (interrupted) pass.
    """
    if node_output_path(item_run_dir, "04_success", 1).exists():
        return "success"
    if node_output_path(item_run_dir, "05_manual_flag", 1).exists():
        return "manual_flag"
    return None


def run_tasks(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    items = state.get("items") or []
    order = [item.get("id") or f"item-{i}" for i, item in enumerate(items, start=1)]
    by_id = {iid: item for iid, item in zip(order, items)}
    index_of = {iid: i for i, iid in enumerate(order, start=1)}

    standard_task_module = load_graph_module(STANDARD_TASK_GRAPH_PATH)
    standard_task_build_graph = get_build_graph(standard_task_module)

    outcomes: dict = {}
    task_outcomes: list = []
    remaining = set(order)

    while remaining:
        ready, perm_blocked, waiting = [], [], []
        for iid in order:
            if iid not in remaining:
                continue
            deps = by_id[iid].get("dependencies") or []
            missing = [d for d in deps if d not in by_id]
            flagged = [d for d in deps if outcomes.get(d) == "manual_flag" or outcomes.get(d) == "blocked"]
            unresolved = [d for d in deps if d in by_id and d not in outcomes]
            if missing or flagged:
                perm_blocked.append(iid)
            elif unresolved:
                waiting.append(iid)
            else:
                ready.append(iid)

        if not ready:
            if perm_blocked:
                for iid in perm_blocked:
                    outcomes[iid] = "blocked"
                    task_outcomes.append({"id": iid, "title": by_id[iid].get("title"), "outcome": "blocked"})
                    remaining.discard(iid)
                continue
            return {
                "halted": True,
                "halt_reason": "unmet_dependencies",
                "halted_at_node": "run_tasks",
                "task_outcomes": task_outcomes,
            }

        for iid in ready:
            index = index_of[iid]
            item_run_dir = run_dir / "05_run_tasks" / f"item-{index}"
            item_run_dir.mkdir(parents=True, exist_ok=True)

            prior = _item_already_done(item_run_dir)
            if prior is not None:
                outcome = prior
            else:
                sub_graph = standard_task_build_graph()
                sub_result = sub_graph.invoke(
                    {"run_dir": str(item_run_dir), "item": by_id[iid]},
                    config={"recursion_limit": 50},
                )
                outcome = sub_result.get("outcome") or "manual_flag"

            outcomes[iid] = outcome
            task_outcomes.append(
                {"id": iid, "title": by_id[iid].get("title"), "outcome": outcome, "run_dir": str(item_run_dir)}
            )
            remaining.discard(iid)

    return {"task_outcomes": task_outcomes}


def route_after_run_tasks(state: FeatureKickoffState) -> str:
    return "halted" if state.get("halted") else "final_review"


# ---------------------------------------------------------------------------
# 06_final_review
# ---------------------------------------------------------------------------


def _final_review_prompt(state: FeatureKickoffState) -> str:
    outcomes = state.get("task_outcomes") or []
    checklist = "\n".join(f"- {o['id']} ({o.get('title')}): {o['outcome']}" for o in outcomes)
    return "\n".join(
        [
            "Confirm nothing was skipped, then run the unfiltered test suite (or reuse recorded "
            "unfiltered counts per this project's reuse convention).",
            "",
            "Per-task outcomes from 05_run_tasks:",
            checklist or "(no tasks)",
            "",
            "End output.md with a per-task checklist, the test run summary, and a single-line "
            "`Result: passed` or `Result: needs manual review — <short reason>` conclusion.",
        ]
    )


def final_review(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    self_retry_count = next_self_retry_count(state, FINAL_REVIEW_GATE)
    output_path = node_output_path(run_dir, "06_final_review", 1)
    result = dispatch_with_retry(
        retry=0,
        role="general-purpose",
        task_prompt=_final_review_prompt(state),
        output_path=output_path,
        model="cheap",
    )
    if not result.ok:
        return {"halted": True, "halt_reason": "retries_exhausted", "halted_at_node": "final_review"}
    updates = {
        "final_review_self_retry_count": self_retry_count,
        "final_review_result_line": result.result_line,
    }
    updates.update(classify_gate({**state, **updates}, FINAL_REVIEW_GATE))
    return updates


def route_after_final_review(state: FeatureKickoffState) -> str:
    if state.get("halted"):
        return "halted"
    return gate_route(state, FINAL_REVIEW_GATE, accept_target="success", manual_target="needs_manual_review")


# ---------------------------------------------------------------------------
# Terminals: 07_blocked_plan_rejected, 08_needs_manual_review, 09_success, halted
# ---------------------------------------------------------------------------


def blocked_plan_rejected(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    output_path = node_output_path(run_dir, "07_blocked_plan_rejected", 1)
    prompt = (
        "The plan/task-list review loop exhausted 3 attempts without reaching approval. Read "
        "every 02_planner and 03_tech_plan_reviewer attempt's output.md in this run's folder. "
        "Write output.md summarizing the latest plan/tasks paths and the unresolved rejection "
        "reasons so a human can take over. End with `Result: blocked`."
    )
    result = dispatch_with_retry(retry=0, role="general-purpose", task_prompt=prompt, output_path=output_path, model="cheap")
    if not result.ok:
        return {"halted": True, "halt_reason": "retries_exhausted", "halted_at_node": "blocked_plan_rejected"}
    # Only reachable via 03_tech_plan_reviewer's gate (reject-exhausted / `manual` / unrecognized-
    # exhausted) — redriving after a human resolves this lands back at the gate's loop-back
    # target (02_planner), fresh, per the reset-on-redrive convention (cli.py's cmd_redrive).
    return {
        "outcome": "blocked",
        "halted": True,
        "halt_reason": state.get("tech_review_result_line_halt_reason", "manual_review_needed"),
        "halted_at_node": "planner",
    }


def needs_manual_review(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    output_path = node_output_path(run_dir, "08_needs_manual_review", 1)
    prompt = (
        "This run needs manual attention — reachable either from 04_load_tasks (environment "
        "down) or 06_final_review (issues found). Read whichever of those (and per-item outputs "
        "under 05_run_tasks) exist and write output.md summarizing why. End with "
        "`Result: manual review needed`."
    )
    result = dispatch_with_retry(retry=0, role="general-purpose", task_prompt=prompt, output_path=output_path, model="cheap")
    if not result.ok:
        return {"halted": True, "halt_reason": "retries_exhausted", "halted_at_node": "needs_manual_review"}
    # Reachable from either 04_load_tasks's or 06_final_review's gate — neither has a loop-back
    # edge, so redriving re-attempts whichever of those two actually triggered this (per the
    # reset-on-redrive convention: "a node with no loop-back edge redrives by re-attempting the
    # node itself").
    if state.get("final_review_result_line_route") == MANUAL:
        redrive_node = "final_review"
        reason = state.get("final_review_result_line_halt_reason", "manual_review_needed")
    else:
        redrive_node = "load_tasks"
        reason = state.get("load_tasks_result_line_halt_reason", "manual_review_needed")
    return {"outcome": "manual_review", "halted": True, "halt_reason": reason, "halted_at_node": redrive_node}


def success(state: FeatureKickoffState) -> dict:
    run_dir = Path(state["run_dir"])
    output_path = node_output_path(run_dir, "09_success", 1)
    prompt = (
        "Write a Recap a human can read without opening the rest of the run: what shipped, the "
        "suite result, links to spec/plan/tasks JSON, and any follow-up docs. End with "
        "`Result: recap written`."
    )
    result = dispatch_with_retry(retry=0, role="general-purpose", task_prompt=prompt, output_path=output_path, model="cheap")
    if not result.ok:
        return {"halted": True, "halt_reason": "retries_exhausted", "halted_at_node": "success"}
    return {"outcome": "success"}


def _halted(state: FeatureKickoffState) -> dict:
    return {}


def build_graph(checkpointer=None):
    graph = StateGraph(FeatureKickoffState)
    graph.add_node("create_feature_branch", create_feature_branch)
    graph.add_node("planner", planner)
    graph.add_node("tech_plan_reviewer", tech_plan_reviewer)
    graph.add_node("load_tasks", load_tasks)
    graph.add_node("run_tasks", run_tasks)
    graph.add_node("final_review", final_review)
    graph.add_node("blocked_plan_rejected", blocked_plan_rejected)
    graph.add_node("needs_manual_review", needs_manual_review)
    graph.add_node("success", success)
    graph.add_node("halted", _halted)

    graph.add_edge(START, "create_feature_branch")
    graph.add_conditional_edges(
        "create_feature_branch", route_after_branch, {"planner": "planner", "halted": "halted"}
    )
    graph.add_conditional_edges(
        "planner", route_after_planner, {"tech_plan_reviewer": "tech_plan_reviewer", "halted": "halted"}
    )
    graph.add_conditional_edges(
        "tech_plan_reviewer",
        route_after_tech_review,
        {
            "load_tasks": "load_tasks",
            "planner": "planner",
            "tech_plan_reviewer": "tech_plan_reviewer",
            "blocked_plan_rejected": "blocked_plan_rejected",
            "halted": "halted",
        },
    )
    graph.add_conditional_edges(
        "load_tasks",
        route_after_load_tasks,
        {
            "run_tasks": "run_tasks",
            "load_tasks": "load_tasks",
            "needs_manual_review": "needs_manual_review",
            "halted": "halted",
        },
    )
    graph.add_conditional_edges(
        "run_tasks", route_after_run_tasks, {"final_review": "final_review", "halted": "halted"}
    )
    graph.add_conditional_edges(
        "final_review",
        route_after_final_review,
        {
            "success": "success",
            "final_review": "final_review",
            "needs_manual_review": "needs_manual_review",
            "halted": "halted",
        },
    )
    graph.add_edge("blocked_plan_rejected", END)
    graph.add_edge("needs_manual_review", END)
    graph.add_edge("success", END)
    graph.add_edge("halted", END)
    return graph.compile(checkpointer=checkpointer)


graph = build_graph()
