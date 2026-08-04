# Agent Graph Format — Spec

This document is the single source of truth for the "agent graph" file format used by the
`agentgraph-define-graph` and `agentgraph-run-graph` skills. Both skills reference this file instead of each
re-describing the format. It is not itself an invocable skill.

An agent graph turns a written plan into an executable graph of subagent-call "nodes". The
main agent is the runtime: it reads a Markdown graph spec (`graph.md`) and drives
execution itself via its own subagent-dispatch tool (e.g. Claude Code's `Agent` tool, or a
Cursor subagent). There is no external framework or execution engine.

## Node model

One primitive: a leaf node = one subagent call + prompt, output written to `output.md`. There
is no human-checkpoint node type — runs are fully autonomous, no interrupts. A node can instead
be a reference to another graph (`ref: {graph-name}`), recursing arbitrarily deep ("a node is
just a sub-graph"). When the target graph's name isn't known until runtime (e.g. it's produced by
an earlier node in this same graph), use `ref_from: {node-id}` instead of `ref` — see "Dynamic
subgraph convention" below.

Node types: `leaf`, `map`, `subgraph`.

## File layout

```
agent_works/graphs/{graph-name}/
  graph.md                                   # graph definition (see schema below)
  runs/
    {input-summary-slug}_{timestamp}/
      run-state.json                         # top-level run bookkeeping
      {seq}_{node-id}/
        attempt-1/
          output.md                          # present if this node is a leaf
          items.json                         # present if this node is a map SOURCE (any type, produces a list)
          run-state.json                     # present if this node is a sub-graph
          {seq}_{child-node-id}/attempt-1/... # recurses if sub-graph
        attempt-2/                            # any re-execution: retry, loop, or re-run

    # type: map nodes additionally nest one level per item inside their attempt:
      {seq}_{map-node-id}/
        attempt-1/                            # this map step's overall attempt
          item-1/
            attempt-1/
              output.md                        # or run-state.json if the template is itself a subgraph
          item-2/
            attempt-1/
              output.md
```

Item folders are **1-indexed** (`item-1`, `item-2`, ...), matching the 1-indexed `{seq}` node
prefixes — not a 0-indexed array position. Element `i` (0-based) of the source `items.json` array
lives in `item-{i+1}/`.

`run-state.json` tracks, per node: `status` (`pending` / `running` / `completed` / `halted` /
`bypassed`), `attempt`, and (if the node declares `branches`) `branch_decision`. A `type: map`
node's entry additionally carries an `items` map (`item-1`, `item-2`, ... → the same per-node
shape, recursively). A `type: subgraph` node's entry additionally carries its nested run's own
state under a `subgraph_state` key — **always a nested JSON object matching this same shape
recursively, never a string** — plus `resolved_ref` when the node used `ref_from`, for
auditability. `total_executions` and `halt_reason` are tracked once, at the top level of a run,
shared across every nesting level rather than tracked separately per subgraph.

Every graph lives flat under `agent_works/graphs/{graph-name}/` — there is no separate library
folder, since any graph might later be reused as a sub-graph of another.

## `graph.md` schema

### Node section block

Then one `## {seq}_{node-id}` section per node, each starting with a fenced ```yaml block:

```yaml
deps: [01_fetch_requirements]   # node ids that must complete first
type: leaf | map | subgraph
retry: 2                        # optional, default 0 — technical-failure retries only
agent: reviewer                 # optional, default general-purpose
model: haiku                    # optional — overrides the agent type's own default model
ref: schema-design              # for type: subgraph — a graph name fixed at authoring time
ref_from: 02_planner_step        # alternative to ref — node id whose output.md's `Graph:` line names the graph at runtime
map_over: 01_planner             # required if type: map — node id whose items.json to iterate
branches:                        # optional — controller-judged, evaluated in order
  - condition: "review found critical issues"
    next: 04_retry_with_context
  - condition: "review passed"
    next: 05_success
  default: 06_manual_flag         # optional
