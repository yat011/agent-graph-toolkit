---
name: agentgraph-run-graph
description: Use when the user asks to run, execute, resume, or continue a previously-defined agent graph (a graph.md file produced by the agentgraph-define-graph skill) under agent_works/graphs/{graph-name}/ — walks the graph's nodes in dependency order, dispatching each as a subagent call, handling map/subgraph nodes, branching, retries, and resumable run-state.
---

# agentgraph-run-graph

Executes a graph previously authored by the `agentgraph-define-graph` skill. All the mechanical
bookkeeping — dependency ordering, resuming, retries, bypass/loop-back rules, map fan-out,
subgraph recursion, `run-state.json` — is owned by `run-graph.js`, a dependency-free Node CLI
shipped alongside this file. You (the main agent) are still the runtime for the two things a
script cannot judge: actually making each subagent-dispatch call, and reading its output to judge
which outcome occurred. See `CLI-CONTRACT.md` (or `node run-graph.js --help`) for exact command
syntax — this file does not restate it.

For the `graph.md` schema, node types, and `runs/` folder layout, see
`../agentgraph-define-graph/GRAPH-SPEC.md`.

## Inputs

- A graph name (required). If no local `graph.md` exists yet at
  `agent_works/graphs/{graph-name}/graph.md` and the name matches one of this skill's own
  `templates/{graph-name}/` (e.g. `feature-kickoff`, `standard-task`), it is auto-copied in on
  first use — including for nested `subgraph`/`map` lookups reached mid-run, not just the
  top-level graph — so no manual setup step is required for those. `next`'s response reports any
  auto-copy via `copied_templates` (see `CLI-CONTRACT.md`). A graph name that matches neither an
  existing local `graph.md` nor a template still fails with the usual "graph.md not found" error.
- Optionally, "start fresh" / "new run" (pass `--fresh` to `resolve-run`).
- Optionally, "redrive `{graph-name}`" to resume a *halted* run after fixing whatever caused it
  (pass `--redrive`). A halted run is never auto-resumed without this explicit ask.

## Loop

1. On a fresh run (not a resume/redrive), derive a short kebab-case slug for what this run's input
   is actually about (e.g. the feature/idea slug, not the graph name — that's already implied by
   `--graph`) and pass it as `--slug`, so the run folder stays identifiable when a `runs/`
   directory accumulates several runs over time. Skip this on `--redrive` (irrelevant — an existing
   run's folder is reused) and when the run has no natural single-subject slug (e.g. a
   `multi-feature-pipeline`-style batch run with no one topic).
   Call `node run-graph.js resolve-run --graph {graph-name} [--redrive] [--fresh] [--slug {slug}]`.
   - `{status:"blocked", reason:"halted_run_exists", ...}` — report the halt (node, reason) and
     stop; tell the user to ask for a redrive (once fixed) or an explicit fresh start.
   - `{status:"blocked", reason:"nothing_to_redrive"}` — report there's nothing halted for this
     graph and stop.
   - `{status:"ready", run_path, ...}` — proceed to the loop below with this `run_path`.
