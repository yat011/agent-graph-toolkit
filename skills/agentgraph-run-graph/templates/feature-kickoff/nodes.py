"""feature-kickoff node functions and prompt builders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    BLOCKED_PLAN_REJECTED_NODE,
    CREATE_FEATURE_BRANCH_NODE,
    FINAL_REVIEW_NODE,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    HALT_MANUAL_REVIEW_NEEDED,
    HALT_REASON_KEY,
    HALT_RETRIES_EXHAUSTED,
    HALT_UNMET_DEPENDENCIES,
    ITEMS_KEY,
    ITEM_KEY,
    LOAD_TASKS_NODE,
    MANUAL,
    MAP_TASK_STATES_KEY,
    MODEL_CHEAP,
    NEEDS_MANUAL_REVIEW_NODE,
    OUTCOME_BLOCKED,
    OUTCOME_KEY,
    OUTCOME_MANUAL_FLAG,
    OUTCOME_MANUAL_REVIEW,
    OUTCOME_SUCCESS,
    OUTPUT_PATH_KEY,
    PLANNER_NODE,
    RESULT_ACCEPT,
    RESULT_KEY,
    RESULT_MANUAL,
    RESULT_REJECT,
    ROLE_GENERAL_PURPOSE,
    ROUTE_KEY,
    RUN_DIR_KEY,
    RUN_TASKS_NODE,
    SPEC_PATH_KEY,
    STANDARD_TASK_MANUAL_FLAG_DIR,
    STANDARD_TASK_SUCCESS_DIR,
    SUCCESS_NODE,
    TECH_PLAN_REVIEWER_NODE,
)
from agentgraph_engine.dispatch import attach_usage, dispatch_with_retry
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.routing import classify_gate
from agentgraph_engine.runs import node_output_path
from agentgraph_engine.states.feature_kickoff import FeatureKickoffState

STANDARD_TASK_GRAPH_PATH = Path(__file__).resolve().parent.parent / "standard-task" / "graph.py"

BRANCH_NODE_DIR = "01_create_feature_branch"
PLANNER_NODE_DIR = "02_planner"
TECH_REVIEW_NODE_DIR = "03_tech_plan_reviewer"
LOAD_TASKS_NODE_DIR = "04_load_tasks"
RUN_TASKS_NODE_DIR = "05_run_tasks"
FINAL_REVIEW_NODE_DIR = "06_final_review"
BLOCKED_NODE_DIR = "07_blocked_plan_rejected"
MANUAL_REVIEW_NODE_DIR = "08_needs_manual_review"
SUCCESS_NODE_DIR = "09_success"


def _record(state: dict, node_id: str) -> dict:
    value = state.get(node_id)
    return value if isinstance(value, dict) else {}


def _next_attempt(state: dict, node_id: str) -> int:
    return int(_record(state, node_id).get(ATTEMPT_COUNT_KEY) or 0) + 1


def _branch_prompt(state: FeatureKickoffState) -> str:
    spec_hint = (
        f"Use this spec path: {state[SPEC_PATH_KEY]}"
        if state.get(SPEC_PATH_KEY)
        else "No spec path was given — read the most recently modified file under "
        "agent_works/specs/. If none exists, do not guess: write output.md stating no spec was "
        "found and stop."
    )
    return f"""{spec_hint}

Derive a short kebab-case slug from the spec's subject/title.

Check the current branch name and `git status` first, before running any git command that changes state: if the current branch is already feature/{{slug}}, treat it as already done. Otherwise check for uncommitted changes outside agent_works/ — if anything is uncommitted there, do not touch it or switch branches; write output.md explaining the working tree is dirty and stop. Otherwise, if a branch named feature/{{slug}} already exists, check it out (no -b); only use `git checkout -b feature/{{slug}}` when that branch doesn't exist yet. Never force anything, never touch remotes.

Write output.md containing: the spec file path, the derived slug, and the branch name. End with a single-line `Result: branch ready` conclusion.
"""


def create_feature_branch(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, CREATE_FEATURE_BRANCH_NODE)
    output_path = node_output_path(run_dir, BRANCH_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=1,
        role=ROLE_GENERAL_PURPOSE,
        task_prompt=_branch_prompt(state),
        output_path=output_path,
        model=MODEL_CHEAP,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            CREATE_FEATURE_BRANCH_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: CREATE_FEATURE_BRANCH_NODE,
        }
    record[RESULT_KEY] = result.result_line
    return {CREATE_FEATURE_BRANCH_NODE: record}


def _planner_prompt(state: FeatureKickoffState, run_dir: Path, attempt: int) -> str:
    prompt = f"""Produce a tech plan + task breakdown only — not a spec. The input spec named by 01_create_feature_branch's output.md is already approved ground truth: read it in full, but do not rewrite or re-derive it.