```

followed by the node's prompt as plain Markdown (for `map` nodes, this is the per-item template
prompt, using `{{item}}` / `{{item.field}}` placeholders — unless the map's per-item template is
itself a subgraph, see the `ref`/`ref_from` row and the "Map-of-subgraphs invocation context"
convention below, in which case this text is never dispatched as a prompt to any agent directly).

| Field | Required | Default | Meaning |
|---|---|---|---|
| `deps` | no | `[]` | Node ids that must complete before this node runs. |
| `type` | yes | — | One of `leaf`, `map`, `subgraph`. |
| `retry` | no | 0 | Number of retries on technical failure (crash / no output produced). Independent of branching. |
| `agent` | no | `general-purpose` | Subagent type to dispatch this node to (e.g. `reviewer`, `code-writer`, `Explore`). Not used (omit it) when `type: map` and `ref`/`ref_from` is also set — see below. |
| `model` | no | the `agent` type's own default | Overrides the model this node's subagent runs on (e.g. `haiku`), passed straight through as the subagent-dispatch tool's `model` param at dispatch. Use for mechanical/cheap nodes (e.g. a connectivity check) that don't need the agent type's default model. Not used on `type: map`/`subgraph` nodes whose `ref`/`ref_from` recurses into another graph — the model is set per-node inside the referenced graph.md instead. |
| `ref` | one of `ref`/`ref_from` required if `type: subgraph`; also usable on `type: map` | — | Name of another graph under `agent_works/graphs/` to recurse into, fixed when this graph.md was authored. On a `type: map` node (in place of `agent`), marks the per-item template as a nested subgraph invocation rather than a leaf agent call — see "Map-of-subgraphs invocation context" below. |
| `ref_from` | one of `ref`/`ref_from` required if `type: subgraph`; also usable on `type: map` | — | Node id (in this same graph) whose latest `output.md` supplies the target graph name at runtime via a `Graph:` line. Use when the target isn't known until execution (e.g. a planning node that authors a fresh graph per run). Usable on `type: map` the same way as `ref`. |
| `map_over` | required if `type: map` | — | Node id whose `items.json` to iterate over. |
| `branches` | no | — | List of `{condition, next}` entries, evaluated in declaration order, plus an optional `default`. |

A fenced ASCII diagram block at the top of the file renders the same graph (nodes + edges, branch
labels on conditional edges) as plain-text boxes/arrows, and must be regenerated whenever
nodes/edges change.

## Worked example

A small 3-node graph: a planner (map source), a per-task map node, and a review node with
branching.

`agent_works/graphs/example-graph/graph.md`:

````markdown
```
01_planner ──► 02_per_task_impl ──► 03_review
                     ^                 │
                     └──[critical issues]
                                       ├──[passed]────► 05_success
                                       └──[no match]──► 06_manual_flag
```

## 01_planner

```yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
```

Break the plan into a list of independent tasks. Write the list to `items.json` in this node's
attempt folder as a JSON array of objects with a `title` and `description` field. End
`output.md` with a one-line summary of how many tasks were produced.

## 02_per_task_impl

```yaml
deps: [01_planner]
type: map
map_over: 01_planner
retry: 1
agent: code-writer
```

Implement the task: {{item.title}}. Details: {{item.description}}. Write your result summary
to `output.md`.

## 03_review

```yaml
deps: [02_per_task_impl]
type: leaf
retry: 0
agent: reviewer
branches:
  - condition: "review found critical issues"
    next: 02_per_task_impl
  - condition: "review passed"
    next: 05_success
  default: 06_manual_flag
```

Read all implementation outputs matching
`.../02_per_task_impl/attempt-1/item-*/attempt-*/output.md`. Review them for correctness. End
`output.md` with a single-line `Result: <short phrase>` conclusion.
````

### Resulting `runs/` folder at various points during execution

After `01_planner` completes (produces 2 tasks):

```
agent_works/graphs/example-graph/runs/example-plan_20260801T120000/
  run-state.json
  01_planner/
    attempt-1/
      output.md
      items.json
```

After `02_per_task_impl` completes both items (a `map` node — no separate `items.json` of its
own, it fans out over `01_planner`'s `items.json`):

```
agent_works/graphs/example-graph/runs/example-plan_20260801T120000/
  run-state.json
  01_planner/
    attempt-1/
      output.md
      items.json
  02_per_task_impl/
    attempt-1/
      item-1/
        attempt-1/
          output.md
      item-2/
        attempt-1/
          output.md
```

After `03_review` completes and its `Result:` line matches the `"review found critical issues"`
branch, routing back to `02_per_task_impl` (a new attempt, not a new map-node attempt-folder
name — item retries bump only the item's own `attempt-N`; here the whole map node re-runs so it
gets its own `attempt-2`):

```
agent_works/graphs/example-graph/runs/example-plan_20260801T120000/
  run-state.json
  01_planner/
    attempt-1/
      output.md
      items.json
  02_per_task_impl/
    attempt-1/
      item-1/
        attempt-1/
          output.md
      item-2/
        attempt-1/
          output.md
    attempt-2/
      item-1/
        attempt-1/
          output.md
      item-2/
        attempt-1/
          output.md
  03_review/
    attempt-1/
      output.md
```

If `03_review` instead ran again after the second `02_per_task_impl` attempt and its `Result:`
line matched `"review passed"`, execution proceeds to `05_success` per the `branches` table. This
toy example stops short of writing out `05_success` and `06_manual_flag` as full node sections —
in a complete graph.md they would be ordinary `## {seq}_{node-id}` sections like any other node.

## Conventions

These conventions are normative and apply verbatim to both `agentgraph-define-graph` and `agentgraph-run-graph`.

