"""Per-node record shapes shared by every graph."""

from __future__ import annotations

from typing import Optional, TypedDict


class BasicNodeState(TypedDict, total=False):
    attempt_count: int  # times THIS node has executed (first run, loop-back re-entry, or a peer's reject landing here)
    result_line: Optional[str]
    output_path: Optional[str]
    usage: Optional[dict]


class GateNodeState(BasicNodeState, total=False):
    route: Optional[str]
    halt_reason: Optional[str]


class BaseGraphState(TypedDict, total=False):
    run_dir: str
    halted: bool
    halt_reason: Optional[str]
    halted_at_node: Optional[str]
    redrive_node: Optional[str]
    reset_attempts: bool
    redrive_message: Optional[str]
    outcome: Optional[str]
    worker_cli: Optional[str]
