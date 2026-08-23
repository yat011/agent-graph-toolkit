# `agentgraph` CLI Contract

`agentgraph` is a thin Python entry point (`agentgraph_engine/cli.py`, installed as the
`agentgraph` console script by this repo's `pyproject.toml`) over a compiled LangGraph
`StateGraph`. Unlike the old engine, **dispatch judgment lives in the Graph's own Python code**,
not in a coordinating LLM reading this CLI's JSON output between every node — so there is no
`next`/`record-result`/`record-branch` step-by-step loop to drive here. A Coordinating agent's
job is reduced to: start a run (or resume/redrive one), then read the terminal JSON summary.

Every command prints exactly one JSON object to stdout. No provider API key is read, accepted, or
forwarded anywhere in this CLI, its underlying `agentgraph_engine` package, or the templates it
loads (CONTEXT.md's "Executor").

## Commands

### `agentgraph start --graph <name> [--slug <slug>] [--spec <path>] [--input-json <path>] [--agent-works-root <path>] [--recursion-limit N]`

Starts a brand-new Run of the named template graph (`feature-kickoff`, `standard-task`, or any
other `skills/agentgraph-run-graph/templates/{name}/graph.py`), loaded dynamically via
`agentgraph_engine.graph_loader` (never copied into a project). Creates
`agent_works/{graph_name}/runs/{run_id}/` (`run_id = {timestamp}_{slug}`, mirroring the old
engine's run-folder convention) with its own `checkpoints.sqlite` (one `SqliteSaver` per Run,
`thread_id == run_id` — CONTEXT.md's "Run"), then drives the compiled graph to completion, a halt,
or an `interrupt()` pause — whichever comes first — via one blocking `.invoke()` call. This is the
literal fire-and-forget requirement from the migration spec: the calling agent makes one call and
gets a terminal summary back; no per-node dispatch loop to drive.

`--input-json` merges an arbitrary JSON object into the graph's initial state (e.g. task items for
a graph that doesn't derive them itself).

Output: `{"run_path": ..., "run_id": ..., "halted": bool, "halt_reason": str|null, "outcome":
str|null, "interrupted"?: bool, "interrupt_value"?: [...]}`.

### `agentgraph resume --run <run_path> [--resume-value <value>] [--recursion-limit N]`

Resumes a Run paused at a LangGraph `interrupt()` call (not a halt — see below), using the same
`run_path`'s `checkpoints.sqlite`. `--resume-value` becomes the interrupted node's `interrupt()`
return value via `Command(resume=...)`. Nodes already completed before the interrupt are **not**
re-executed — this is the mechanism behind the migration's demonstrated checkpoint-resume proof
(see `agentgraph_engine/examples/hello_graph/` and its test).

### `agentgraph status --run <run_path>`

Prints the Run's current checkpointed state: `{"run_path": ..., "next": [...], "values": {...}}`.
`next` is the node(s) LangGraph would run on the next `invoke`/`resume` call (empty if the Run
reached `END`).

### `agentgraph redrive --run <run_path> [--recursion-limit N]`

Re-attempts a **halted** Run's failing node fresh (a technical failure that exhausted its node's
`retry` budget — see `halt_reason: retries_exhausted`/`unmet_dependencies`), without re-running
anything upstream of it. Every halting node in both ported graphs records its own node name as
`halted_at_node` in state; `redrive` walks the checkpoint history
(`compiled.get_state_history()`) for the most recent snapshot whose `.next` is exactly that node,
clears the halt fields there, and forks execution forward from that point. Errors (exit 1, JSON
`{"status":"error", ...}`) if the Run isn't halted, or `halted_at_node` wasn't recorded.

## Halting

Only two halt reasons exist now (no `capability_gap` — there's no coordinating LLM positioned to
make that judgment per-dispatch anymore; a bad node/prompt pairing just surfaces as an ordinary
technical failure):

- `retries_exhausted` — a node's headless-CLI dispatch failed (non-zero exit, or the worker
  didn't write its required output file) more times than its `retry` count allows.
- `unmet_dependencies` — `05_run_tasks`'s sequential map had remaining task items still waiting on
  unfinished `dependencies` (a cycle) with nothing ready to dispatch. A permanently-blocked item
  (a missing dependency id, or one that finished at `manual_flag`) does **not** halt the whole
  map — it's left `blocked` so `06_final_review` can flag it.

## Branch judgment

Every ported node's routing is a plain Python function reading the `Result: <phrase>` line
extracted from the dispatched Worker's output (`agentgraph_engine.dispatch.extract_result_line`)
— string-matching only, never an LLM judgment call. A Coordinating agent invoking
`agentgraph start`/`resume` never judges a branch itself; that logic is compiled into the graph.

## Permission mode by model tier

`--permission-mode auto` lets the model's own action-classifier judge each tool call, but that
classifier requires Sonnet-tier and above. A dispatch resolved to the `haiku` model instead uses
`--permission-mode acceptEdits --allowedTools Write` (no classifier needed; `Write` is the one
tool this dispatch path's contract requires). `DispatchResult.ok` comes back `False` — an
ordinary technical failure, retried/halted like any other — if a dispatch's permission mode still
blocks the write it needs.

## Map/fan-out and subgraph composition

`05_run_tasks` (in `feature-kickoff`) is a single graph node that loops over its task items
**sequentially** (no concurrent dispatch) and, for each ready item, invokes the `standard-task`
template's own compiled `StateGraph` directly as a Python call — a compiled `StateGraph` embeds
as a node inside a parent graph, no subfolder-per-composed-piece, no new node type to learn. Each
item's own artifacts land under
`{run_dir}/05_run_tasks/item-{n}/` using the same `{node_id}/attempt-{n}/output.md` convention as
every other node, so a partially-completed map is inspectable and (via an on-disk idempotency
check keyed on that path) does not re-dispatch an item whose nested run already reached a terminal
on an earlier pass.
