"""feature-kickoff node functions and prompt builders."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
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
    TECH_PLAN_REVIEWER_NODE,
)
from agentgraph_engine.dispatch import attach_usage, dispatch_with_retry, extract_result_line
from agentgraph_engine.graph_loader import get_build_graph, load_graph_module
from agentgraph_engine.routing import classify_gate
from agentgraph_engine.runs import node_output_path, slugify
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


def _write_output_file(output_path: Path, body: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not body.endswith("\n"):
        body += "\n"
    output_path.write_text(body, encoding="utf-8")


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _resolve_spec_path(state: FeatureKickoffState) -> Optional[Path]:
    given = state.get(SPEC_PATH_KEY)
    if given:
        return Path(given)
    specs = Path("agent_works") / "specs"
    if not specs.is_dir():
        return None
    files = [path for path in specs.glob("*.md") if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _branch_slug(state: FeatureKickoffState) -> str:
    spec = _resolve_spec_path(state)
    if spec is not None:
        return slugify(spec.stem)
    return slugify(Path(state[RUN_DIR_KEY]).name)


def _plan_artifact_paths(state: FeatureKickoffState) -> tuple[Path, Path]:
    """Convention paths for the plan markdown and tasks JSON the planner writes."""
    spec = _resolve_spec_path(state)
    slug = _branch_slug(state)
    if spec is not None:
        plans_dir = spec.resolve().parent.parent / "plans"
    else:
        plans_dir = Path("agent_works") / "plans"
    return plans_dir / f"{slug}.md", plans_dir / f"{slug}.tasks.json"


def _dirty_paths_outside_agent_works(porcelain: str) -> list[str]:
    dirty: list[str] = []
    for raw in porcelain.splitlines():
        if len(raw) < 4:
            continue
        path = raw[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path.startswith("agent_works/") or path == "agent_works":
            continue
        dirty.append(path)
    return dirty


def _git_or_halt(
    record: dict,
    output_path: Path,
    args: list[str],
) -> subprocess.CompletedProcess[str] | dict:
    try:
        proc = _run_git(args)
    except FileNotFoundError:
        _write_output_file(
            output_path,
            "git is not on PATH.\n\nResult: manual — git is not on PATH\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        return {
            CREATE_FEATURE_BRANCH_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: CREATE_FEATURE_BRANCH_NODE,
        }
    return proc


def create_feature_branch(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, CREATE_FEATURE_BRANCH_NODE)
    output_path = node_output_path(run_dir, BRANCH_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    spec = _resolve_spec_path(state)
    slug = _branch_slug(state)
    branch = f"feature/{slug}"
    spec_display = str(spec) if spec is not None else "(no spec path)"

    head = _git_or_halt(record, output_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if isinstance(head, dict):
        return head
    if head.returncode != 0:
        _write_output_file(
            output_path,
            (
                f"Spec: {spec_display}\n"
                f"Slug: {slug}\n"
                f"Branch: {branch}\n\n"
                "Not a git repository (or git rev-parse failed).\n\n"
                "Result: manual — not a git repository\n"
            ),
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        return {
            CREATE_FEATURE_BRANCH_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: CREATE_FEATURE_BRANCH_NODE,
        }
    current = (head.stdout or "").strip()
    if current == branch:
        _write_output_file(
            output_path,
            (
                f"Spec: {spec_display}\n"
                f"Slug: {slug}\n"
                f"Branch: {branch}\n\n"
                "Already on this branch.\n\n"
                "Result: branch ready\n"
            ),
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        return {CREATE_FEATURE_BRANCH_NODE: record}

    status = _git_or_halt(record, output_path, ["status", "--porcelain"])
    if isinstance(status, dict):
        return status
    dirty = _dirty_paths_outside_agent_works(status.stdout or "")
    if dirty:
        listed = ", ".join(dirty)
        _write_output_file(
            output_path,
            (
                f"Spec: {spec_display}\n"
                f"Slug: {slug}\n"
                f"Branch: {branch}\n\n"
                "Working tree is dirty outside agent_works/; did not switch branches. "
                f"Dirty paths: {listed}\n\n"
                "Result: working tree dirty — uncommitted changes outside agent_works/\n"
            ),
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        return {CREATE_FEATURE_BRANCH_NODE: record}

    exists = _git_or_halt(
        record, output_path, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
    )
    if isinstance(exists, dict):
        return exists
    checkout_args = ["checkout", branch] if exists.returncode == 0 else ["checkout", "-b", branch]
    checkout = _git_or_halt(record, output_path, checkout_args)
    if isinstance(checkout, dict):
        return checkout
    if checkout.returncode != 0:
        err = (checkout.stderr or checkout.stdout or "git checkout failed").strip()
        _write_output_file(
            output_path,
            (
                f"Spec: {spec_display}\n"
                f"Slug: {slug}\n"
                f"Branch: {branch}\n\n"
                f"{err}\n\n"
                "Result: manual — git checkout failed\n"
            ),
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        return {
            CREATE_FEATURE_BRANCH_NODE: record,
            HALTED_KEY: True,
            HALT_REASON_KEY: HALT_RETRIES_EXHAUSTED,
            HALTED_AT_NODE_KEY: CREATE_FEATURE_BRANCH_NODE,
        }

    _write_output_file(
        output_path,
        (
            f"Spec: {spec_display}\n"
            f"Slug: {slug}\n"
            f"Branch: {branch}\n\n"
            "Result: branch ready\n"
        ),
    )
    record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
    return {CREATE_FEATURE_BRANCH_NODE: record}


def _planner_prompt(state: FeatureKickoffState, run_dir: Path, attempt: int) -> str:
    plan_md, tasks_json = _plan_artifact_paths(state)
    spec = _resolve_spec_path(state)
    prompt = (
        "Write the tech plan (starting with a `Spec:` line naming the spec file) and the same "
        "task list as machine-readable JSON (id, title, description, test_cases, dependencies, "
        "optional test_scope) at the paths in the suffix.\n"
        "\n"
        "End output.md with a single-line `Result: plan written`.\n"
    )
    if attempt > 1:
        reviewer_dir = run_dir / TECH_REVIEW_NODE_DIR
        if reviewer_dir.exists():
            attempts = sorted(reviewer_dir.glob("attempt-*/output.md"))
            if attempts:
                prompt += (
                    "\nA previous attempt was rejected — read its findings first: "
                    f"{attempts[-1]}. Revise the plan and task list to explicitly address every "
                    "rejection reason (sticky-research convention: treat facts you already "
                    "established as still valid unless the rejection specifically contradicts "
                    "them).\n"
                )
    spec_display = str(spec) if spec is not None else "(none)"
    prompt += (
        f"\nSpec: {spec_display}\n"
        f"Plan: {plan_md}\n"
        f"Tasks JSON: {tasks_json}\n"
    )
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
    plan_md, tasks_json = _plan_artifact_paths(state)
    spec = _resolve_spec_path(state)
    spec_display = str(spec) if spec is not None else "(none)"
    prompt = (
        "Review the plan and tasks (not the spec's own decisions).\n"
        "\n"
        "On reject, list each failure as a bullet: reason, then a pointer "
        "(plan section, task id, or file:line).\n"
        "On accept, write only the Result line.\n"
        "\n"
        f"End output.md with `Result: {RESULT_ACCEPT}`, "
        f"`Result: {RESULT_REJECT} — <reason>`, or — only if you judge this situation needs a "
        f"human right now rather than another automatic attempt — "
        f"`Result: {RESULT_MANUAL} — <reason>`.\n"
    )
    prompt += (
        f"\nSpec: {spec_display}\n"
        f"Plan: {plan_md}\n"
        f"Tasks JSON: {tasks_json}\n"
    )
    return prompt


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
    """Fallback: last `.tasks.json` path mentioned in 02_planner's output.md, if any."""
    if not plan_output_path or not Path(plan_output_path).exists():
        return None
    lines = [line.strip() for line in Path(plan_output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    candidates = [line for line in lines if line.endswith(".tasks.json")]
    return candidates[-1] if candidates else None


def _resolve_tasks_json_path(state: FeatureKickoffState) -> Optional[str]:
    _plan_md, tasks_json = _plan_artifact_paths(state)
    if tasks_json.is_file():
        return str(tasks_json)
    return _parse_plan_output_paths(_record(state, PLANNER_NODE).get(OUTPUT_PATH_KEY))


def _js_env_reason(items: list) -> Optional[str]:
    """If any task looks like JS/TS work, node must be on PATH."""
    blob = json.dumps(items)
    if ".js" not in blob and ".mjs" not in blob and ".ts" not in blob:
        return None
    if shutil.which("node") is None:
        return "environment not working — node is not on PATH"
    return None


def load_tasks(state: FeatureKickoffState) -> dict:
    from .graph import LOAD_TASKS_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, LOAD_TASKS_NODE)
    output_path = node_output_path(run_dir, LOAD_TASKS_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path), ITEMS_KEY: []}
    tasks_json_path = _resolve_tasks_json_path(state)

    if not tasks_json_path or not Path(tasks_json_path).exists():
        reason = "could not load tasks — no readable .tasks.json at the expected plan path"
        _write_output_file(
            output_path,
            f"{reason}\n\nResult: {RESULT_MANUAL} — {reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_TASKS_NODE: record}, LOAD_TASKS_GATE, LOAD_TASKS_NODE))
        return {LOAD_TASKS_NODE: record}

    raw = Path(tasks_json_path).read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        reason = f"could not load tasks — invalid JSON ({exc.msg})"
        _write_output_file(
            output_path,
            f"{reason}\n\nResult: {RESULT_MANUAL} — {reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_TASKS_NODE: record}, LOAD_TASKS_GATE, LOAD_TASKS_NODE))
        return {LOAD_TASKS_NODE: record}

    if not isinstance(parsed, list):
        reason = "could not load tasks — tasks JSON is not an array"
        _write_output_file(
            output_path,
            f"{reason}\n\nResult: {RESULT_MANUAL} — {reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_TASKS_NODE: record}, LOAD_TASKS_GATE, LOAD_TASKS_NODE))
        return {LOAD_TASKS_NODE: record}

    env_reason = _js_env_reason(parsed)
    if env_reason:
        _write_output_file(
            output_path,
            f"{env_reason}\n\nResult: {RESULT_MANUAL} — {env_reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_TASKS_NODE: record}, LOAD_TASKS_GATE, LOAD_TASKS_NODE))
        return {LOAD_TASKS_NODE: record}

    items_path = output_path.parent / "items.json"
    items_path.parent.mkdir(parents=True, exist_ok=True)
    items_path.write_text(raw, encoding="utf-8")
    _write_output_file(
        output_path,
        (
            f"Loaded {len(parsed)} task(s) from {tasks_json_path}\n\n"
            f"Result: {RESULT_ACCEPT}\n"
        ),
    )
    record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
    record[ITEMS_KEY] = parsed
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
    prompt = (
        "Confirm nothing was skipped. Do not re-read exact code diffs/hunks.\n"
        "\n"
        "Seed changed files as `git diff --name-only` vs merge-base with the repo default branch "
        "(origin/HEAD or main), UNION every item's test_scope. Search for modules that **directly "
        "import** those changed product files (rg/imports; prefer codebase-memory-mcp if connected, "
        "missing is not a stop). Run the tests those importer files own PLUS the test_scope set. "
        "Two-hop graph tests that only import a wrapper (e.g. test_feature_kickoff_graph importing "
        "graph.py not the changed module) are OUT.\n"
        "\n"
        "End output.md with the test command you ran, pass or fail (with counts if available), "
        f"and a single-line `Result: {RESULT_ACCEPT}` or "
        f"`Result: {RESULT_MANUAL} — <short reason>` conclusion.\n"
    )
    child_states = state.get(MAP_TASK_STATES_KEY) or []
    checklist = "\n".join(
        f"- {(child.get(ITEM_KEY) or {}).get('id')} "
        f"({(child.get(ITEM_KEY) or {}).get('title')}): {child.get(OUTCOME_KEY)}"
        for child in child_states
    )
    prompt += (
        "\nPer-task outcomes from 05_run_tasks:\n"
        f"{checklist or '(no tasks)'}\n"
    )
    return prompt


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


def _attempt_outputs(run_dir: Path, node_dir: str) -> list[Path]:
    folder = run_dir / node_dir
    if not folder.exists():
        return []
    return sorted(folder.glob("attempt-*/output.md"))


def _output_excerpt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    line = extract_result_line(text)
    if line:
        return f"{path}: Result: {line}"
    tail = text.strip()[-500:] if text.strip() else "(empty)"
    return f"{path}:\n{tail}"


def blocked_plan_rejected(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, BLOCKED_NODE_DIR, 1)
    excerpts = [
        _output_excerpt(path)
        for path in (
            *_attempt_outputs(run_dir, PLANNER_NODE_DIR),
            *_attempt_outputs(run_dir, TECH_REVIEW_NODE_DIR),
        )
    ]
    body = (
        "Plan/task-list review loop did not reach approval.\n\n"
        + ("\n".join(excerpts) if excerpts else "(no planner/reviewer outputs found)")
        + f"\n\nResult: {OUTCOME_BLOCKED}\n"
    )
    _write_output_file(output_path, body)
    return {
        OUTCOME_KEY: OUTCOME_BLOCKED,
        HALTED_KEY: True,
        HALT_REASON_KEY: _record(state, TECH_PLAN_REVIEWER_NODE).get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED),
        HALTED_AT_NODE_KEY: PLANNER_NODE,
    }


def needs_manual_review(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, MANUAL_REVIEW_NODE_DIR, 1)
    paths = [
        *_attempt_outputs(run_dir, LOAD_TASKS_NODE_DIR),
        *_attempt_outputs(run_dir, FINAL_REVIEW_NODE_DIR),
        *sorted(run_dir.glob("05_run_tasks/**/output.md")),
    ]
    excerpts = [_output_excerpt(path) for path in paths]
    body = (
        "This run needs manual attention.\n\n"
        + ("\n".join(excerpts) if excerpts else "(no prior node outputs found)")
        + "\n\nResult: manual review needed\n"
    )
    _write_output_file(output_path, body)
    if _record(state, FINAL_REVIEW_NODE).get(ROUTE_KEY) == MANUAL:
        redrive_node = FINAL_REVIEW_NODE
        reason = _record(state, FINAL_REVIEW_NODE).get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED)
    else:
        redrive_node = LOAD_TASKS_NODE
        reason = _record(state, LOAD_TASKS_NODE).get(HALT_REASON_KEY, HALT_MANUAL_REVIEW_NEEDED)
    return {
        OUTCOME_KEY: OUTCOME_MANUAL_REVIEW,
        HALTED_KEY: True,
        HALT_REASON_KEY: reason,
        HALTED_AT_NODE_KEY: redrive_node,
    }


def success(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, SUCCESS_NODE_DIR, 1)
    spec = state.get(SPEC_PATH_KEY) or "(no spec path)"
    plan_output = _record(state, PLANNER_NODE).get(OUTPUT_PATH_KEY) or "(no planner output)"
    items = _record(state, LOAD_TASKS_NODE).get(ITEMS_KEY) or []
    child_states = state.get(MAP_TASK_STATES_KEY) or []
    checklist = "\n".join(
        f"- {(child.get(ITEM_KEY) or {}).get('id')} "
        f"({(child.get(ITEM_KEY) or {}).get('title')}): {child.get(OUTCOME_KEY)}"
        for child in child_states
    ) or "(no tasks)"
    _write_output_file(
        output_path,
        (
            f"Spec: {spec}\n"
            f"Planner output: {plan_output}\n"
            f"Tasks loaded: {len(items)}\n\n"
            f"Per-task outcomes:\n{checklist}\n\n"
            "Result: recap written\n"
        ),
    )
    return {OUTCOME_KEY: OUTCOME_SUCCESS}

