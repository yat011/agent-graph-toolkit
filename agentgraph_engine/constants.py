"""Shared mechanism and business vocabulary for agentgraph_engine and the shipped templates.

Repeats across files live here. Repeats within one file stay module-level there. Anything else
stays inline.
"""

from __future__ import annotations

# Mechanism vocabulary — route labels written onto a gate node's own record.
ACCEPT = "accept"
LOOP_BACK = "loop_back"
MANUAL = "manual"

# Mechanism vocabulary — halt_reason strings (graph-level and nested on a gate record).
HALT_RETRIES_EXHAUSTED = "retries_exhausted"
HALT_UNMET_DEPENDENCIES = "unmet_dependencies"
HALT_MANUAL_REQUESTED = "manual_requested"
HALT_REJECT_ATTEMPTS_EXHAUSTED = "reject_attempts_exhausted"
HALT_UNRECOGNIZED_RESULT = "unrecognized_result"

GATE_HALT_REASONS = {
    HALT_MANUAL_REQUESTED,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_UNRECOGNIZED_RESULT,
}

# Mechanism vocabulary — pause (interrupt) node id, leftover no-op terminal, Result: key.
PAUSE_NODE = "pause_node"
HALTED_NODE = "halted_node"
RESULT_KEY = "result_line"

# Mechanism vocabulary — graph-level and nested-record field names.
HALTED_KEY = "halted"
HALT_REASON_KEY = "halt_reason"
HALTED_AT_NODE_KEY = "halted_at_node"
REDRIVE_NODE_KEY = "redrive_node"
RESET_ATTEMPTS_KEY = "reset_attempts"
REDRIVE_MESSAGE_KEY = "redrive_message"
CURRENT_ITEM_KEY = "current_item"
CURRENT_ITEM_INDEX_KEY = "current_item_index"
ATTEMPT_COUNT_KEY = "attempt_count"
OUTPUT_PATH_KEY = "output_path"
ROUTE_KEY = "route"
OUTCOME_KEY = "outcome"
RUN_DIR_KEY = "run_dir"
ITEM_KEY = "item"
ITEMS_KEY = "items"
SPEC_PATH_KEY = "spec_path"
MAP_TASK_STATES_KEY = "map_task_states"
WORKER_CLI_KEY = "worker_cli"
USAGE_KEY = "usage"
STDERR_KEY = "stderr"
STDOUT_KEY = "stdout"
RETURNCODE_KEY = "returncode"


# Mechanism vocabulary — dispatch role / model aliases used by more than one graph.
ROLE_GENERAL_PURPOSE = "general-purpose"
MODEL_CHEAP = "cheap"

# Mechanism vocabulary — Worker CLI identities (vendor headless CLI for a process).
WORKER_CLI_CLAUDE = "claude"
WORKER_CLI_GROK = "grok"
WORKER_CLI_CURSOR = "cursor"
WORKER_CLI_IDENTITIES = frozenset({WORKER_CLI_CLAUDE, WORKER_CLI_GROK, WORKER_CLI_CURSOR})
WORKER_CLI_CURSOR_BINARY = "cursor-agent"

# On-disk attempt-folder names for a nested standard-task run.
STANDARD_TASK_SUCCESS_DIR = "04_success"

# Business vocabulary — Result: phrases used identically by every gate in both templates.
RESULT_ACCEPT = "accepted"
RESULT_REJECT = "rejected"
RESULT_MANUAL = "manual"

# Business vocabulary — implement_requirements Result: phrases (standard-task).
RESULT_IMPLEMENTED = "implemented"
RESULT_STOPPED = "stopped"

# Business vocabulary — graph outcome values (not node ids).
OUTCOME_SUCCESS = "success"
OUTCOME_BLOCKED = "blocked"

# standard-task production node ids.
IMPLEMENT_REQUIREMENTS_NODE = "implement_requirements_node"
REVIEW_NODE = "review_node"
SUCCESS_NODE = "success_node"

# feature-kickoff production node ids.
CREATE_FEATURE_BRANCH_NODE = "create_feature_branch_node"
PLANNER_NODE = "planner_node"
TECH_PLAN_REVIEWER_NODE = "tech_plan_reviewer_node"
LOAD_TASKS_NODE = "load_tasks_node"
PICK_NEXT_TASK_NODE = "pick_next_task_node"
RUN_ONE_TASK_NODE = "run_one_task_node"
FINAL_REVIEW_NODE = "final_review_node"
