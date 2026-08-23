"""Shared branch routing for every accept/reject/manual/unrecognized `Result:` gate.

A gate node classifies its own dispatch's `Result:` line via `classify_gate` and merges the
returned fields into that gate's own nested state record. The paired `route_after_*`
conditional-edge function calls `gate_route` and stays a pure "read state, pick a target string"
function — no side effects, no re-classification.

Classification, in order:
  1. Result: line starts with RESULT_ACCEPT -> ACCEPT
  2. Result: line starts with RESULT_MANUAL -> MANUAL (halt_reason: manual_requested)
  3. Result: line starts with RESULT_REJECT and retry_target is set ->
       - retry_target's attempt_count already at/over max_retry_attempts -> MANUAL
         (halt_reason: reject_attempts_exhausted)
       - otherwise -> LOOP_BACK
  4. Anything else (garbled/missing/unrecognized, or reject with no retry_target) -> MANUAL
     immediately (halt_reason: unrecognized_result). No retry hop.

Budget checks read `state[retry_target][ATTEMPT_COUNT_KEY]`. Route and halt_reason live on the
gate's own record (`state[self_node][ROUTE_KEY]`, `state[self_node][HALT_REASON_KEY]`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agentgraph_engine.constants import (
    ACCEPT,
    ATTEMPT_COUNT_KEY,
    HALT_MANUAL_REQUESTED,
    HALT_REASON_KEY,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_UNRECOGNIZED_RESULT,
    LOOP_BACK,
    MANUAL,
    RESULT_ACCEPT,
    RESULT_KEY,
    RESULT_MANUAL,
    RESULT_REJECT,
    ROUTE_KEY,
)


@dataclass(frozen=True)
class GateConfig:
    """One gate's retry budget for reject-with-loop-back routing."""

    retry_target: Optional[str] = None
    max_retry_attempts: Optional[int] = None


def _record(state: dict, node_id: str | None) -> dict:
    if not node_id:
        return {}
    value = state.get(node_id)
    return value if isinstance(value, dict) else {}


def classify_gate(state: dict, config: GateConfig, self_node: str) -> dict:
    """Classify one gate's `Result:` line.

    Returns the fields to merge into this gate's own nested record — call exactly once per
    dispatch, from the node function itself, never from the paired `route_after_*` router.
    """
    line = (_record(state, self_node).get(RESULT_KEY) or "").strip()

    if line.startswith(RESULT_ACCEPT):
        return {ROUTE_KEY: ACCEPT}

    if line.startswith(RESULT_MANUAL):
        return {ROUTE_KEY: MANUAL, HALT_REASON_KEY: HALT_MANUAL_REQUESTED}

    if config.retry_target is not None and line.startswith(RESULT_REJECT):
        attempts = _record(state, config.retry_target).get(ATTEMPT_COUNT_KEY, 0)
        if config.max_retry_attempts is not None and attempts >= config.max_retry_attempts:
            return {ROUTE_KEY: MANUAL, HALT_REASON_KEY: HALT_REJECT_ATTEMPTS_EXHAUSTED}
        return {ROUTE_KEY: LOOP_BACK}

    return {ROUTE_KEY: MANUAL, HALT_REASON_KEY: HALT_UNRECOGNIZED_RESULT}


def gate_route(state: dict, config: GateConfig, self_node: str, *, accept_target: str, manual_target: str) -> str:
    """Translate the classification `classify_gate` already wrote into this gate's edge target.

    Pure read of state — no side effects, no re-classification.
    """
    route = _record(state, self_node).get(ROUTE_KEY)
    if route == ACCEPT:
        return accept_target
    if route == LOOP_BACK:
        return config.retry_target or manual_target
    return manual_target
