# 0001: Monitor derives Run status and timing entirely from existing checkpoint state

## Status

Accepted

## Context

The planned `agentgraph monitor` needs two things per Run: whether it's actively being worked
right now (vs. merely paused between CLI invocations), and how long each Node took. Two options
were considered for each:

- **Liveness**: add a lock/heartbeat file that `dispatch.py` touches while a Node is executing, so
  the Monitor can tell "a process is in here right now" apart from "idle, waiting for a human" —
  versus inferring status purely from the LangGraph snapshot (`next`, `__interrupt__`,
  `halt_reason`, `outcome`), which already exists for every Run today.
- **Timing**: add explicit `started_at`/`ended_at` fields to Node records — versus deriving
  per-Node duration by diffing the `ts` timestamp LangGraph's `SqliteSaver` already writes on
  every checkpoint, walked along the `parent_checkpoint_id` chain.

## Decision

Derive both purely from what's already persisted. No heartbeat/lock file, no new engine fields.

## Consequences

Zero engine changes — the Monitor works against every Run that exists today, including past
runs. The trade-off: the Monitor cannot distinguish a crashed or orphaned Run from one that's
simply paused between `agentgraph` invocations — both look identical on disk (pending `next`
Nodes, no interrupt). "Running" in the Monitor means "not finished," not "a process is touching
it this instant." Revisit this if orphan/crash detection becomes a real operational need.
