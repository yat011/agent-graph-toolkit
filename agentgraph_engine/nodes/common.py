"""Node functions shared by every graph."""

from __future__ import annotations


def halted(state: dict) -> dict:
    """No-op terminal. Routing already recorded halt fields on the graph-level state."""
    return {}
