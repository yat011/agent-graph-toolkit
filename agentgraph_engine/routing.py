"""Shared branch-routing convention for every accept/reject/manual/unknown `Result:` gate in the
ported template graphs (fixes the review finding that `route_after_tech_review`/
`route_after_review` never checked the reject keyword at all, and de-duplicates the two
near-identical routing functions into one reusable router).

A gate node (`tech_plan_reviewer`, `review`, `load_tasks`, `final_review`) classifies its own
dispatch's `Result:` line via `classify_gate` and merges the returned dict straight into its own
state update -- this is also where the `halted`-for-redrive bookkeeping a human's
`agentgraph redrive` needs gets written (see cli.py's `cmd_redrive`), so the paired
`route_after_*` conditional-edge function (which calls `gate_route`) stays a pure "read state,
pick a target string" function with no side effects, as LangGraph conditional edges require --
never re-deriving the classification itself.

Classification, in order:
  1. Result: line starts with the accept keyword          -> ACCEPT
  2. Result: line starts with the reserved `manual` keyword -> MANUAL (explicit human escape
     hatch -- bypasses the attempt-count check entirely, unlike a rejection)
  3. Result: line starts with the reject keyword (if this gate has one) ->
       - retry_target's own attempt count already at/over max_retry_attempts -> MANUAL
       - otherwise                                                          -> LOOP_BACK
  4. Anything else (garbled/missing/unrecognized) -> soft failure, retry this same node itself,
     bounded by its own attempt/retry budget (`max_self_attempts`, mirroring the magnitude of the
     node's own `dispatch_with_retry(retry=...)` budget):
       - this gate's own self-retry count already at/over max_self_attempts -> MANUAL
       - otherwise                                                          -> SELF_RETRY

A gate with no reject keyword (`reject_keyword=None` -- feature-kickoff's `load_tasks` and
`final_review`, which have no loop-back edge in the original graph.md) is the router's degenerate
binary form: everything that isn't the accept keyword or `manual` falls straight into step 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ACCEPT = "accept"
LOOP_BACK = "loop_back"
MANUAL = "manual"
SELF_RETRY = "self_retry"

DEFAULT_MANUAL_KEYWORD = "manual"


@dataclass(frozen=True)
class GateConfig:
    """One gate's own accept/reject/manual/unknown convention.

    `reject_keyword`/`retry_target`/`retry_attempt_key`/`max_retry_attempts` are all `None` for a
    degenerate binary gate with no reject-with-loop-back (`load_tasks`, `final_review`).
    """

    result_key: str  # state key holding this gate's Result: line text
    accept_keyword: str
    manual_keyword: str = DEFAULT_MANUAL_KEYWORD

    reject_keyword: Optional[str] = None
    retry_target: Optional[str] = None  # loop-back node id, or None for a degenerate gate
    retry_attempt_key: Optional[str] = None  # state key counting retry_target's own attempts
    max_retry_attempts: Optional[int] = None  # reject-loop budget, checked against retry_attempt_key

    self_node: str = ""  # this gate node's own id -- self-retry target and no-loop-back redrive target
    self_attempt_key: str = ""  # state key counting this gate's own self-retry count (this visit)
    max_self_attempts: int = 0  # self-retry-on-unrecognized budget (0 = no retry, immediate MANUAL)


def route_key_for(config: GateConfig) -> str:
    return f"{config.result_key}_route"


def halt_reason_key_for(config: GateConfig) -> str:
    return f"{config.result_key}_halt_reason"


def classify_gate(state: dict, config: GateConfig) -> dict:
    """Classify one gate's `Result:` line. Returns the state-update dict the gate node function
    merges into its own return value -- call this exactly once per dispatch, from the node
    function itself, never from the paired `route_after_*` router (see module docstring).
    """
    line = (state.get(config.result_key) or "").strip()
    route_key = route_key_for(config)
    reason_key = halt_reason_key_for(config)

    if line.startswith(config.accept_keyword):
        return {route_key: ACCEPT}

    if line.startswith(config.manual_keyword):
        return {route_key: MANUAL, reason_key: "manual_requested"}

    if config.reject_keyword is not None and line.startswith(config.reject_keyword):
        attempts = state.get(config.retry_attempt_key, 0) if config.retry_attempt_key else 0
        if config.max_retry_attempts is not None and attempts >= config.max_retry_attempts:
            return {route_key: MANUAL, reason_key: "reject_attempts_exhausted"}
        return {route_key: LOOP_BACK}

    # Unrecognized/garbled Result: line -- soft failure, retry this same node.
    self_attempts = state.get(config.self_attempt_key, 0) if config.self_attempt_key else 0
    if self_attempts >= config.max_self_attempts:
        return {route_key: MANUAL, reason_key: "unrecognized_result_exhausted"}
    return {route_key: SELF_RETRY}


def gate_route(state: dict, config: GateConfig, *, accept_target: str, manual_target: str) -> str:
    """Translate the classification `classify_gate` already wrote into state into this gate's
    actual graph-edge target. Pure read of state -- no side effects, no re-classification.
    """
    route = state.get(route_key_for(config))
    if route == ACCEPT:
        return accept_target
    if route == LOOP_BACK:
        return config.retry_target
    if route == SELF_RETRY:
        return config.self_node
    return manual_target  # MANUAL, or a missing/unexpected value -- default toward a human.


def next_self_retry_count(state: dict, config: GateConfig) -> int:
    """This gate's own self-retry counter for the CURRENT visit: continues counting up while
    being re-entered via its own SELF_RETRY edge, resets to 0 on any other kind of entry (a fresh
    visit from upstream, e.g. after a loop-back or the very first dispatch).
    """
    is_self_retry = state.get(route_key_for(config)) == SELF_RETRY
    return (state.get(config.self_attempt_key, 0) + 1) if is_self_retry else 0
