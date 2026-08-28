"""Textual fleet TUI: a `DataTable` of Runs plus a per-Run detail screen.

Read-only. Every poll tick — the `set_interval` timer, or a manual refresh — only calls
`discover_runs` (agentgraph_engine.monitor.discovery) and reads checkpointer state
(agentgraph_engine.monitor.status/timing/topology, all built on the read-only opener in
agentgraph_engine.monitor.checkpointer). Nothing here calls `.invoke()`, `Command(...)`,
`update_state`, or opens a writable `SqliteSaver` — the sqlite file on disk is never touched.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from agentgraph_engine.monitor.checkpointer import open_readonly_checkpointer
from agentgraph_engine.monitor.discovery import DiscoveredRun, discover_runs
from agentgraph_engine.monitor.graph_resolve import CHILD_GRAPH_NAME, compiled_graph_for
from agentgraph_engine.monitor.status import (
    STATUS_COMPLETED,
    FleetRow,
    child_row,
    child_thread_ids,
    fleet_row,
    fleet_rows,
)
from agentgraph_engine.monitor.timing import node_timings
from agentgraph_engine.monitor.topology import topology_ascii

DEFAULT_INTERVAL = 3


class DetailScreen(Screen):
    """Status, current node, per-node timings, and topology for one Run or nested child thread.

    `thread_id is None` means "the Run itself" — its own children (`{run_id}:item-*` threads,
    one level only, per `nodes.py:645-680`) are listed in a selectable table below; Enter on a
    child row drills into that child's own status/topology by pushing another `DetailScreen`
    with `thread_id` set, compiled against `CHILD_GRAPH_NAME` instead of the parent's graph.
    """

    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "quit", "Quit")]

    def __init__(self, run: DiscoveredRun, thread_id: str | None = None) -> None:
        super().__init__()
        self.run = run
        self.thread_id = thread_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="detail-body")
        if self.thread_id is None:
            yield DataTable(id="children-table")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#detail-body", Static).update(self._render_text())
        if self.thread_id is None:
            table = self.query_one("#children-table", DataTable)
            table.add_columns("Child thread", "Status", "Current node")
            table.cursor_type = "row"
            for row in self._child_rows():
                table.add_row(row["run_id"], row["status"], row["current_node"] or "", key=row["run_id"])

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(DetailScreen(self.run, event.row_key.value))

    def _child_rows(self) -> list[FleetRow]:
        thread_ids = child_thread_ids(self.run["checkpoint_path"], self.run["run_id"])
        return [child_row(self.run, thread_id) for thread_id in thread_ids]

    def _row(self) -> FleetRow:
        if self.thread_id is None:
            return fleet_row(self.run)
        return child_row(self.run, self.thread_id)

    def _graph_name(self) -> str:
        return self.run["graph_name"] if self.thread_id is None else CHILD_GRAPH_NAME

    def _render_text(self) -> str:
        row = self._row()
        timings = node_timings(self.run["checkpoint_path"], self.thread_id or self.run["run_id"])
        lines = [
            f"Run: {row['run_id']}",
            f"Graph: {row['graph_name']}",
            f"Status: {row['status']}",
            f"Current node: {row['current_node'] or '-'}",
            "",
            "Timings:",
        ]
        if timings:
            lines.extend(f"  {t['node']}: {t['duration']}" for t in timings)
        else:
            lines.append("  (none)")
        lines += ["", "Topology:", self._topology(row)]
        return "\n".join(lines)

    def _topology(self, row: FleetRow) -> str:
        with open_readonly_checkpointer(self.run["checkpoint_path"]) as saver:
            compiled = compiled_graph_for(self.run, self._graph_name(), saver)
            return topology_ascii(compiled, row)


class FleetScreen(Screen):
    """Fleet table: run id, graph name, status, current node. Hides Completed by default."""

    BINDINGS = [Binding("c", "toggle_completed", "Toggle Completed"), Binding("q", "quit", "Quit")]

    def __init__(self, agent_works_root: Path, interval: int) -> None:
        super().__init__()
        self.agent_works_root = agent_works_root
        self.interval = interval
        self.show_completed = False
        self.runs: list[DiscoveredRun] = []
        self.rows: list[FleetRow] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="fleet-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Run ID", "Graph", "Status", "Current Node")
        table.cursor_type = "row"
        self.refresh_fleet()
        self.set_interval(self.interval, self.refresh_fleet)

    def refresh_fleet(self) -> None:
        """Discovery + state-read poll tick. No writes to any Run's checkpoints.sqlite."""
        self.runs = discover_runs(self.agent_works_root)
        self.rows = fleet_rows(self.runs)
        self._render_table()

    def _visible(self) -> list[tuple[DiscoveredRun, FleetRow]]:
        pairs = list(zip(self.runs, self.rows))
        if self.show_completed:
            return pairs
        return [(run, row) for run, row in pairs if row["status"] != STATUS_COMPLETED]

    def _render_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for run, row in self._visible():
            table.add_row(
                row["run_id"],
                row["graph_name"],
                row["status"],
                row["current_node"] or "",
                key=row["run_id"],
            )

    def action_toggle_completed(self) -> None:
        self.show_completed = not self.show_completed
        self._render_table()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        run_id = event.row_key.value
        for run, _row in self._visible():
            if run["run_id"] == run_id:
                self.app.push_screen(DetailScreen(run))
                return


class MonitorApp(App):
    """Read-only fleet monitor over one `--agent-works-root`."""

    def __init__(self, agent_works_root: Path, interval: int = DEFAULT_INTERVAL) -> None:
        super().__init__()
        self.agent_works_root = Path(agent_works_root)
        self.interval = interval

    def on_mount(self) -> None:
        self.push_screen(FleetScreen(self.agent_works_root, self.interval))