- **Aggregation convention, no new node type.** A node that `deps` on a map node reads every
  `item-*/attempt-{latest}/output.md` under the map node's current attempt folder as its input.
  `agentgraph-define-graph` is responsible for wiring this into the downstream node's prompt explicitly
  (e.g. "read all files matching `.../02_per_task_impl/attempt-1/item-*/attempt-*/output.md`")
  — there is no separate collector/reduce node type.

- **Loops must self-limit via their own branch conditions.** There is no global cap on total node
  executions and no human in the loop to interrupt a runaway graph, so any node whose `branches`
  can route back to an earlier node (a loop-back) must make the loop bounded through its own
  condition text — e.g. `"Reject, attempted 3 times"` routing to a manual-review terminal instead
  of looping again. `agentgraph-define-graph` must never author an unbounded loop-back condition.

- **Result-line convention for branching.** Every node whose section in `graph.md` declares
  `branches` must have its prompt instruct the subagent to end `output.md` with a single-line
  `Result: <short phrase>` conclusion. The controller still judges the branch from the full
  output, but the convention reduces ambiguous/hedging matches. Whichever branch is taken (or
  `default`, or none) is logged in `run-state.json` for that node with the matched condition
  text, so misroutes are auditable after the fact.

- **Dynamic subgraph convention (`ref_from`).** Exactly one of `ref` / `ref_from` must be set on
  every `type: subgraph` node. `ref_from: {node-id}` names a node in this *same* graph.md (never a
  node buried inside another subgraph's own nested run) whose latest `output.md` is read at
  execution time for a trailing single-line `Graph: <graph-name>` conclusion — the resolved
  `agent_works/graphs/{graph-name}/graph.md` is then recursed into exactly as a static `ref`
  would be. This lets a node earlier in the graph (e.g. one that authors a fresh graph per run,
  such as a planning node) decide, at runtime, which graph the later subgraph node actually
  executes. If the named node's latest `output.md` has no `Graph:` line when the `ref_from` node
  is reached, `agentgraph-run-graph` treats it as a technical failure (subject to `retry`, then
  `halt_reason: retries_exhausted`) rather than guessing — so `agentgraph-define-graph` should route around a
  `ref_from` node via `branches` whenever the graph-producing node might legitimately not produce
  one (e.g. it was rejected/blocked instead of approved), rather than let it be reached in that
  state.

- **Map-of-subgraphs invocation context.** A `type: map` node may declare `ref` (or `ref_from`)
  instead of `agent`, marking its per-item template as a nested subgraph invocation (one full
  recursion of the referenced graph per item) rather than a leaf agent call. Because the recursed
  graph's own node prompts are dispatched exactly as authored in its `graph.md`, the map node's
  per-item template body is **not** itself dispatched as a prompt to any agent in this case — so
  the usual "the subagent reads this text" framing doesn't apply. Instead, after `{{item}}` /
  `{{item.field}}` substitution, the template body is written to
  `item-{i}/attempt-1/context.md` before recursion begins, and `agentgraph-run-graph` appends its content to
  the prompt of every node dispatched anywhere in that nested run (not only its entry points) —
  exactly as it would treat text "given directly as part of the agentgraph-run-graph request" for a
  top-level invocation, which is likewise available to whichever node's own prompt actually asks
  for it, not scoped to entry points. Which specific node needs the context depends entirely on
  the target graph's own structure (e.g. a graph that gates on a precondition before its real
  entry-point node, the way a per-task subgraph might check an external dependency's availability
  before implementing). `agentgraph-define-graph` must phrase a map-of-subgraphs template's body as the
  context itself (what the nested run needs to know), not as an instruction telling a subagent to
  "pass this through" — no subagent receives that instruction directly.

- **Retry idempotency note.** `retry` re-runs a node's subagent from scratch after technical
  failure. If a node's prompt performs external side effects (file writes, tool calls) that
  aren't safe to repeat, `agentgraph-define-graph` should phrase that node's prompt to check existing state
  before acting (e.g. "if X already exists, treat as done") rather than assuming a clean slate —
  `agentgraph-run-graph` itself does not attempt any de-duplication.

- **Checkpoint-every-node.** `agentgraph-run-graph` writes/updates `run-state.json` immediately after each
  node execution completes (success, failure, or halt) — not only at the end of a run — so
  resume is always accurate, including partially-completed map fan-outs and nested sub-graph
  runs.

## Other layout rules

- Every node execution (leaf or sub-graph, any attempt) gets its own `attempt-N/` folder.
- A sub-graph invoked as a node always nests its execution under wherever it was called from —
  its own `runs/` folder is only populated when that graph is invoked directly as a top-level
  run.
- A `type: map` node's `attempt-N/` holds one `item-{index}/` folder per element in the source's
  `items.json`, each of which is itself a full node-execution tree (`attempt-N/output.md`, or a
  recursive sub-graph tree if the map template is `type: subgraph`). An item retrying does not
  bump the map node's own `attempt-N` — only that item's `attempt-N` increments.
