---
name: agentgraph-define-graph
description: Use when the user wants to turn a written plan into an executable agent graph for review — e.g. "define a graph for this plan", "turn this plan into a graph", "break this plan into nodes". Reads a plan file, breaks it into nodes, writes agent_works/graphs/{graph-name}/graph.md, and iterates with the user until they confirm it. Does not execute the graph — that's a separate skill.
---

# agentgraph-define-graph

Turns a written plan (`agent_works/plans/*.md`) into an `agent_works/graphs/{graph-name}/graph.md`
file per the shared spec, and iterates with the user on the design until they confirm it.

**This skill only writes `graph.md`. It never calls `agentgraph-run-graph`, never dispatches an `Agent` tool
call, and never executes any node.** Defining a graph and running a graph are separate skills —
running is out of scope here even if the user seems eager to proceed; hand off to the `agentgraph-run-graph`
skill (or ask them to invoke it) once they confirm.

Before doing anything else, read `GRAPH-SPEC.md` in this same skill directory
(`.claude/skills/agentgraph-define-graph/GRAPH-SPEC.md`) in full. Token/index/memory
conventions and the portable tool list live in
`../agentgraph-run-graph/TOKEN-SPEED.md` and `../agentgraph-run-graph/DEPENDENCIES.md`. It is the single source of truth for the
file layout and the `graph.md` schema (node types, `deps`, `retry`, `agent`, `ref`, `map_over`,
`receipt`, `branches`, the ASCII diagram block, the worked example). Do not re-derive or duplicate that format from
memory — follow it exactly as written there.

## Steps

### 1. Resolve the input plan

- If the user gave a plan path, use it.
- Otherwise, default to the most recently modified file under `agent_works/plans/`.
- Read the plan in full before proceeding.
- If the plan is too vague or thin to decompose into distinct nodes (e.g. a one-line note with
  no real steps), don't guess — tell the user what's missing and ask them to flesh out the plan
  (or point you at a different one) before continuing. Do not invent nodes not grounded in the
  plan's content.

### 2. Choose the graph name

- Derive `{graph-name}` from the plan file's name (kebab-case, drop the extension and any
  redundant "plan" wording if it's already implied by context), e.g.
  `agent_works/plans/agent-graph-skills.md` → `agent-graph-skills`.
