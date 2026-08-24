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
    REVIEW_NODE,
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
    prompt = (
        "Implement the task.\n"
        "\n"
        "No external search / no researcher subagent — every codebase fact you rely on comes from "
        "reading files in this repo. If something is not in the repo, stop and end output.md with "
        f"`Result: {RESULT_STOPPED} — <short reason>` rather than searching the web or spawning a "
        "researcher/explore subagent.\n"
        "\n"
        "Run scoped tests only (the files this task owns, or test_scope from the suffix if set). "
        "Do not run the unfiltered project suite.\n"
        "\n"
        f"End output.md with a single-line `Result: {RESULT_IMPLEMENTED}` or "
        f"`Result: {RESULT_STOPPED} — <short reason>`.\n"
    )
    prompt += (
        "\n"
        f"title: {item.get('title', '')}\n"
        f"description: {item.get('description', '')}\n"
        f"test_cases: {item.get('test_cases', [])}\n"
        f"test_scope: {item.get('test_scope', '')}\n"
        f"dependencies: {item.get('dependencies', [])}\n"
    )
    prior_review = _prior_attempt_paths(run_dir, REVIEW_NODE_DIR, attempt)
    prior_self = _prior_attempt_paths(run_dir, IMPLEMENT_NODE_DIR, attempt)
    if prior_review or prior_self:
        latest_review = prior_review[-1] if prior_review else "(none found)"
        latest_self = prior_self[-1] if prior_self else "(none found)"
        prompt += (
            "\nThis is a retry after a previously rejected review. "
            f"Read the latest review output first: {latest_review}. "
            "Also read this same node's own immediately preceding attempt "
            "(sticky-research convention) — treat file paths, line numbers, and facts already "
            f"established there as still valid unless the rejection specifically contradicts them: "
            f"{latest_self}. Scope fresh investigation to exactly what the rejection's findings "
            "require re-checking.\n"
        )
    return prompt


def _review_prompt(run_dir: Path) -> str:
    node_dir = run_dir / IMPLEMENT_NODE_DIR
    latest = None
    if node_dir.exists():
        attempts = sorted(node_dir.glob("attempt-*/output.md"))
        latest = attempts[-1] if attempts else None
    latest_display = latest if latest else "(not found)"
    prompt = (
        "Read only the `Result:` line of the latest 02_implement_requirements output.md in this "
        "run's folder — open the rest only if that line is missing or the verdict is unclear — "
        "plus `git status`/`git diff` of product files this task owns.\n"
        "\n"
        "On reject, list each failure as a bullet: reason, then a pointer (file:line).\n"
        "On accept, write only the Result line.\n"
        "\n"
        f"End output.md with a single-line `Result: {RESULT_ACCEPT}` or "
        f"`Result: {RESULT_REJECT} — <short reason>` conclusion — or, only if you judge this "
        "situation needs a human right now rather than another automatic attempt, "
        f"`Result: {RESULT_MANUAL} — <reason>`.\n"
        "\n"
        f"If and only if the result is {RESULT_ACCEPT}, stage and commit exactly the files "
        "`git status` currently shows as modified/untracked at that point, with commit message "
        "`<task-id>: <title>`.\n"
    )
    prompt += f"\nImplement output.md: {latest_display}\n"
    return prompt


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
        f"Synthesized receipt: requirements were implemented and passed review.\n\n"
        f"Result: {OUTCOME_SUCCESS}\n",
        encoding="utf-8",
    )
    return {OUTCOME_KEY: OUTCOME_SUCCESS}


def manual_flag(state: StandardTaskState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, MANUAL_FLAG_NODE_DIR, 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("Result: flagged\n", encoding="utf-8")
    review_record = _record(state, REVIEW_NODE)
    if review_record.get(ROUTE_KEY) == MANUAL:
        return {
            OUTCOME_KEY: OUTCOME_MANUAL_FLAG,
            HALTED_KEY: True,
            HALT_REASON_KEY: review_record.get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED),
            HALTED_AT_NODE_KEY: IMPLEMENT_REQUIREMENTS_NODE,
        }
    return {OUTCOME_KEY: OUTCOME_MANUAL_FLAG}
