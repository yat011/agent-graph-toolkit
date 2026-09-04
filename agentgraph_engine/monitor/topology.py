"""Per-Run ASCII topology: `compiled.get_graph().draw_ascii()` with the row's current node
highlighted. One diagram per Run instance — never a shared fleet-wide diagram. Child drill-in
passes the child's own compiled graph (standard-phase, for `{run_id}:item-*` threads), not the
parent's.
"""

from __future__ import annotations

import re
from typing import Mapping


def topology_ascii(compiled: object, row: Mapping[str, object]) -> str:
    """Ascii topology for `compiled`, wrapping the row's `current_node` label(s) in `*`.

    A `current_node` that is `None` or does not match any node label in the drawing (e.g. an
    `item n of m` synthesized label) leaves the ascii unhighlighted, without raising.
    """
    ascii_art = compiled.get_graph().draw_ascii()
    node = row.get("current_node")
    if not node:
        return ascii_art
    pattern = re.compile(rf"\b{re.escape(str(node))}\b")
    return pattern.sub(lambda m: f"*{m.group(0)}*", ascii_art)
