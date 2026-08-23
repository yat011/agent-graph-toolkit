"""standard-task node functions and prompt builders."""

from __future__ import annotations

from pathlib import Path

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    HALT_MANUAL_REVIEW_NEEDED,
    HALT_REASON_KEY,
    HALT_RETRIES_EXHAUSTED,
    IMPLEMENT_REQUIREMENTS_NODE,
    ITEM_KEY,
    MANUAL,
    MANUAL_FLAG_NODE,
    MODEL_CHEAP,
    OUTCOME_KEY,
    OUTCOME_MANUAL_FLAG,
    OUTCOME_SUCCESS,
    OUTPUT_PATH_KEY,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RESULT_KEY,
    RESULT_MANUAL,
    RESULT_REJECT,
    RESULT_STOPPED,
    RESULT_VERIFIED,
    REVIEW_NODE,
    ROLE_GENERAL_PURPOSE,
    ROUTE_KEY,
    RUN_DIR_KEY,
    STANDARD_TASK_MANUAL_FLAG_DIR,
    STANDARD_TASK_SUCCESS_DIR,
)
from agentgraph_engine.dispatch import attach_usage, dispatch_with_retry
from agentgraph_engine.routing import classify_gate
from agentgraph_engine.runs import node_output_path
from agentgraph_engine.states.standard_task import StandardTaskState

IMPLEMENT_NODE_DIR = "02_implement_requirements"
REVIEW_NODE_DIR = "03_review"
SUCCESS_NODE_DIR = STANDARD_TASK_SUCCESS_DIR
MANUAL_FLAG_NODE_DIR = STANDARD_TASK_MANUAL_FLAG_DIR


def _record(state: dict, node_id: str) -> dict:
    value = state.get(node_id)
    return value if isinstance(value, dict) else {}


def _next_attempt(state: dict, node_id: str) -> int:
    return int(_record(state, node_id).get(ATTEMPT_COUNT_KEY) or 0) + 1


def _prior_attempt_paths(run_dir: Path, node_id: str, before_attempt: int) -> list[Path]:
    """Existing attempt-N/output.md paths for `node_id`, oldest first, strictly before
    `before_attempt` — used to build the sticky-research / loop-back context a retry needs.
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
    item = state[ITEM_KEY]
    prompt = f"""Implement the requirements for this task: {item.get('title', '')} — {item.get('description', '')}
Test cases to cover (at minimum): {item.get('test_cases', [])}
kind: {item.get('kind', 'implement')}
test_scope: {item.get('test_scope', '')}
full_suite: {item.get('full_suite', False)}
dependencies: {item.get('dependencies', [])}

No external search. Every codebase fact you rely on comes from reading files in this repo. If something is not in the repo, stop and end output.md with `Result: {RESULT_STOPPED} — <short reason>` rather than searching the web or spawning a researcher/explore subagent.

Follow this project's own conventions (SOLID/DRY, no duplicate code, no defensive null checks, and whatever file/language scope restrictions it declares).

You must build/compile and run this task's scoped tests before finishing — do not hand off untested code for review to discover failures in. Scope: test_scope above if set; otherwise only the tests for the files this task owns. Do not run the unfiltered project suite unless full_suite is true or kind is verify. Report the actual pass/fail counts from your own scoped run, never an unverified claim.

Only stop short of green and say so explicitly if a failure genuinely requires a design decision beyond a mechanical fix, and only after you've actually run the scoped tests and root-caused it — never as a substitute for running them. If the build/test environment itself is unavailable, say so explicitly rather than treating it as an implementation gap.

End output.md with a single-line `Result: {RESULT_VERIFIED}` when kind is verify (or full_suite is true and you changed no product files) — skips review, routes to success. End with `Result: {RESULT_IMPLEMENTED}` when kind is implement/default (or full_suite is true and you did change product files) — routes to review. Otherwise end with `Result: {RESULT_STOPPED} — <short reason>` so this routes straight to manual review.
"""
    prior_review = _prior_attempt_paths(run_dir, REVIEW_NODE_DIR, attempt)
    prior_self = _prior_attempt_paths(run_dir, IMPLEMENT_NODE_DIR, attempt)
    if prior_review or prior_self:
        latest_review = prior_review[-1] if prior_review else "(none found)"
        latest_self = prior_self[-1] if prior_self else "(none found)"
        prompt += f"""