2. Repeat:
   - Call `node run-graph.js next --run {run_path}`.
   - `{status:"complete"}` — report success and stop.
   - `{status:"halted", halt_reason}` — report the halt and stop.
   - `{status:"needs_branch", node_id}` — a prior session already recorded this node's result but
     never recorded its branch judgment (e.g. interrupted mid-loop). Re-read that node's
     `output.md` and call `record-branch` for it, then call `next` again.
   - `{status:"dispatch", node_id, agent, model, prompt, output_path, has_branches, ...}` —
     **before dispatching**, verify the declared `agent` (its tool list / permissions) can
     actually do what `prompt` needs. This capability-gap judgment is yours alone, never the
     script's: if there's a gap, do not substitute, narrow scope, or retry around it — call
     `node run-graph.js record-halt --run {run_path} --node {node_id} --reason capability_gap
     --detail "<what's missing>"`, report it to the user, and stop. If `dispatch.model` is
     null/omitted and the prompt, invocation context, or the matching `itemsSource` item has
     `kind: mechanical`, pass the cheapest model (`haiku`, or whatever this host already uses
     for bookkeeping) as the subagent `model`, overriding the null. Do not invent an engine
     field for this. Then make exactly one `Agent`-tool call with the given
     `agent`/`model`/`prompt`, and wait for it to finish.
   - Read `output.md` at `output_path`.
     - If the subagent crashed or `output.md` wasn't written, call `record-result --outcome
       technical_failure` (add `--item {item}` if the dispatch had one).
     - Otherwise call `record-result --outcome success` (`--item` likewise).
     - If `has_branches` was true, match the `Result:` line first (do not open the rest of
       `output.md` unless that line is missing or ambiguous). Judge which `branches` condition
       (if any) matches, and call `record-branch --node {node_id} --match "<condition text>"`,
       or `--default`, or `--none` if nothing matches and there's no default. This
       branch-condition judgment is also yours alone; the script only applies the resulting
       transition. Keep `record-branch` as today.

## Halting

A run halts for exactly four reasons, recorded as `halt_reason`:

- `unresolved_branch` — a completed node's `branches` had no matching condition and no `default`.
- `retries_exhausted` — a node (or map item) failed technically more times than its `retry` allows.
- `capability_gap` — you judged the declared agent can't do what the node needs, or a dispatch was
  blocked/rejected by a permission prompt. Never resolved by retrying, substituting, or narrowing
  scope on your own judgment — only by the user fixing the graph, granting the permission, or
  explicitly directing a specific path forward.
- `unmet_dependencies` — a map had remaining items still waiting on unfinished `dependencies`
  (typically a cycle) and nothing was in progress to unblock them. Permanently blocked items
  (dep hit `05_manual_flag`, missing id) do **not** halt — the map completes so final review
  can flag the missing `04_success`.

Re-invoking this skill on a halted graph does **not** resume it automatically — ask for a redrive
(resets just the halted node and continues) or an explicit fresh start (abandons it for a new run).

## Map items and cross-item dependencies

- **A map item can reach a "needs help" terminal that isn't a halt** — e.g. a nested
  `standard-task` run's `02_implement_requirements` routes to `05_manual_flag`. That is a
  recorded terminal, not a `halted` run — `next()` keeps moving later items forward. There is
  no CLI primitive to redrive one already-terminal map item back onto `04_success`. Record the
  resolution in `agent_works/manual_actions/` (or the project's equivalent).
- **The engine honors `itemsSource[].dependencies`.** `dispatchMap` will not start an item until
  every listed id's corresponding map item is `completed` **and** reached a success terminal
  (`04_success` for a nested `standard-task`; the item itself for a leaf map). If the next
  array-order item is blocked, `next()` skips to the next *ready* item. In-progress items are
  returned first — it never dispatches a blocked item. When every remaining item is permanently
  blocked (dep hit `05_manual_flag`, missing id, finished without `04_success`), those items
  stay pending and the map completes so `06_final_review` can flag the missing `04_success`.
  A cycle (nothing ready or in-progress, some still waiting on unfinished deps) returns
  `halted` / `unmet_dependencies`. Independent ready items may still be dispatched in parallel
  via the host's parallel subagent API if you choose to fan out beyond one `next()` at a time.
  After a nested `05_manual_flag`, blockers live in that node's `output.md` and
  `agent_works/manual_actions/`. Do **not** create `agent_works/memory/` or `open-questions.md`.

Implementers run **scoped** tests; the unfiltered suite is the final-review node (or one
`full_suite: true` item, not both). Judge branches from the `Result:` line first. Do not paste
prior `output.md` bodies into the next prompt — pass a path. `codebase-memory` is **required**;
if its MCP tools are missing, the planner / `04_load_tasks` / implementer stop the graph.
