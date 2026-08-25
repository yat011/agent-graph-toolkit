"""Pause a graph with LangGraph `interrupt()` instead of routing to a dead-end terminal.

When a gate's reject budget is exhausted, a Worker dispatch dies, or a `Result:` line is
unrecognized/`manual`, the graph PAUSES. `agentgraph redrive` then `Command(resume=...)`s.
The pause node (or the nested-task wrapper) issues `Command(goto=redrive_node, update=...)`.

Every pause zeroes `attempt_count` on nested node records so a redrive cannot immediately
re-hit the same cap (deadlock). Reject-budget exhaustion redrives the **code-writer**
(implement / planner). Gate `Result: manual` / unrecognized redrives **that gate**. A
technical `retries_exhausted` pause redrives the failed node.
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.types import Command, interrupt

from agentgraph_engine.constants import (
    ATTEMPT_COUNT_KEY,
    HALT_REASON_KEY,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALTED_AT_NODE_KEY,
    HALTED_KEY,
    OUTCOME_KEY,
    PAUSE_NODE,
    REDRIVE_MESSAGE_KEY,
    REDRIVE_NODE_KEY,
    RESET_ATTEMPTS_KEY,
)

INTERRUPT_REASON_KEY = "reason"
INTERRUPT_REDRIVE_NODE_KEY = "redrive_node"
INTERRUPT_RESET_ATTEMPTS_KEY = "reset_attempts"
INTERRUPT_PARENT_NODE_KEY = "parent_node"
INTERRUPT_CHECKPOINT_NS_KEY = "checkpoint_ns"


def halt_fields(*, reason: str, redrive_node: str, reset_attempts: bool = True) -> dict:
    """Graph-level fields that tell `pause` / `redrive` where to jump and whether to zero counters."""
    return {
        HALTED_KEY: True,
        HALT_REASON_KEY: reason,
        HALTED_AT_NODE_KEY: redrive_node,
        REDRIVE_NODE_KEY: redrive_node,
        RESET_ATTEMPTS_KEY: reset_attempts,
    }


def gate_redrive_node(*, halt_reason: str, writer: str, gate: str) -> str:
    """Reject budget → writer. `Result: manual` / unrecognized → the gate itself."""
    if halt_reason == HALT_REJECT_ATTEMPTS_EXHAUSTED:
        return writer
    return gate


def reset_nested_attempt_records(values: dict) -> dict:
    """Zero every nested node record's `attempt_count`. Route/result fields stay; the redriven
    node overwrites its own record when it runs.
    """
    updates: dict = {}
    for key, value in values.items():
        if not isinstance(value, dict) or ATTEMPT_COUNT_KEY not in value:
            continue
        reset = dict(value)
        reset[ATTEMPT_COUNT_KEY] = 0
        updates[key] = reset
    return updates


def message_from_resume(resume_value: Any) -> str | None:
    """Pull an optional human note out of `Command(resume=...)`."""
    if resume_value is None:
        return None
    if isinstance(resume_value, dict):
        raw = resume_value.get("message")
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None
    if isinstance(resume_value, str) and resume_value not in {"", "redrive"}:
        return resume_value.strip() or None
    return None


def resume_value_for_redrive(message: str | None) -> str | dict:
    """CLI / wrapper payload: bare `"redrive"` or a dict that carries `--message`."""
    if message:
        return {"action": "redrive", "message": message}
    return "redrive"


def redrive_note_block(state: dict) -> str:
    """Suffix for a Worker prompt when a human passed `--message` on redrive."""
    note = (state.get(REDRIVE_MESSAGE_KEY) or "").strip()
    if not note:
        return ""
    return (
        "\nHuman redrive note — treat as instruction for this attempt "
        "(example: a finding is non-blocking):\n"
        f"{note}\n"
    )


def _interrupt_value(item: Any) -> Any:
    return getattr(item, "value", item)


def interrupt_payload_from_result(result: dict) -> Optional[dict]:
    """Pull the first interrupt payload out of an `.invoke()` return dict."""
    items = result.get("__interrupt__")
    if not items:
        return None
    val = _interrupt_value(items[0])
    return val if isinstance(val, dict) else None


def interrupt_payload_from_snapshot(snapshot: Any) -> Optional[dict]:
    """Pull the first interrupt payload out of `compiled.get_state(...)`."""
    items = list(getattr(snapshot, "interrupts", None) or ())
    if not items:
        values = getattr(snapshot, "values", None) or {}
        raw = values.get("__interrupt__") if isinstance(values, dict) else None
        if raw:
            items = list(raw)
    if not items:
        return None
    val = _interrupt_value(items[0])
    return val if isinstance(val, dict) else None


def pause_payload(state: dict, *, extra: Optional[dict] = None) -> dict:
    reason = state.get(HALT_REASON_KEY)
    redrive = state.get(REDRIVE_NODE_KEY) or state.get(HALTED_AT_NODE_KEY)
    if RESET_ATTEMPTS_KEY in state and state.get(RESET_ATTEMPTS_KEY) is not None:
        reset = bool(state.get(RESET_ATTEMPTS_KEY))
    else:
        reset = True
    payload = {
        INTERRUPT_REASON_KEY: reason,
        INTERRUPT_REDRIVE_NODE_KEY: redrive,
        INTERRUPT_RESET_ATTEMPTS_KEY: reset,
    }
    if extra:
        payload.update(extra)
    return payload


def pause(state: dict) -> Command:
    """Shared pause node: interrupt, then on redrive jump to `redrive_node`.

    Every pause zeroes nested `attempt_count`s when `reset_attempts` is set (the default)
    so the redrive target starts a fresh loop. An optional human note on `Command(resume=)`
    is stored as `redrive_message` for the target node's Worker prompt.
    """
    payload = pause_payload(state)
    resume_value = interrupt(payload)
    redrive = payload[INTERRUPT_REDRIVE_NODE_KEY]
    updates = {
        HALTED_KEY: False,
        HALT_REASON_KEY: None,
        HALTED_AT_NODE_KEY: None,
        REDRIVE_NODE_KEY: None,
        RESET_ATTEMPTS_KEY: False,
        OUTCOME_KEY: None,
        REDRIVE_MESSAGE_KEY: message_from_resume(resume_value),
    }
    if payload[INTERRUPT_RESET_ATTEMPTS_KEY]:
        updates.update(reset_nested_attempt_records(state))
    return Command(goto=redrive, update=updates)


def route_to_pause_if_halted(state: dict, otherwise: str) -> str:
    return PAUSE_NODE if state.get(HALTED_KEY) else otherwise