This is a retry after a previously rejected review. Read the latest review output first: {latest_review}. Also read this same node's own immediately preceding attempt (sticky-research convention) — treat file paths, line numbers, and facts already established there as still valid unless the rejection specifically contradicts them: {latest_self}. Scope fresh investigation to exactly what the rejection's findings require re-checking.
"""
    return prompt


def _review_prompt(run_dir: Path) -> str:
    node_dir = run_dir / IMPLEMENT_NODE_DIR
    latest = None
    if node_dir.exists():
        attempts = sorted(node_dir.glob("attempt-*/output.md"))
        latest = attempts[-1] if attempts else None
    latest_display = latest if latest else "(not found)"
    return f"""Read only: the `Result:` line of the latest 02_implement_requirements output.md in this run's folder ({latest_display}) — open the rest only if that line is missing or the verdict is unclear — plus `git status`/`git diff` of product files this task owns.

Do not re-run tests — trust the implementer's counts unless the diff makes them implausible.

Checklist (one short paragraph each): SOLID/DRY (reuse existing types, no duplicate framework); no new defensive null checks; diff matches the item requirements; off-limits files have zero diff; tests/miss-path only if the implement Result: line cites them.

Accept or reject. End output.md with a single-line `Result: {RESULT_ACCEPT}` or `Result: {RESULT_REJECT} — <short reason>` conclusion — or, only if you judge this situation needs a human right now rather than another automatic attempt, `Result: {RESULT_MANUAL} — <reason>`.

If and only if the result is {RESULT_ACCEPT}, stage and commit exactly the files `git status` currently shows as modified/untracked at that point, with commit message `<task-id>: <title>`.
"""


def _manual_flag_prompt() -> str:
    return f"""This run needs manual attention. Read whichever of {IMPLEMENT_NODE_DIR}/attempt-*/output.md and {REVIEW_NODE_DIR}/attempt-*/output.md exist in this run's folder. Write output.md summarizing why the run couldn't complete automatically and what a human should check next. Also save this summary as a manual follow-up checklist under agent_works/manual_actions/, if this project uses that convention.
"""


def implement_requirements(state: StandardTaskState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, IMPLEMENT_REQUIREMENTS_NODE)
    output_path = node_output_path(run_dir, IMPLEMENT_NODE_DIR, attempt)
    record = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=1,
        role="code-writer",
        task_prompt=_implement_prompt(state, attempt, run_dir),
        output_path=output_path,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            IMPLEMENT_REQUIREMENTS_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: IMPLEMENT_REQUIREMENTS_NODE,
        }
    record[RESULT_KEY] = result.result_line
    return {IMPLEMENT_REQUIREMENTS_NODE: record}


def review(state: StandardTaskState) -> dict:
    from .graph import REVIEW_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, REVIEW_NODE)
    output_path = node_output_path(run_dir, REVIEW_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=1,
        role="reviewer",
        task_prompt=_review_prompt(run_dir),
        output_path=output_path,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            REVIEW_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: REVIEW_NODE,
        }
    record[RESULT_KEY] = result.result_line
    record.update(classify_gate({**state, REVIEW_NODE: record}, REVIEW_GATE, REVIEW_NODE))
    return {REVIEW_NODE: record}


def success(state: StandardTaskState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, SUCCESS_NODE_DIR, 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        f"Synthesized receipt: requirements were implemented and passed review, or kind: verify / "
        f"full_suite skipped review.\n\nResult: {OUTCOME_SUCCESS}\n",
        encoding="utf-8",
    )
    return {OUTCOME_KEY: OUTCOME_SUCCESS}


def manual_flag(state: StandardTaskState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, MANUAL_FLAG_NODE_DIR, 1)
    result = dispatch_with_retry(
        retry=0,
        role=ROLE_GENERAL_PURPOSE,
        task_prompt=_manual_flag_prompt(),
        output_path=output_path,
        model=MODEL_CHEAP,
    )
    if not result.ok:
        return {
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: MANUAL_FLAG_NODE,
            OUTCOME_KEY: OUTCOME_MANUAL_FLAG,
        }
    review_record = _record(state, REVIEW_NODE)
    if review_record.get(ROUTE_KEY) == MANUAL:
        return {
            OUTCOME_KEY: OUTCOME_MANUAL_FLAG,
            HALTED_KEY: True,
            HALT_REASON_KEY: review_record.get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED),
            HALTED_AT_NODE_KEY: IMPLEMENT_REQUIREMENTS_NODE,
        }
    return {OUTCOME_KEY: OUTCOME_MANUAL_FLAG}
