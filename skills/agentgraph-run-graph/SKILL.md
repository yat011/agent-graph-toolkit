---
name: agentgraph-run-graph
description: Use when the user asks to run, execute, resume, or continue a previously-defined agent graph (a graph.py written by the agentgraph-define-graph skill, or one of the built-in feature-kickoff/standard-task templates) — starts/resumes/redrives it via the `agentgraph` CLI and reports its terminal state, without driving individual node dispatches yourself.
---

# agentgraph-run-graph

Executes a graph previously authored by the `agentgraph-define-graph` skill, or one of the two
built-in template graphs (`feature-kickoff`, `standard-task`). All node-by-node mechanics —
dependency ordering, retries, branch judgment, map fan-out, subgraph recursion, checkpointing —
are owned by the compiled LangGraph `StateGraph` itself, driven by the `agentgraph` CLI
(`agentgraph_engine/cli.py`; see `ENGINE-CLI.md` in this same directory for exact command syntax
— this file does not restate it).

Never monitor progress or read run files. Start `agentgraph` in the background and wait only for the subprocess/terminal completion callback; when that process has exited, report "The graph execution is done".

Dispatch and branch judgment live in the graph's own Python code. A node's router function does
plain string-matching against a `Result:` line — never an LLM or human judgment call. You start
or resume a run, then interpret the **one** JSON object the CLI prints back: report done, ask
the user how to proceed on a halt, or resume a deliberate `interrupt()` pause.

## Inputs

- A graph name (required). Resolved via `agentgraph_engine.graph_loader.resolve_graph_path` in
  this order: **project** (`agent_works/graphs/{graph-name}/graph.py`) **> user**
  (`~/.agents/graphs/{graph-name}/graph.py`, via `Path.home()`) **> template**
  (`skills/agentgraph-run-graph/templates/{graph-name}/graph.py` — `feature-kickoff`,
  `standard-task`). **None of these is ever copied anywhere** — each loads in place via
  `importlib`, exactly where it already lives.
- Optionally, "start fresh" / "new run" — just call `agentgraph start` again; it always creates a
  new `run_id`, never silently reuses an old one.
- Optionally, "redrive `{graph-name}`" to resume a **halted** run after fixing whatever caused it —
  `agentgraph redrive --run {run_path}`. A halted run is never auto-resumed without this explicit
  ask.
- Optionally, "resume `{run_path}`" for a run **paused at an `interrupt()`** (a deliberate
  checkpoint a graph author put in — distinct from a halt; see below) — `agentgraph resume --run
  {run_path} [--resume-value {value}]`.

## Starting a run

Derive a short kebab-case slug for what this run's input is actually about (e.g. the feature/idea
slug, not the graph name — that's already implied by `--graph`), so a `runs/` directory stays
identifiable once several runs accumulate. Then:

```
agentgraph start --graph {graph-name} [--slug {slug}] [--spec {path}] [--input-json {path}]
```

This one call drives the compiled graph through every node until it reaches `END`, halts, or pauses
at an `interrupt()` — whichever comes first. It prints exactly one JSON object; read it, don't
guess:

```json
{"run_path": "...", "run_id": "...", "halted": false, "halt_reason": null, "outcome": "success"}
```

## Interpreting the terminal result

- **`outcome` set, `halted: false`, no `interrupted`** — the run reached a real terminal (e.g.
  `success`, `blocked`, `manual_review` for `feature-kickoff`; `success`, `manual_flag` for
  `standard-task`). Report it to the user in plain language (what `outcome` means for this
  specific graph) and stop.
- **`halted: true`** — a node exhausted its `retry` budget (`halt_reason: "retries_exhausted"`), a
  sequential map had items stuck on unresolved `dependencies` (`halt_reason:
  "unmet_dependencies"` — a cycle; a permanently-blocked item, e.g. one whose dependency ended at
  a non-success terminal, does **not** halt the whole map on its own), or a gate halted for a
  human (`manual_requested`, `reject_attempts_exhausted`, or `unrecognized_result`). An
  unrecognized `Result:` line on a gate routes to manual **immediately** — there is no self-retry
  hop. Report the reason and, if useful, `agentgraph status --run {run_path}` to see the full
  state snapshot (including which node halted). Do **not** auto-redrive. Ask the user to fix the
  underlying cause (a broken environment, a genuinely-needed design decision, etc.), then call
  `agentgraph redrive --run {run_path}` — this re-attempts only the failing node fresh, using the
  checkpoint history to replay from exactly the point before it ran, never re-running anything
  upstream. A gate-manual halt also zeros nested `attempt_count` fields on redrive.
- **`interrupted: true`** — the graph paused at an explicit `interrupt()` call the graph's author
  deliberately placed (rare in `feature-kickoff`/`standard-task` today; real in
  `agentgraph_engine/examples/hello_graph/`, which is what proves the mechanism —
  `tests/test_checkpoint_resume.py`). `interrupt_value` carries whatever payload the node passed.
  Decide (or ask the user for) the resume value the paused node needs, then call `agentgraph
  resume --run {run_path} --resume-value {value}`. Nodes already completed before the pause are
  **not** re-executed.

## Halting

A failing/erroring Worker CLI dispatch is an ordinary technical failure, subject to the node's
own `retry` count, then `halt_reason: "retries_exhausted"`. Halt reasons:

- `retries_exhausted` — a node's headless-CLI Worker dispatch failed (non-zero exit, or the
  Worker didn't write its required output file) more times than its `retry` count allows. See
  `ENGINE-CLI.md`'s "Permission mode by model tier" section for how the dispatch's
  `--permission-mode` is chosen.
- `unmet_dependencies` — a sequential map (`feature-kickoff`'s `run_tasks`) had remaining items
  stuck on unresolved `dependencies` with nothing ready to progress (a cycle).
- `manual_requested` — a gate's `Result:` line started with `manual`.
- `reject_attempts_exhausted` — a gate's reject-loop budget on the retry target's `attempt_count`
  was already at the cap.
- `unrecognized_result` — a gate's `Result:` line matched none of accepted / rejected / manual.
  Routes to manual immediately; no self-retry hop.

Re-invoking `agentgraph start` on a graph with a halted run does **not** resume it — start always
creates a fresh run. Use `redrive` explicitly.

## Map items and cross-item dependencies

A map/fan-out node (e.g. `05_run_tasks` inside `feature-kickoff`) dispatches sequentially — no
concurrent dispatch — and honors each item's own `dependencies` (other item ids): an item doesn't
start until every listed dependency reached a success terminal; a permanently-blocked item (a
missing dependency id, or one that itself ended at a non-success terminal like `standard-task`'s
`manual_flag`) is left `blocked` rather than halting the whole map, so a later review node can
still see and report the gap. Each item's own artifacts land under
`{run_dir}/{map_node_name}/item-{n}/`, using the same `{node_name}/attempt-{n}/output.md`
convention as every other node — inspect them directly if you need to see what a specific item's
nested run actually did.
