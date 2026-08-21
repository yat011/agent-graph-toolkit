# run-graph.js CLI Contract

Dependency-free Node.js CLI. All state lives in `run-state.json` on disk; each invocation reads,
computes, writes, and exits. stdout carries exactly one JSON object per invocation; every response
includes `schemaVersion: 1`. Exit code `0` covers every well-formed computed answer (including
`blocked`/`halted`/`complete`); exit code `1` is reserved for command-level errors (bad args,
corrupt state, missing graph.md, misuse).

Every `record-result`/`record-branch`/`record-halt`/`invalidate` call also appends one line to
`progress.log` next to `run-state.json` (see GRAPH-SPEC.md's file layout). Unlike `run-state.json`
(rewritten wholesale on every mutation), this file is append-only — earlier lines never change —
so reorienting on a run (a human skimming it, or a fresh `graph-runner` hop with no prior context)
can cheaply tail its last few lines instead of re-reading/re-parsing the full JSON state just to
see what happened recently. It is a convenience trail, not authoritative — `status` / `next` are
still the source of truth for what to do next.

## Commands

### `resolve-run --graph <name> [--redrive] [--fresh] [--slug <text>] [--graphs-root <path>]`

Finds or starts a run for `{graphs-root}/{graph}/graph.md` (default `graphs-root`:
`agent_works/graphs` under cwd).

`--slug` only affects a brand-new run (`mode: "new"`) — it's ignored on resume/redrive, since
those reuse an existing run's already-established folder. When given, the new run folder is named
`{slug}_{graph}_{timestamp}` instead of the bare `{graph}_{timestamp}`; `slug` is sanitized the
same way `graph` is (non-alphanumeric runs collapsed to a single `-`, leading/trailing `-`
trimmed). Pass a short identifier for what this run is actually about (e.g. the feature/idea slug
the run's input concerns) so a `runs/` directory with several runs stays scannable — see
GRAPH-SPEC.md's file layout section for the full naming convention.

Responses:
- `{status:"ready", mode:"new"|"resume"|"redrive", run_path}`
- `{status:"blocked", reason:"halted_run_exists", run_path, halt_reason}`
- `{status:"blocked", reason:"nothing_to_redrive"}`

### `next --run <run_path> [--graphs-root <path>]`

Recurses through nested map/subgraph structure internally. Returns a fully composed prompt
(readiness-check text, redrive notice, invocation-context all pre-appended). If any graph
referenced during this call's resolution (the top-level graph itself, or a nested one reached via
a `subgraph`/`map` node) had no local `graph.md` and was auto-copied in from the skill's own
`templates/{name}/`, every such graph name is listed in an added `copied_templates: string[]`
field on the response; the field is omitted (or empty) when nothing was copied during that call.

`next` never returns `dispatch` for a node with `receipt: true`. The engine synthesizes that
node's `attempt-N/output.md`, marks it `completed` (same `run-state.json` shape as a real leaf),
and continues resolving in the same call until it has a real dispatch, or the run is
`complete`/`halted`. Synthesized receipts do not increment `total_executions` — that counter
counts dispatches.

Map items honor `itemsSource[].dependencies` (other item **ids**). An item does not start until
every listed id's map item is `completed` and reached a success terminal (`04_success` completed
for a nested subgraph; the item itself for a leaf map). `next` skips a not-ready array-order
item and dispatches the next ready one. In-progress items are returned first. Permanently
blocked remaining items (dep hit `05_manual_flag`, missing id, finished without success) stay
pending and the map completes — so a later final-review node can see the missing `04_success`.
If nothing is ready or in-progress and some items still wait on unfinished deps (cycle),
`next` returns `{status:"halted", halt_reason:"unmet_dependencies"}`.

Responses:
- `{status:"dispatch", run_path, node_id, node_type, item?, attempt, output_path, agent, model, prompt, has_branches, is_redrive, is_invalidated, copied_templates?}`
- `{status:"needs_branch", run_path, node_id, copied_templates?}` — this node already succeeded
  (`record-result` ran) but no `record-branch` call was ever recorded for it, most likely because
  the process/session was interrupted between the two calls. Nothing to dispatch: re-read that
  node's `output.md` and call `record-branch` for it before calling `next` again.
- `{status:"complete", run_path, copied_templates?}`
- `{status:"halted", run_path, halt_reason, copied_templates?}`

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

### `invalidate --run <run_path> --node <id> --reason "<text>" [--graphs-root <path>]`

Forces a targeted re-run of a node whose output a human has determined is wrong, plus everything
transitively downstream of it (per `deps`, and a `type: map` node's `map_over` source), without
touching any other node. The target node must currently be `completed` or `bypassed` — exits 1 if
the run doesn't exist, the node id is unknown, the node is still `pending` (never executed), or
`running` (use `record-result`/wait instead). Every downstream node that is `completed`/`bypassed`
is set to `invalidated` too; downstream nodes already `pending`/`invalidated` are left alone. If
any downstream node is `running`, the command refuses and mutates nothing (exits 1) rather than
touch an in-flight run.

`invalidated` is a distinct `run-state.json` status from `pending` — see GRAPH-SPEC.md — so the
node's prior `attempt-N/` folders stay on disk for audit. Only the directly-targeted node stores
the human-supplied `--reason` (as `invalidated_reason`); cascaded downstream nodes instead store
`invalidated_because: <nodeId>`, tracing back to the originally-invalidated node's own reason.
`next`'s composed prompt for an invalidated node's next dispatch prepends an invalidation notice
(analogous to the redrive notice) including that reason — directly for the targeted node, or "an
upstream dependency was invalidated: `<reason>`" for a cascaded one — and the dispatch response
carries `is_invalidated: true` for that one call. A fresh dispatch for an invalidated node bumps
its `attempt` as usual (a new `attempt-N/` folder), it does not overwrite the prior attempt.

Response: `{status:"ok", run_path, node_id, node_status:"invalidated", downstream_invalidated: string[]}`.
Exits 1 (no mutation) if the run is already halted, `--reason` is empty, the node id is unknown,
the node isn't `completed`/`bypassed`, or a downstream node is `running`.
