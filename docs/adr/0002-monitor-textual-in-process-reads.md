# 0002: Monitor is a Textual subcommand reading engine internals in-process

## Status

Accepted

## Context

No TUI library existed in the toolkit (`pyproject.toml` had only `langgraph`,
`langgraph-checkpoint-sqlite`, `grandalf`). The existing `agentgraph status` reads one Run via a
subprocess and prints JSON to stdout — fine for one Run, but the Monitor needs to poll a fleet of
Runs on a timer. Options considered:

- **Framework**: Textual (interactive widgets, keyboard nav, timer-driven refresh) vs. Rich
  (simpler live-rendering, weaker interactivity) vs. stdlib `curses` (no new dependency, worse on
  Windows, more manual layout work).
- **Integration**: import `agentgraph_engine` directly and open each Run's `SqliteSaver`
  read-only in-process vs. shell out to `agentgraph status --run <path>` per Run per poll tick.

## Decision

Build `agentgraph monitor` on Textual. It imports `agentgraph_engine` (`runs.py`, `pause.py`)
directly and reads each Run's checkpoint state in-process, rather than shelling out to the CLI.

## Consequences

Adds Textual as a new dependency. Avoids spawning a subprocess and parsing stdout per Run per
poll tick, which would not scale as the fleet grows. The cost: the Monitor is coupled to internal
module layout (`runs.py`/`pause.py`) rather than the CLI's stable JSON surface, so a future
refactor of engine internals must account for it alongside `cli.py`.
