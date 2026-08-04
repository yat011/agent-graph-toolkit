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

- A graph name (required). The graph must already exist at
  `agent_works/graphs/{graph-name}/graph.md`.
- Optionally, "start fresh" / "new run" (pass `--fresh` to `resolve-run`).
- Optionally, "redrive `{graph-name}`" to resume a *halted* run after fixing whatever caused it
  (pass `--redrive`). A halted run is never auto-resumed without this explicit ask.

## Loop

1. Call `node run-graph.js resolve-run --graph {graph-name} [--redrive] [--fresh]`.
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
     --detail "<what's missing>"`, report it to the user, and stop. Otherwise, make exactly one
     `Agent`-tool call with the given `agent`/`model`/`prompt`, and wait for it to finish.
   - Read `output.md` at `output_path`.
     - If the subagent crashed or `output.md` wasn't written, call `record-result --outcome
       technical_failure` (add `--item {item}` if the dispatch had one).
     - Otherwise call `record-result --outcome success` (`--item` likewise).
     - If `has_branches` was true, additionally judge — in plain language, from the full
       `output.md` — which `branches` condition (if any) matches, and call `record-branch --node
       {node_id} --match "<condition text>"`, or `--default`, or `--none` if nothing matches and
       there's no default. This branch-condition judgment is also yours alone; the script only
       applies the resulting transition.

## Halting

A run halts for exactly three reasons, recorded as `halt_reason`:

- `unresolved_branch` — a completed node's `branches` had no matching condition and no `default`.
- `retries_exhausted` — a node (or map item) failed technically more times than its `retry` allows.
- `capability_gap` — you judged the declared agent can't do what the node needs, or a dispatch was
  blocked/rejected by a permission prompt. Never resolved by retrying, substituting, or narrowing
  scope on your own judgment — only by the user fixing the graph, granting the permission, or
  explicitly directing a specific path forward.

Re-invoking this skill on a halted graph does **not** resume it automatically — ask for a redrive
(resets just the halted node and continues) or an explicit fresh start (abandons it for a new run).
