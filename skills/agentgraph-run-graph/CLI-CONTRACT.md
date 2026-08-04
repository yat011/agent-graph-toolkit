# run-graph.js CLI Contract

Dependency-free Node.js CLI. All state lives in `run-state.json` on disk; each invocation reads,
computes, writes, and exits. stdout carries exactly one JSON object per invocation; every response
includes `schemaVersion: 1`. Exit code `0` covers every well-formed computed answer (including
`blocked`/`halted`/`complete`); exit code `1` is reserved for command-level errors (bad args,
corrupt state, missing graph.md, misuse).

## Commands

### `resolve-run --graph <name> [--redrive] [--fresh] [--graphs-root <path>]`

Finds or starts a run for `{graphs-root}/{graph}/graph.md` (default `graphs-root`:
`agent_works/graphs` under cwd).

Responses:
- `{status:"ready", mode:"new"|"resume"|"redrive", run_path}`
- `{status:"blocked", reason:"halted_run_exists", run_path, halt_reason}`
- `{status:"blocked", reason:"nothing_to_redrive"}`

### `next --run <run_path> [--graphs-root <path>]`

Recurses through nested map/subgraph structure internally. Returns a fully composed prompt
(readiness-check text, redrive notice, invocation-context all pre-appended).

Responses:
- `{status:"dispatch", run_path, node_id, node_type, item?, attempt, output_path, agent, model, prompt, has_branches, is_redrive}`
- `{status:"needs_branch", run_path, node_id}` — this node already succeeded (`record-result` ran)
  but no `record-branch` call was ever recorded for it, most likely because the process/session
  was interrupted between the two calls. Nothing to dispatch: re-read that node's `output.md` and
  call `record-branch` for it before calling `next` again.
- `{status:"complete", run_path}`
- `{status:"halted", run_path, halt_reason}`

### `record-result --run <run_path> --node <id> [--item <item-N>] --outcome success|technical_failure`

Applies the retry-or-complete/halt transition for a leaf or map-item execution.

Response: `{status:"ok", run_path, node_status}`. Exits 1 (no mutation) if the run is already halted.

### `record-branch --run <run_path> --node <id> (--match "<condition text>" | --default | --none)`

Applies branch evaluation, bypass marking, and the bypassed-target override. No `--item` flag —
branches are evaluated once per node, never per map item.

Response: `{status:"ok", run_path, run_status?}`.

### `record-halt --run <run_path> --node <id> --reason capability_gap [--detail "<text>"]`

Agent-initiated halt for the capability-gap judgment. Works even if the node has no prior
`run-state.json` entry.

Response: `{status:"ok", run_path}`. Exits 1 if the run is already halted.

### `status --run <run_path>`

Response: `{status, total_executions, halt_reason, nodes}` — full run-state summary.

## Implementation Audit

Checked against what Tasks 3-10 actually implemented:

- `resolve-run` — matches contract as written.
- `next` — matches contract as written. `item` field only present on map-item dispatches. Added a
  `needs_branch` status (not in the original draft) for the interrupted-between-record-result-and-
  record-branch case, so resume never silently skips a pending branch judgment.
- `record-result` — matches contract as written.
- `record-branch` — matches contract as written.
- `record-halt` — matches contract as written; `--reason` currently only accepts `capability_gap`
  since that's the only agent-initiated halt path (the other two reasons are always recorded by
  the engine itself via `record-result`/`record-branch`).
- `status` — matches contract as written.
