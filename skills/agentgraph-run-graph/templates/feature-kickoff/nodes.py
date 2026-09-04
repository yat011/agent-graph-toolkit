"""feature-kickoff node functions and prompt builders."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agentgraph_engine import review_policy as review_policy_mod
from agentgraph_engine.constants import (
    ADDITIONAL_TEST_NODE,
    ATTEMPT_COUNT_KEY,
    CREATE_FEATURE_BRANCH_NODE,
    CURRENT_ITEM_INDEX_KEY,
    CURRENT_ITEM_KEY,
    FINAL_REVIEWER_NODE,
    HALT_MANUAL_REQUESTED,
    HALT_REASON_KEY,
    HALT_RETRIES_EXHAUSTED,
    HALT_UNMET_DEPENDENCIES,
    HALT_UNRECOGNIZED_RESULT,
    HANDOFF_FILENAME,
    INTEGRATION_FIX_NODE,
    ITEMS_KEY,
    ITEM_KEY,
    LOAD_PHASES_NODE,
    MANUAL,
    MAP_PHASE_STATES_KEY,
    OUTCOME_BLOCKED,
    OUTCOME_KEY,
    OUTCOME_SUCCESS,
    OUTPUT_PATH_KEY,
    PAUSE_NODE,
    PICK_NEXT_PHASE_NODE,
    PLAN_PATH_KEY,
    PLANNER_NODE,
    PREVIOUS_HANDOFF_PATH_KEY,
    RESULT_ACCEPT,
    RESULT_IMPLEMENTED,
    RESULT_KEY,
    RESULT_MANUAL,
    RESULT_REJECT,
    RESULT_STOPPED,
    RETURNCODE_KEY,
    REVIEW_LINE_THRESHOLD_KEY,
    REVIEW_NODE,
    ROUTE_KEY,
    RUN_DIR_KEY,
    RUN_ONE_PHASE_NODE,
    SKIP_REVIEW_COMMIT_NODE,
    SPEC_PATH_KEY,
    STANDARD_TASK_SUCCESS_DIR,
    STDERR_KEY,
    STDOUT_KEY,
    TECH_PLAN_REVIEWER_NODE,
    WORKER_CLI_KEY,
    REDRIVE_MESSAGE_KEY,
)
from agentgraph_engine.dispatch import (
    attach_usage,
    dispatch_with_retry,
    extract_result_line,
    result_phrases,
)
from agentgraph_engine.pause import (
    INTERRUPT_REASON_KEY,
    INTERRUPT_REDRIVE_NODE_KEY,
    INTERRUPT_RESET_ATTEMPTS_KEY,
    gate_redrive_node,
    halt_fields,
    interrupt_payload_from_result,
    interrupt_payload_from_snapshot,
    redrive_note_block,
    resume_value_for_redrive,
)
from agentgraph_engine.routing import any_matching_result_phrase, classify_gate, matches_result_keyword
from agentgraph_engine.runs import node_output_path, slugify
from agentgraph_engine.states.feature_kickoff import FeatureKickoffState

STANDARD_PHASE_GRAPH_PATH = Path(__file__).resolve().parent.parent / "standard-phase" / "graph.py"

BRANCH_NODE_DIR = "01_create_feature_branch"
PLANNER_NODE_DIR = "02_planner"
TECH_REVIEW_NODE_DIR = "03_tech_plan_reviewer"
LOAD_PHASES_NODE_DIR = "04_load_phases"
RUN_PHASES_NODE_DIR = "05_run_phases"
ADDITIONAL_TEST_NODE_DIR = "06_additional_test"
INTEGRATION_FIX_NODE_DIR = "07_integration_fix"
FINAL_REVIEWER_NODE_DIR = "08_final_reviewer"
SUCCESS_NODE_DIR = "09_success"
ADDITIONAL_TEST_SCRIPT_WIN = "additional_test.cmd"
ADDITIONAL_TEST_SCRIPT_POSIX = "additional_test.sh"


def _additional_test_script_name() -> str:
    return ADDITIONAL_TEST_SCRIPT_WIN if sys.platform == "win32" else ADDITIONAL_TEST_SCRIPT_POSIX


def _additional_test_script_path(run_dir: Path) -> Path:
    return run_dir / _additional_test_script_name()


def _additional_test_script_kind() -> str:
    if sys.platform == "win32":
        return "Windows cmd script"
    return "POSIX shell script"


def _additional_test_argv(script_path: Path) -> list[str]:
    if sys.platform == "win32":
        return ["cmd", "/c", str(script_path)]
    shell = shutil.which("bash") or shutil.which("sh")
    if shell:
        return [shell, str(script_path)]
    return [str(script_path)]


def _run_additional_test(script_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _additional_test_argv(script_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


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
            **halt_fields(
                reason=HALT_RETRIES_EXHAUSTED,
                redrive_node=CREATE_FEATURE_BRANCH_NODE,
                reset_attempts=True,
            ),
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
            **halt_fields(
                reason=HALT_RETRIES_EXHAUSTED,
                redrive_node=CREATE_FEATURE_BRANCH_NODE,
                reset_attempts=True,
            ),
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
            **halt_fields(
                reason=HALT_RETRIES_EXHAUSTED,
                redrive_node=CREATE_FEATURE_BRANCH_NODE,
                reset_attempts=True,
            ),
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
    additional_test = _additional_test_script_path(run_dir)
    kind = _additional_test_script_kind()
    prompt = (
        "Write plan, phases JSON, and additional-test at suffix paths.\n"
        "\n"
        f"Additional-test is a {kind}. Engine runs it with repository root as cwd; use "
        "repo-relative paths. If command is `node --test`, pass test-file paths or glob "
        "(`dir/*.test.js`), never a directory — Node 24 treats a directory argument as CJS "
        "module and exits non-zero even when tests exist. Non-zero exit: Windows cmd "
        "`exit /b 1`; POSIX `set -e` or `|| exit 1`. Do not run the script.\n"
        "\n"
        "Do not emit a phase whose job is the unfiltered suite or an e2e/final review. After "
        "all phases the engine runs additional_test, then a final-reviewer agent on the full "
        "branch (spec completeness, seams, integration). Last implementing phase must leave "
        "the feature integration-complete. Write a ## Acceptance / e2e list in the plan. "
        "Review policy is position-independent — do not set review: always merely because a "
        "phase is last.\n"
    )
    if attempt > 1:
        reviewer_dir = run_dir / TECH_REVIEW_NODE_DIR
        if reviewer_dir.exists():
            attempts = sorted(reviewer_dir.glob("attempt-*/output.md"))
            if attempts:
                prompt += (
                    "\nPrevious attempt rejected — read findings first: "
                    f"{attempts[-1]}. Revise plan, phase list, additional-test script to "
                    "address every rejection reason (sticky-research: facts already established "
                    "stay valid unless rejection specifically contradicts them).\n"
                )
    spec_display = str(spec) if spec is not None else "(none)"
    prompt += (
        f"\nSpec: {spec_display}\n"
        f"tech plan: {plan_md}\n"
        f"Phases JSON: {tasks_json}\n"
        f"additional_test_script: {additional_test}\n"
    )
    prompt += redrive_note_block(state)
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
            **halt_fields(
                reason=HALT_RETRIES_EXHAUSTED,
                redrive_node=PLANNER_NODE,
                reset_attempts=True,
            ),
        }
    record[RESULT_KEY] = result.result_line
    return {PLANNER_NODE: record, REDRIVE_MESSAGE_KEY: None}


def _tech_review_prompt(state: FeatureKickoffState) -> str:
    plan_md, tasks_json = _plan_artifact_paths(state)
    spec = _resolve_spec_path(state)
    spec_display = str(spec) if spec is not None else "(none)"
    additional_test = _additional_test_script_path(Path(state[RUN_DIR_KEY]))
    prompt = (
        "Review plan and phases (not spec's own decisions).\n"
        "\n"
        f"Also allowed: `Result: {RESULT_MANUAL} — <reason>` if human needed now rather "
        "than another automatic attempt.\n"
    )
    prompt += (
        f"\nSpec: {spec_display}\n"
        f"tech plan: {plan_md}\n"
        f"Phases JSON: {tasks_json}\n"
        f"additional_test_script: {additional_test}\n"
    )
    prompt += redrive_note_block(state)
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
            **halt_fields(
                reason=HALT_RETRIES_EXHAUSTED,
                redrive_node=TECH_PLAN_REVIEWER_NODE,
                reset_attempts=True,
            ),
        }
    record[RESULT_KEY] = result.result_line
    script = _additional_test_script_path(run_dir)
    if not script.is_file() and matches_result_keyword(record[RESULT_KEY] or "", RESULT_ACCEPT):
        reason = f"{RESULT_REJECT} — additional_test script missing"
        existing = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        _write_output_file(output_path, existing.rstrip() + f"\n\nResult: {reason}\n")
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
    record.update(classify_gate({**state, TECH_PLAN_REVIEWER_NODE: record}, TECH_REVIEW_GATE, TECH_PLAN_REVIEWER_NODE))
    update = {TECH_PLAN_REVIEWER_NODE: record}
    if record.get(ROUTE_KEY) == MANUAL:
        update.update(
            halt_fields(
                reason=record[HALT_REASON_KEY],
                redrive_node=gate_redrive_node(
                    halt_reason=record[HALT_REASON_KEY],
                    writer=PLANNER_NODE,
                    gate=TECH_PLAN_REVIEWER_NODE,
                ),
            )
        )
    update[REDRIVE_MESSAGE_KEY] = None
    return update


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


def load_phases(state: FeatureKickoffState) -> dict:
    from .graph import LOAD_PHASES_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, LOAD_PHASES_NODE)
    output_path = node_output_path(run_dir, LOAD_PHASES_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path), ITEMS_KEY: []}
    tasks_json_path = _resolve_tasks_json_path(state)

    if not tasks_json_path or not Path(tasks_json_path).exists():
        reason = "could not load phases — no readable .tasks.json at the expected plan path"
        _write_output_file(
            output_path,
            f"{reason}\n\nResult: {RESULT_MANUAL} — {reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_PHASES_NODE: record}, LOAD_PHASES_GATE, LOAD_PHASES_NODE))
        update = {LOAD_PHASES_NODE: record}
        if record.get(ROUTE_KEY) == MANUAL:
            update.update(
                halt_fields(
                    reason=record[HALT_REASON_KEY],
                    redrive_node=LOAD_PHASES_NODE,
                    reset_attempts=True,
                )
            )
        return update

    raw = Path(tasks_json_path).read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        reason = f"could not load phases — invalid JSON ({exc.msg})"
        _write_output_file(
            output_path,
            f"{reason}\n\nResult: {RESULT_MANUAL} — {reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_PHASES_NODE: record}, LOAD_PHASES_GATE, LOAD_PHASES_NODE))
        update = {LOAD_PHASES_NODE: record}
        if record.get(ROUTE_KEY) == MANUAL:
            update.update(
                halt_fields(
                    reason=record[HALT_REASON_KEY],
                    redrive_node=LOAD_PHASES_NODE,
                    reset_attempts=True,
                )
            )
        return update

    if not isinstance(parsed, list):
        reason = "could not load phases — phases JSON is not an array"
        _write_output_file(
            output_path,
            f"{reason}\n\nResult: {RESULT_MANUAL} — {reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_PHASES_NODE: record}, LOAD_PHASES_GATE, LOAD_PHASES_NODE))
        update = {LOAD_PHASES_NODE: record}
        if record.get(ROUTE_KEY) == MANUAL:
            update.update(
                halt_fields(
                    reason=record[HALT_REASON_KEY],
                    redrive_node=LOAD_PHASES_NODE,
                    reset_attempts=True,
                )
            )
        return update

    env_reason = _js_env_reason(parsed)
    if env_reason:
        _write_output_file(
            output_path,
            f"{env_reason}\n\nResult: {RESULT_MANUAL} — {env_reason}\n",
        )
        record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
        record.update(classify_gate({**state, LOAD_PHASES_NODE: record}, LOAD_PHASES_GATE, LOAD_PHASES_NODE))
        update = {LOAD_PHASES_NODE: record}
        if record.get(ROUTE_KEY) == MANUAL:
            update.update(
                halt_fields(
                    reason=record[HALT_REASON_KEY],
                    redrive_node=LOAD_PHASES_NODE,
                    reset_attempts=True,
                )
            )
        return update

    items_path = output_path.parent / "items.json"
    items_path.parent.mkdir(parents=True, exist_ok=True)
    items_path.write_text(raw, encoding="utf-8")
    _write_output_file(
        output_path,
        (
            f"Loaded {len(parsed)} phase(s) from {tasks_json_path}\n\n"
            f"Result: {RESULT_ACCEPT}\n"
        ),
    )
    record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
    record[ITEMS_KEY] = parsed
    record.update(classify_gate({**state, LOAD_PHASES_NODE: record}, LOAD_PHASES_GATE, LOAD_PHASES_NODE))
    update = {LOAD_PHASES_NODE: record}
    if record.get(ROUTE_KEY) == MANUAL:
        update.update(
            halt_fields(
                reason=record[HALT_REASON_KEY],
                redrive_node=LOAD_PHASES_NODE,
                reset_attempts=True,
            )
        )
    return update


def _item_already_done(item_run_dir: Path) -> str | None:
    """On-disk idempotency: a resumed map must not re-dispatch an item that already succeeded."""
    if node_output_path(item_run_dir, STANDARD_TASK_SUCCESS_DIR, 1).exists():
        return OUTCOME_SUCCESS
    return None


def pick_next_phase(state: FeatureKickoffState) -> Command:
    items = _record(state, LOAD_PHASES_NODE).get(ITEMS_KEY) or []
    map_states = list(state.get(MAP_PHASE_STATES_KEY) or [])
    outcomes: dict = {}
    for child in map_states:
        iid = (child.get(ITEM_KEY) or {}).get("id")
        if iid:
            outcomes[iid] = child.get(OUTCOME_KEY)
    order = [item.get("id") or f"item-{i}" for i, item in enumerate(items, start=1)]
    by_id = {iid: item for iid, item in zip(order, items)}
    index_of = {iid: i for i, iid in enumerate(order, start=1)}
    remaining = [iid for iid in order if iid not in outcomes]
    ready: list[str] = []
    perm_blocked: list[str] = []
    waiting: list[str] = []
    for iid in remaining:
        deps = by_id[iid].get("dependencies") or []
        missing = [d for d in deps if d not in by_id]
        flagged = [d for d in deps if d in outcomes and outcomes.get(d) != OUTCOME_SUCCESS]
        unresolved = [d for d in deps if d in by_id and d not in outcomes]
        if missing or flagged:
            perm_blocked.append(iid)
        elif unresolved:
            waiting.append(iid)
        else:
            ready.append(iid)
    if ready:
        iid = ready[0]
        return Command(
            goto=RUN_ONE_PHASE_NODE,
            update={CURRENT_ITEM_KEY: by_id[iid], CURRENT_ITEM_INDEX_KEY: index_of[iid]},
        )
    if perm_blocked:
        new_states = list(map_states)
        for iid in perm_blocked:
            new_states.append({ITEM_KEY: by_id[iid], OUTCOME_KEY: OUTCOME_BLOCKED})
        return Command(goto=PICK_NEXT_PHASE_NODE, update={MAP_PHASE_STATES_KEY: new_states})
    if waiting:
        return Command(
            goto=PAUSE_NODE,
            update=halt_fields(
                reason=HALT_UNMET_DEPENDENCIES,
                redrive_node=PICK_NEXT_PHASE_NODE,
                reset_attempts=True,
            ),
        )
    return Command(goto=ADDITIONAL_TEST_NODE)


def _all_handoff_paths(map_states: list) -> list[str]:
    paths: list[str] = []
    for child in map_states:
        if child.get(OUTCOME_KEY) != OUTCOME_SUCCESS:
            continue
        child_run = child.get(RUN_DIR_KEY)
        if not child_run:
            continue
        handoff = Path(child_run) / HANDOFF_FILENAME
        if handoff.is_file():
            paths.append(str(handoff))
    return paths


def _latest_handoff_path(map_states: list) -> str | None:
    paths = _all_handoff_paths(map_states)
    return paths[-1] if paths else None


def make_run_one_phase(phase_graph):
    def run_one_phase(state: FeatureKickoffState, config: RunnableConfig) -> Command:
        item = state[CURRENT_ITEM_KEY]
        index = state[CURRENT_ITEM_INDEX_KEY]
        run_dir = Path(state[RUN_DIR_KEY])
        item_run_dir = run_dir / RUN_PHASES_NODE_DIR / f"item-{index}"
        item_run_dir.mkdir(parents=True, exist_ok=True)
        ns = f"item-{index}"
        thread_id = (config.get("configurable") or {}).get("thread_id", "run")
        child_cfg = {
            "configurable": {"thread_id": f"{thread_id}:{ns}"},
            "recursion_limit": 50,
        }
        plan_md, _tasks_json = _plan_artifact_paths(state)
        map_states = list(state.get(MAP_PHASE_STATES_KEY) or [])
        child_input = {RUN_DIR_KEY: str(item_run_dir), ITEM_KEY: item}
        if WORKER_CLI_KEY in state:
            child_input[WORKER_CLI_KEY] = state[WORKER_CLI_KEY]
        spec = state.get(SPEC_PATH_KEY)
        if spec:
            child_input[SPEC_PATH_KEY] = spec
        child_input[PLAN_PATH_KEY] = str(plan_md)
        previous = _latest_handoff_path(map_states)
        if previous:
            child_input[PREVIOUS_HANDOFF_PATH_KEY] = previous
        if REVIEW_LINE_THRESHOLD_KEY in state:
            child_input[REVIEW_LINE_THRESHOLD_KEY] = state[REVIEW_LINE_THRESHOLD_KEY]
        prior = _item_already_done(item_run_dir)
        if prior is not None:
            result = {RUN_DIR_KEY: str(item_run_dir), ITEM_KEY: item, OUTCOME_KEY: prior}
        else:
            snapshot = phase_graph.get_state(child_cfg)
            child_open = interrupt_payload_from_snapshot(snapshot) or bool(
                getattr(snapshot, "next", None)
            )
            if child_open:
                result = phase_graph.invoke(
                    Command(resume=resume_value_for_redrive(state.get(REDRIVE_MESSAGE_KEY))),
                    config=child_cfg,
                )
            else:
                result = phase_graph.invoke(child_input, config=child_cfg)
            inner = interrupt_payload_from_result(result)
            if inner:
                return Command(
                    goto=PAUSE_NODE,
                    update=halt_fields(
                        reason=inner[INTERRUPT_REASON_KEY],
                        redrive_node=inner[INTERRUPT_REDRIVE_NODE_KEY],
                        reset_attempts=inner[INTERRUPT_RESET_ATTEMPTS_KEY],
                        parent_node=RUN_ONE_PHASE_NODE,
                        checkpoint_ns=ns,
                    ),
                )
        map_states.append(result)
        return Command(goto=PICK_NEXT_PHASE_NODE, update={MAP_PHASE_STATES_KEY: map_states})

    return run_one_phase


_MERGE_BASE_CANDIDATES = ("main", "master", "origin/main", "origin/master")


def _incomplete_phase_ids(state: FeatureKickoffState) -> list[str]:
    incomplete: list[str] = []
    for child in state.get(MAP_PHASE_STATES_KEY) or []:
        outcome = child.get(OUTCOME_KEY)
        if outcome == OUTCOME_SUCCESS:
            continue
        item_id = (child.get(ITEM_KEY) or {}).get("id") or "(unknown)"
        incomplete.append(f"{item_id} ({outcome})")
    return incomplete


def _branch_diff_summary() -> str:
    base = ""
    for other in _MERGE_BASE_CANDIDATES:
        proc = _run_git(["merge-base", "HEAD", other])
        sha = (proc.stdout or "").strip()
        if proc.returncode == 0 and sha:
            base = sha
            break
    if not base:
        return (
            "(could not resolve merge-base with main/master; run `git log --oneline` and "
            "`git diff` against the feature's start commit yourself)\n"
        )
    log = _run_git(["log", "--oneline", f"{base}..HEAD"])
    stat = _run_git(["diff", "--stat", f"{base}...HEAD"])
    names = _run_git(["diff", "--name-only", f"{base}...HEAD"])
    return (
        f"merge-base: {base}\n\n"
        f"git log --oneline:\n{(log.stdout or '').strip() or '(empty)'}\n\n"
        f"git diff --stat:\n{(stat.stdout or '').strip() or '(empty)'}\n\n"
        f"git diff --name-only:\n{(names.stdout or '').strip() or '(empty)'}\n"
    )


def _phase_outcome_lines(state: FeatureKickoffState) -> str:
    lines: list[str] = []
    for child in state.get(MAP_PHASE_STATES_KEY) or []:
        item = child.get(ITEM_KEY) or {}
        iid = item.get("id") or "?"
        title = item.get("title") or ""
        outcome = child.get(OUTCOME_KEY) or "?"
        reviewed = int(_record(child, REVIEW_NODE).get(ATTEMPT_COUNT_KEY) or 0) > 0
        skipped = int(_record(child, SKIP_REVIEW_COMMIT_NODE).get(ATTEMPT_COUNT_KEY) or 0) > 0
        how = "phase-reviewed" if reviewed else ("review-skipped" if skipped else "no-review-record")
        lines.append(f"- {iid} ({title}): {outcome}; {how}")
    return "\n".join(lines) or "(no phases)"


def additional_test(state: FeatureKickoffState) -> dict:
    from .graph import ADDITIONAL_TEST_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, ADDITIONAL_TEST_NODE)
    output_path = node_output_path(run_dir, ADDITIONAL_TEST_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    script = _additional_test_script_path(run_dir)
    stdout = ""
    stderr = ""
    returncode: Optional[int] = None
    suite_failed = False
    manual_reasons: list[str] = []

    incomplete = _incomplete_phase_ids(state)
    if incomplete:
        manual_reasons.append("incomplete phases: " + ", ".join(incomplete))

    if not script.is_file():
        manual_reasons.append(f"additional_test script missing at {script}")
    elif not incomplete:
        try:
            proc = _run_additional_test(script)
        except FileNotFoundError as exc:
            stderr = str(exc)
            returncode = 127
            manual_reasons.append(f"failed to launch additional_test script: {exc}")
        else:
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            returncode = proc.returncode
            if returncode != 0:
                suite_failed = True

    record[STDOUT_KEY] = stdout
    record[STDERR_KEY] = stderr
    record[RETURNCODE_KEY] = returncode
    attempt_dir = output_path.parent
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (attempt_dir / "stderr.txt").write_text(stderr, encoding="utf-8")

    if manual_reasons:
        result_line = f"{RESULT_MANUAL} — {'; '.join(manual_reasons)}"
    elif suite_failed:
        result_line = f"{RESULT_REJECT} — additional tests failed (exit {returncode})"
    else:
        result_line = RESULT_ACCEPT

    rc_display = "n/a" if returncode is None else str(returncode)
    _write_output_file(
        output_path,
        (
            f"Additional test script: {script}\n"
            f"Return code: {rc_display}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}\n\n"
            f"Result: {result_line}\n"
        ),
    )
    record[RESULT_KEY] = extract_result_line(output_path.read_text(encoding="utf-8"))
    record.update(
        classify_gate({**state, ADDITIONAL_TEST_NODE: record}, ADDITIONAL_TEST_GATE, ADDITIONAL_TEST_NODE)
    )
    update = {ADDITIONAL_TEST_NODE: record}
    if record.get(ROUTE_KEY) == MANUAL:
        update.update(
            halt_fields(
                reason=record[HALT_REASON_KEY],
                redrive_node=gate_redrive_node(
                    halt_reason=record[HALT_REASON_KEY],
                    writer=INTEGRATION_FIX_NODE,
                    gate=ADDITIONAL_TEST_NODE,
                ),
                reset_attempts=True,
            )
        )
    return update


def _integration_fix_prompt(state: FeatureKickoffState) -> str:
    test_record = _record(state, ADDITIONAL_TEST_NODE)
    stdout = test_record.get(STDOUT_KEY) or ""
    stderr = test_record.get(STDERR_KEY) or ""
    output = test_record.get(OUTPUT_PATH_KEY) or "(none)"
    plan_md, _tasks_json = _plan_artifact_paths(state)
    spec = state.get(SPEC_PATH_KEY) or "(none)"
    handoffs = _all_handoff_paths(state.get(MAP_PHASE_STATES_KEY) or [])
    handoff_block = "\n".join(handoffs) if handoffs else "(none)"
    prompt = (
        "Fix only what the additional_test suite output names. Do not reopen accepted phase "
        "decisions or expand scope. If the failure needs a product decision, stop.\n"
        "\n"
        f"End output.md with a single-line `Result: {RESULT_IMPLEMENTED}` or "
        f"`Result: {RESULT_STOPPED} — <short reason>`.\n"
        "\n"
        f"additional_test output.md: {output}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}\n"
        f"spec: {spec}\n"
        f"tech plan: {plan_md}\n"
        f"implementer handoffs:\n{handoff_block}\n"
    )
    prompt += redrive_note_block(state)
    return prompt


def integration_fix(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, INTEGRATION_FIX_NODE)
    output_path = node_output_path(run_dir, INTEGRATION_FIX_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=1,
        role="code-writer",
        task_prompt=_integration_fix_prompt(state),
        output_path=output_path,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            INTEGRATION_FIX_NODE: record,
            REDRIVE_MESSAGE_KEY: None,
            **halt_fields(
                reason=HALT_RETRIES_EXHAUSTED,
                redrive_node=INTEGRATION_FIX_NODE,
                reset_attempts=True,
            ),
        }
    phrases = result_phrases(output_path.read_text(encoding="utf-8"))
    implemented = any_matching_result_phrase(phrases, RESULT_IMPLEMENTED)
    stopped = any_matching_result_phrase(phrases, RESULT_STOPPED)
    if implemented is not None:
        try:
            review_policy_mod.commit_dirty_tree("final-fix: additional_test")
        except review_policy_mod.GitOpError as exc:
            existing = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            _write_output_file(
                output_path,
                existing.rstrip()
                + f"\n\nCommit failed: {exc}\n\nResult: {RESULT_MANUAL} — git commit failed\n",
            )
            record[RESULT_KEY] = RESULT_MANUAL
            return {
                INTEGRATION_FIX_NODE: record,
                REDRIVE_MESSAGE_KEY: None,
                **halt_fields(
                    reason=HALT_RETRIES_EXHAUSTED,
                    redrive_node=INTEGRATION_FIX_NODE,
                    reset_attempts=True,
                ),
            }
        record[RESULT_KEY] = implemented
        return {INTEGRATION_FIX_NODE: record, REDRIVE_MESSAGE_KEY: None}
    if stopped is not None:
        record[RESULT_KEY] = stopped
        return {
            INTEGRATION_FIX_NODE: record,
            REDRIVE_MESSAGE_KEY: None,
            **halt_fields(
                reason=HALT_MANUAL_REQUESTED,
                redrive_node=INTEGRATION_FIX_NODE,
                reset_attempts=True,
            ),
        }
    record[RESULT_KEY] = result.result_line
    return {
        INTEGRATION_FIX_NODE: record,
        REDRIVE_MESSAGE_KEY: None,
        **halt_fields(
            reason=HALT_UNRECOGNIZED_RESULT,
            redrive_node=INTEGRATION_FIX_NODE,
            reset_attempts=True,
        ),
    }


def _final_reviewer_prompt(state: FeatureKickoffState) -> str:
    run_dir = Path(state[RUN_DIR_KEY])
    plan_md, _tasks_json = _plan_artifact_paths(state)
    spec = state.get(SPEC_PATH_KEY) or "(none)"
    test_record = _record(state, ADDITIONAL_TEST_NODE)
    stdout = test_record.get(STDOUT_KEY) or ""
    stderr = test_record.get(STDERR_KEY) or ""
    rc = test_record.get(RETURNCODE_KEY)
    rc_display = "n/a" if rc is None else str(rc)
    handoffs = _all_handoff_paths(state.get(MAP_PHASE_STATES_KEY) or [])
    handoff_block = "\n".join(handoffs) if handoffs else "(none)"
    prompt = (
        "Review the assembled feature for spec completeness, cross-phase seams, and "
        "e2e/integration. Skip Fowler smell-baseline hunting already in scope of phase review "
        "unless a seam makes it newly visible. Do not re-run the unfiltered suite. Do not open "
        "phase implementer output.md or phase reviewer output.md beyond the Result lines "
        "implied by the phase outcomes below. Do not commit product code.\n"
        "\n"
        f"End output.md with a single-line `Result: {RESULT_ACCEPT}` or "
        f"`Result: {RESULT_REJECT} — <short reason>` — or, only if a human is needed now, "
        f"`Result: {RESULT_MANUAL} — <reason>`.\n"
        "\n"
        f"spec: {spec}\n"
        f"tech plan: {plan_md}\n"
        f"implementer handoffs:\n{handoff_block}\n"
        "phase outcomes:\n"
        f"{_phase_outcome_lines(state)}\n"
        f"additional_test return code: {rc_display}\n"
        f"additional_test stdout (summary):\n{stdout}\n"
        f"additional_test stderr:\n{stderr}\n"
        "branch diff summary:\n"
        f"{_branch_diff_summary()}\n"
        f"Open `git diff <merge-base>...HEAD` for files you need. Run dir: {run_dir}\n"
    )
    prompt += redrive_note_block(state)
    return prompt


def final_reviewer(state: FeatureKickoffState) -> dict:
    from .graph import FINAL_REVIEWER_GATE

    run_dir = Path(state[RUN_DIR_KEY])
    attempt = _next_attempt(state, FINAL_REVIEWER_NODE)
    output_path = node_output_path(run_dir, FINAL_REVIEWER_NODE_DIR, attempt)
    record: dict = {ATTEMPT_COUNT_KEY: attempt, OUTPUT_PATH_KEY: str(output_path)}
    result = dispatch_with_retry(
        retry=1,
        role="final-reviewer",
        task_prompt=_final_reviewer_prompt(state),
        output_path=output_path,
    )
    attach_usage(record, result)
    if not result.ok:
        return {
            FINAL_REVIEWER_NODE: record,
            REDRIVE_MESSAGE_KEY: None,
            **halt_fields(
                reason=HALT_RETRIES_EXHAUSTED,
                redrive_node=FINAL_REVIEWER_NODE,
                reset_attempts=True,
            ),
        }
    record[RESULT_KEY] = result.result_line
    record.update(
        classify_gate({**state, FINAL_REVIEWER_NODE: record}, FINAL_REVIEWER_GATE, FINAL_REVIEWER_NODE)
    )
    update = {FINAL_REVIEWER_NODE: record}
    if record.get(ROUTE_KEY) == MANUAL:
        update.update(
            halt_fields(
                reason=record[HALT_REASON_KEY],
                redrive_node=FINAL_REVIEWER_NODE,
                reset_attempts=True,
            )
        )
    update[REDRIVE_MESSAGE_KEY] = None
    return update


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


def success(state: FeatureKickoffState) -> dict:
    run_dir = Path(state[RUN_DIR_KEY])
    output_path = node_output_path(run_dir, SUCCESS_NODE_DIR, 1)
    spec = state.get(SPEC_PATH_KEY) or "(no spec path)"
    plan_output = _record(state, PLANNER_NODE).get(OUTPUT_PATH_KEY) or "(no planner output)"
    items = _record(state, LOAD_PHASES_NODE).get(ITEMS_KEY) or []
    child_states = state.get(MAP_PHASE_STATES_KEY) or []
    checklist = "\n".join(
        f"- {(child.get(ITEM_KEY) or {}).get('id')} "
        f"({(child.get(ITEM_KEY) or {}).get('title')}): {child.get(OUTCOME_KEY)}"
        for child in child_states
    ) or "(no phases)"
    _write_output_file(
        output_path,
        (
            f"Spec: {spec}\n"
            f"Planner output: {plan_output}\n"
            f"Phases loaded: {len(items)}\n\n"
            f"Per-phase outcomes:\n{checklist}\n\n"
            "Result: recap written\n"
        ),
    )
    return {OUTCOME_KEY: OUTCOME_SUCCESS}