Follow agentgraph-vertical-slice-tasks for task sizing (cut vertical, prefactor first, dependencies as blocking edges). Do not dispatch your own agent definition's parallel researcher subagents in this context — that research already happened before the spec was approved.

Write the tech plan under agent_works/plans/{{feature-slug}}.md (starting with a `Spec: agent_works/specs/{{slug}}.md` line) and the same task list as machine-readable JSON at agent_works/plans/{{feature-slug}}.tasks.json` (id, title, description, test_cases, dependencies, and optional kind/test_scope/full_suite fields).

End output.md with the plan file path and the tasks JSON file path, each on their own line, then a single-line `Result: plan written` conclusion.
"""
    reviewer_dir = run_dir / TECH_REVIEW_NODE_DIR
    if attempt > 1 and reviewer_dir.exists():
        attempts = sorted(reviewer_dir.glob("attempt-*/output.md"))
        if attempts:
            prompt += f"""
A previous attempt was rejected — read its findings first: {attempts[-1]}. Revise the plan and task list to explicitly address every rejection reason (sticky-research convention: treat facts you already established as still valid unless the rejection specifically contradicts them).
"""
    return prompt


def planner(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, PLANNER_NODE)
    output_path = node_output_path(run_dir, PLANNER_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=1,
        role="planner",
        task_prompt=_planner_prompt(state, run_dir, attempt),
        output_path=output_path,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            PLANNER_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: PLANNER_NODE,
        }
    record[RESULT_KEY] = result.result_line
    return {PLANNER_NODE: record}


def _tech_review_prompt(state: FeatureKickoffState) -> str:
    plan_output_path = _record(state, PLANNER_NODE).get(OUTPUT_PATH_KEY)
    return f"""Read 02_planner's latest output.md ({plan_output_path}) for the plan file path and tasks JSON file path, then read both in full, plus the spec they reference.

Scope this review to the plan and tasks, not the spec's own decisions. Fact-check every codebase claim the plan depends on that isn't covered by a passing Verified-Evidence citation. Follow agentgraph-vertical-slice-tasks when judging task size and sequencing.

End output.md with your standard `Verdict: {RESULT_ACCEPT}` / `Verdict: {RESULT_REJECT} — <reason>` conclusion, then restate it as this graph's `Result:` line: exactly `Result: {RESULT_ACCEPT}`, `Result: {RESULT_REJECT} — <reason>`, or — only if you judge this situation needs a human right now rather than another automatic attempt — `Result: {RESULT_MANUAL} — <reason>`.
"""


def tech_plan_reviewer(state: FeatureKickoffState) -> dict:
    from .graph import TECH_REVIEW_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, TECH_PLAN_REVIEWER_NODE)
    output_path = node_output_path(run_dir, TECH_REVIEW_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=0,
        role="tech-plan-reviewer",
        task_prompt=_tech_review_prompt(state),
        output_path=output_path,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            TECH_PLAN_REVIEWER_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: TECH_PLAN_REVIEWER_NODE,
        }
    record[RESULT_KEY] = result.result_line
    record.update(classify_gate({**state, TECH_PLAN_REVIEWER_NODE: record}, TECH_REVIEW_GATE, TECH_PLAN_REVIEWER_NODE))
    return {TECH_PLAN_REVIEWER_NODE: record}


def _parse_plan_output_paths(plan_output_path: Optional[str]) -> Optional[str]:
    """02_planner's output.md ends with the plan path and tasks JSON path, each on their own
    line, then a Result: line — the tasks JSON path is the last line ending in `.tasks.json`.
    """
    if not plan_output_path or not Path(plan_output_path).exists():
        return None
    lines = [line.strip() for line in Path(plan_output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    candidates = [line for line in lines if line.endswith(".tasks.json")]
    return candidates[-1] if candidates else None


def _load_tasks_prompt(state: FeatureKickoffState) -> str:
    plan_output_path = _record(state, PLANNER_NODE).get(OUTPUT_PATH_KEY)
    return f"""Verify the project's build/test environment is working once, for the whole batch. If it is not working, do not attempt to load the task list — end output.md with `Result: {RESULT_MANUAL} — environment not working` and stop.

If working, read 02_planner's latest output.md ({plan_output_path}) for the tasks JSON file path, and read that JSON file. Write the task list to items.json in this node's attempt folder as a JSON array (copied verbatim).

End output.md with a one-line summary of how many tasks were loaded, then a single-line `Result: {RESULT_ACCEPT}` conclusion.
"""


def load_tasks(state: FeatureKickoffState) -> dict:
    from .graph import LOAD_TASKS_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, LOAD_TASKS_NODE)
    output_path = node_output_path(run_dir, LOAD_TASKS_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path), ITEMS_KEY: []}
    result = dispatch_with_retry(
        retry=1,
        role=ROLE_GENERAL_PURPOSE,
        task_prompt=_load_tasks_prompt(state),
        output_path=output_path,
        model=MODEL_CHEAP,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            LOAD_TASKS_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: LOAD_TASKS_NODE,
        }

    items: list = []
    line = (result.result_line or "").strip()
    if line.startswith(RESULT_ACCEPT):
        items_path = output_path.parent / "items.json"
        if not items_path.exists():
            tasks_json_path = _parse_plan_output_paths(_record(state, PLANNER_NODE).get(OUTPUT_PATH_KEY))
            if tasks_json_path and Path(tasks_json_path).exists():
                items = json.loads(Path(tasks_json_path).read_text(encoding="utf-8"))
                items_path.parent.mkdir(parents=True, exist_ok=True)
                items_path.write_text(Path(tasks_json_path).read_text(encoding="utf-8"), encoding="utf-8")
        else:
            items = json.loads(items_path.read_text(encoding="utf-8"))

    record[RESULT_KEY] = result.result_line
    record[ITEMS_KEY] = items
    record.update(classify_gate({**state, LOAD_TASKS_NODE: record}, LOAD_TASKS_GATE, LOAD_TASKS_NODE))
    return {LOAD_TASKS_NODE: record}


def _item_already_done(item_run_dir: Path) -> Optional[str]:
    """On-disk idempotency check so a resumed 05_run_tasks doesn't re-dispatch an item whose
    nested standard-task run already reached a terminal on a prior (interrupted) pass.
    """
    if node_output_path(item_run_dir, STANDARD_TASK_SUCCESS_DIR, 1).exists():
        return OUTCOME_SUCCESS
    if node_output_path(item_run_dir, STANDARD_TASK_MANUAL_FLAG_DIR, 1).exists():
        return OUTCOME_MANUAL_FLAG
    return None


def run_tasks(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    items = _record(state, LOAD_TASKS_NODE).get(ITEMS_KEY) or []
    order = [item.get("id") or f"item-{i}" for i, item in enumerate(items, start=1)]
    by_id = {iid: item for iid, item in zip(order, items)}
    index_of = {iid: i for i, iid in enumerate(order, start=1)}

    standard_task_module = load_graph_module(STANDARD_TASK_GRAPH_PATH)
    standard_task_build_graph = get_build_graph(standard_task_module)

    outcomes: dict = {}
    map_task_states: list = []
    remaining = set(order)

    while remaining:
        ready, perm_blocked, waiting = [], [], []
        for iid in order:
            if iid not in remaining:
                continue
            deps = by_id[iid].get("dependencies") or []
            missing = [d for d in deps if d not in by_id]
            flagged = [
                d for d in deps if outcomes.get(d) == OUTCOME_MANUAL_FLAG or outcomes.get(d) == OUTCOME_BLOCKED
            ]
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
                    outcomes[iid] = OUTCOME_BLOCKED
                    map_task_states.append({ITEM_KEY: by_id[iid], OUTCOME_KEY: OUTCOME_BLOCKED})
                    remaining.discard(iid)
                continue
            return {
                HALTED_KEY: True,
                HALT_REASON_KEY: HALT_UNMET_DEPENDENCIES,
                HALTED_AT_NODE_KEY: RUN_TASKS_NODE,
                MAP_TASK_STATES_KEY: map_task_states,
            }

        for iid in ready:
            index = index_of[iid]
            item_run_dir = run_dir / RUN_TASKS_NODE_DIR / f"item-{index}"
            item_run_dir.mkdir(parents=True, exist_ok=True)

            prior = _item_already_done(item_run_dir)
            if prior is not None:
                child_state = {
                    RUN_DIR_KEY: str(item_run_dir),
                    ITEM_KEY: by_id[iid],
                    OUTCOME_KEY: prior,
                }
            else:
                sub_graph = standard_task_build_graph()
                child_state = sub_graph.invoke(
                    {RUN_DIR_KEY: str(item_run_dir), ITEM_KEY: by_id[iid]},
                    config={"recursion_limit": 50},
                )

            outcomes[iid] = child_state.get(OUTCOME_KEY) or OUTCOME_MANUAL_FLAG
            map_task_states.append(child_state)
            remaining.discard(iid)

    return {MAP_TASK_STATES_KEY: map_task_states}


def _final_review_prompt(state: FeatureKickoffState) -> str:
    child_states = state.get(MAP_TASK_STATES_KEY) or []
    checklist = "\n".join(
        f"- {(child.get(ITEM_KEY) or {}).get('id')} ({(child.get(ITEM_KEY) or {}).get('title')}): {child.get(OUTCOME_KEY)}"
        for child in child_states
    )
    return f"""Confirm nothing was skipped, then run the unfiltered test suite (or reuse recorded unfiltered counts per this project's reuse convention).

Per-task outcomes from 05_run_tasks:
{checklist or "(no tasks)"}

End output.md with a per-task checklist, the test run summary, and a single-line `Result: {RESULT_ACCEPT}` or `Result: {RESULT_MANUAL} — <short reason>` conclusion.
"""


def final_review(state: FeatureKickoffState) -> dict:
    from .graph import FINAL_REVIEW_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, FINAL_REVIEW_NODE)
    output_path = node_output_path(run_dir, FINAL_REVIEW_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=0,
        role=ROLE_GENERAL_PURPOSE,
        task_prompt=_final_review_prompt(state),
        output_path=output_path,
        model=MODEL_CHEAP,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            FINAL_REVIEW_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: FINAL_REVIEW_NODE,
        }
    record[RESULT_KEY] = result.result_line
    record.update(classify_gate({**state, FINAL_REVIEW_NODE: record}, FINAL_REVIEW_GATE, FINAL_REVIEW_NODE))
    return {FINAL_REVIEW_NODE: record}


def blocked_plan_rejected(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, BLOCKED_NODE_DIR, 1)
    prompt = f"""The plan/task-list review loop exhausted 3 attempts without reaching approval. Read every 02_planner and 03_tech_plan_reviewer attempt's output.md in this run's folder. Write output.md summarizing the latest plan/tasks paths and the unresolved rejection reasons so a human can take over. End with `Result: {OUTCOME_BLOCKED}`.
"""
    result = dispatch_with_retry(
        retry=0, role=ROLE_GENERAL_PURPOSE, task_prompt=prompt, output_path=output_path, model=MODEL_CHEAP
    )
    if not result.ok:
        return {
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: BLOCKED_PLAN_REJECTED_NODE,
        }
    return {
        OUTCOME_KEY: OUTCOME_BLOCKED,
        HALTED_KEY: True,
        HALT_REASON_KEY: _record(state, TECH_PLAN_REVIEWER_NODE).get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED),
        HALTED_AT_NODE_KEY: PLANNER_NODE,
    }


def needs_manual_review(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, MANUAL_REVIEW_NODE_DIR, 1)
    prompt = f"""This run needs manual attention — reachable either from 04_load_tasks (environment down) or 06_final_review (issues found). Read whichever of those (and per-item outputs under 05_run_tasks) exist and write output.md summarizing why. End with `Result: manual review needed`.
"""
    result = dispatch_with_retry(
        retry=0, role=ROLE_GENERAL_PURPOSE, task_prompt=prompt, output_path=output_path, model=MODEL_CHEAP
    )
    if not result.ok:
        return {
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: NEEDS_MANUAL_REVIEW_NODE,
        }
    if _record(state, FINAL_REVIEW_NODE).get(ROUTE_KEY) == MANUAL:
        redrive_node = FINAL_REVIEW_NODE
        reason = _record(state, FINAL_REVIEW_NODE).get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED)
    else:
        redrive_node = LOAD_TASKS_NODE
        reason = _record(state, LOAD_TASKS_NODE).get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED)
    return {OUTCOME_KEY: OUTCOME_MANUAL_REVIEW, HALTED_KEY: True, HALT_REASON_KEY: reason, HALTED_AT_NODE_KEY: redrive_node}


def success(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, SUCCESS_NODE_DIR, 1)
    prompt = f"""Write a Recap a human can read without opening the rest of the run: what shipped, the suite result, links to spec/plan/tasks JSON, and any follow-up docs. End with `Result: recap written`.
"""
    result = dispatch_with_retry(
        retry=0, role=ROLE_GENERAL_PURPOSE, task_prompt=prompt, output_path=output_path, model=MODEL_CHEAP
    )
    if not result.ok:
        return {HALTED_KEY: True, HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED, HALTED_AT_NODE_KEY: SUCCESS_NODE}
    return {OUTCOME_KEY: OUTCOME_SUCCESS}