- Check whether `agent_works/graphs/{graph-name}/graph.md` already exists:
  - If it exists and the user is asking to redefine/update it, treat this as an edit to the
    existing graph (skip to step 5's loop directly).
  - If it exists and this looks like a fresh definition request for the same plan, ask the user
    whether to overwrite or pick a different name — don't silently clobber prior work.

### 3. Break the plan into nodes

Read the plan's structure (sections, task lists, "Tasks" headings, etc.) and design nodes that
cover the work end to end. For each unit of work, decide:

- **Node id and sequence** — `{seq}_{node-id}` (e.g. `01_planner`, `02_per_task_impl`), ordered
  so `deps` only ever point backward.
- **Node type** (per GRAPH-SPEC.md's node model):
  - `leaf` — a single subagent call producing one `output.md`. Use this for any discrete, one-shot
    piece of work (implement a thing, review a thing, write a doc). A leaf may set `receipt: true`
    (engine synthesizes `output.md` and marks the node completed inside `next()` — no dispatch).
    See GRAPH-SPEC.md's `receipt` field.
  - `map` — use when the plan describes doing the *same kind* of work over a list of items whose
    count/identity isn't known until an earlier node produces them (e.g. "implement each task
    from the plan", "review each file in the list"). The map node must `map_over` a node that
    writes `items.json`; write that source node's prompt to explicitly produce `items.json` as a
    JSON array (never leave the list to be parsed out of prose).
  - `subgraph` (`ref: {other-graph-name}`) — use when a chunk of the plan is itself substantial
    enough to deserve its own graph (reusable, independently definable, or simply too large to
    inline as one node). Only reference a graph that already exists or that you're defining as
    part of this same session — don't invent a dangling `ref`. If the target graph's name isn't
    knowable until runtime (e.g. a node earlier in this graph authors a fresh graph per run), use
    `ref_from: {node-id}` instead of `ref` (see GRAPH-SPEC.md's dynamic subgraph convention) — the
    node it points at must be in this same graph, and its prompt must be written to end
    `output.md` with a `Graph: <graph-name>` line whenever it produces a runnable graph. Route
    around a `ref_from` node via `branches` for any outcome where the graph-producing node
    legitimately won't have produced one (rejected/blocked), rather than let it be reached bare.
- **`deps`** — which earlier nodes' outputs this node needs before it can run.
- **`agent`** — leave at the default (`general-purpose`) unless the plan or the nature of the work
  clearly calls for a specialized subagent (e.g. a review-only node that must not write files
  should get a read-only-oriented agent type).
- **`retry`** — set `> 0` for nodes whose failure is more likely to be a transient/technical
  glitch than a substantive problem; leave at 0 (default) otherwise. If a node's prompt causes
  side effects that aren't safe to blindly repeat, phrase the prompt so it checks existing state
  before acting (per GRAPH-SPEC.md's retry-idempotency convention), rather than skipping retry.
- **`branches`** — add where the plan implies a decision point (e.g. "if review finds issues, fix
  and re-review; if it passes, proceed"). Write plain-language `condition` strings in the order
  they should be checked, each with a `next` node id, and an optional `default`. For every node
  that declares `branches`, its prompt body must instruct the subagent to end `output.md` with a
  single `Result: <short phrase>` line, per GRAPH-SPEC.md's result-line convention.
- **Aggregation** — for any node that depends on a `map` node's fanned-out results, write that
  explicitly into its prompt body (e.g. "read all files matching
  `.../02_per_task_impl/attempt-1/item-*/attempt-*/output.md`") — GRAPH-SPEC.md has no separate
  collector node type, so the reading instruction has to live in the downstream node's own prompt.

Keep the node prompts self-contained: each node's Markdown body is the entire prompt a subagent
will receive, so it must state what to read, what to do, and what to write, without assuming any
context beyond what's in `deps`/`map_over`/`ref` and its own body.

### 4. Write graph.md

Write `agent_works/graphs/{graph-name}/graph.md` following the schema in GRAPH-SPEC.md exactly:

- Set `model:` on a node only when it's a mechanical/cheap task (e.g. a connectivity check) that
  doesn't need its `agent` type's own default model — otherwise omit it and let the agent's default
  apply. Make sure any loop-back `branches` condition is self-bounding (e.g. an attempt count), per
  GRAPH-SPEC.md's "Loops must self-limit" convention — there is no global execution cap to fall
  back on.
- A fenced ASCII diagram block immediately after, rendering every node and edge (including branch
  labels on conditional edges) as plain-text boxes/arrows, matching GRAPH-SPEC.md's worked
  example format.
- One `## {seq}_{node-id}` section per node, each with its ```yaml metadata block followed by the
  node's prompt as plain Markdown body.

The ASCII diagram must always be kept in sync with the node sections — if you add, remove, or
rewire a node, regenerate the diagram in the same edit.

### 5. Confirm/redefine loop with the user

- After writing (or updating) `graph.md`, show the user the rendered ASCII diagram (reproduce
  the fenced block in your response) along with a short plain-language summary of the nodes and
  what each does.
- Ask the user to confirm the graph or describe changes.
- If they request changes, edit `graph.md` (nodes, deps, branches, prompts, and/or the ASCII
  diagram together, keeping them in sync), then show the updated diagram and summary again.
- Repeat until the user explicitly confirms. Do not proceed to running the graph yourself at any
  point — once confirmed, tell the user the graph is ready and that running it is a separate step
  (the `agentgraph-run-graph` skill), and stop.
