---
name: agentgraph-run-graph
description: Use when the user asks to run, execute, resume, or continue a previously-defined agent graph (a graph.py written by the agentgraph-define-graph skill, or one of the built-in feature-kickoff/standard-phase/standard-task templates) — starts/resumes/redrives it via the `agentgraph` CLI and reports its terminal state, without driving individual node dispatches yourself.
---

# agentgraph-run-graph

Executes a graph previously authored by the `agentgraph-define-graph` skill, or one of the two
built-in template graphs (`feature-kickoff`, `standard-phase`, `standard-task`). All node-by-node mechanics —
dependency ordering, retries, branch judgment, map fan-out, subgraph recursion, checkpointing —
are owned by the compiled LangGraph `StateGraph` itself, driven by the `agentgraph` CLI
(`agentgraph_engine/cli.py`; see `ENGINE-CLI.md` in this same directory for exact command syntax
— this file does not restate it).

Never monitor progress or read run files. Start `agentgraph` in the background and wait only for the subprocess/terminal completion callback; when that process has exited, report "The graph execution is done" and show the last 10 lines of that terminal's output (the process body, not the session metadata header).

Dispatch and branch judgment live in the graph's own Python code. A node's router function does
plain string-matching against a `Result:` line — never an LLM or human judgment call. You start
or resume a run, then interpret the **one** JSON object the CLI prints back: report done, ask
the user how to proceed on a halt, or resume a deliberate `interrupt()` pause.

## Inputs

- A graph name (required). Resolved via `agentgraph_engine.graph_loader.resolve_graph_path` in
  this order: **project** (`agent_works/graphs/{graph-name}/graph.py`) **> user**
  (`~/.agents/graphs/{graph-name}/graph.py`, via `Path.home()`) **> template**
  (`skills/agentgraph-run-graph/templates/{graph-name}/graph.py` — `feature-kickoff`,
  `standard-phase`, `standard-task`). **None of these is ever copied anywhere** — each loads in place via
  `importlib`, exactly where it already lives.
- Optionally, "start fresh" / "new run" — just call `agentgraph start` again; it always creates a
  new `run_id`, never silently reuses an old one.
- Optionally, "redrive `{graph-name}`" to continue a **paused** production run (gate reject
  budget, `Result: manual` / unrecognized, Worker death, nested-task interrupt) or a **halted**
  hello_graph sink, after the user fixes the cause — `agentgraph redrive --run {run_path}`. Never
  auto-redrive.
- Optionally, "resume `{run_path}`" for an author-placed `interrupt()` that expects a resume
  value (hello_graph's checkpoint gate) — `agentgraph resume --run {run_path} [--resume-value
  {value}]`. Production template pauses use `redrive`, not `resume`.

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

- **`outcome` set, `halted: false`, no `interrupted`** — the run reached a real terminal (`success`
  for the production templates). Report it to the user in plain language and stop. Dead-end
  outcomes (`blocked`, `manual_review`, `manual_flag`) are gone from the templates; those cases
  pause instead.
- **`interrupted: true`** — the graph paused at `interrupt()`. Production templates do this for
  reject-budget exhaustion, `Result: manual` / unrecognized, Worker CLI death, and nested-task
  failure. `interrupt_value` carries `reason`, `redrive_node`, and `reset_attempts`. Report the
  reason; do **not** auto-redrive. Ask the user to fix the cause, then `agentgraph redrive --run
  {run_path} [--message "..."]` (`Command(resume=...)` → pause node `Command(goto=redrive_node)`).
  Every pause resets writer+gate `attempt_count`. Gate `Result: manual` / unrecognized redrives
  **that gate** (pass `--message` to instruct the reviewer). Reject-budget exhaustion still
  redrives the code-writer. `retries_exhausted` redrives the failed node and also resets.
  hello_graph's checkpoint gate is the exception: it expects `agentgraph resume --run {run_path}
  --resume-value {value}`.
- **`halted: true` without `interrupted`** — hello_graph's technical sink (it still ENDs). Report
  the reason; `agentgraph redrive` uses time-travel for that leftover path.

## Halting and pausing

A failing/erroring Worker CLI dispatch is an ordinary technical failure, subject to the node's
own `retry` count, then `halt_reason: "retries_exhausted"` and a pause (production) or END
(hello_graph). Reasons:

- `retries_exhausted` — a node's headless-CLI Worker dispatch failed (non-zero exit, or the
  Worker didn't write its required output file) more times than its `retry` count allows. Redrive
  the same node; reset `attempt_count`.
- `unmet_dependencies` — a sequential map had remaining items stuck on unresolved `dependencies`
  with nothing ready to progress (a cycle).
- `manual_requested` — a gate's `Result:` line started with `manual` (or implement `stopped`).
  Redrive the **gate** (reviewer); implement `stopped` redrives implement. Reset attempts.
  Optional `--message` is injected into that node's Worker prompt.
- `reject_attempts_exhausted` — a gate's reject-loop budget was already at the cap. Redrive the
  code-writer; reset attempts.
- `unrecognized_result` — a gate's `Result:` line matched none of accepted / rejected / manual.
  Pauses immediately; no self-retry hop. Redrive the **gate**; reset attempts.

Re-invoking `agentgraph start` on a graph with a paused/halted run does **not** resume it — start
always creates a fresh run. Use `redrive` explicitly.

## Map items and cross-item dependencies

A map/fan-out (feature-kickoff's `pick_next_phase` / `run_one_phase`) dispatches sequentially — no
concurrent dispatch — and honors each item's own `dependencies`. An item doesn't start until every
listed dependency reached a success terminal. A missing dependency id is left `blocked` rather
than pausing the whole map. A **paused** nested standard-phase (reject budget, `Result: stopped`,
Worker death) interrupts immediately so later items do not keep running. Nested runs share the
parent checkpointer on a per-item `thread_id`. On-disk idempotency only skips items that already
wrote a `04_success` receipt. Each item's artifacts land under
`{run_dir}/05_run_phases/item-{n}/`. Review after implement is conditional on the phase `review`
field (`always` / `if_substantial` / `never`). After all phases, `additional_test` runs the
planner's unfiltered script (one `integration_fix` if it fails); a green suite dispatches the
`final-reviewer` agent. Reject from that agent pauses — no auto-fix.
