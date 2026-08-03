---
name: run-graph
description: Use when the user asks to run, execute, resume, or continue a previously-defined agent graph (a graph.md file produced by the define-graph skill) under agent_works/graphs/{graph-name}/ — walks the graph's nodes in dependency order, dispatching each as a subagent call, handling map/subgraph nodes, branching, retries, and resumable run-state.
---

# run-graph

Executes a graph previously authored by the `define-graph` skill. You (the main agent) are the
runtime — there is no external execution engine. You read `graph.md`, dispatch each node as a
subagent call via this session's subagent-dispatch tool (e.g. Claude Code's `Agent` tool, or a
Cursor subagent), judge branch outcomes yourself, and persist progress after every node so a run
can always be resumed accurately.

For the exact `graph.md` schema, node types, and `runs/` folder layout, see
`../define-graph/GRAPH-SPEC.md` — this skill does not restate that format, only how to execute it.

## Inputs

- A graph name (required). The graph must already exist at
  `agent_works/graphs/{graph-name}/graph.md`.
- Optionally, the user may say "start fresh" / "new run" — otherwise always prefer resuming an
  incomplete (non-halted) run if one exists.
- Optionally, the user may say "redrive" (e.g. "redrive `{graph-name}`") to resume a *halted* run
  after fixing whatever caused it — see step 2. A halted run is never auto-resumed without this
  explicit ask, even on a bare re-invocation.

## Step-by-step algorithm

### 1. Load the graph

- Read `agent_works/graphs/{graph-name}/graph.md`.
- Parse every `## {seq}_{node-id}` section: its ```yaml metadata block (`deps`, `type`, `retry`,
  `agent`, `model`, `ref`, `map_over`, `branches`) and its Markdown body (the prompt / per-item
  template).
- Compute a valid dependency order (topological sort over `deps`). Ties broken by `seq` prefix.

### 2. Find or start a run

- List `agent_works/graphs/{graph-name}/runs/*/run-state.json`.

- **If the user explicitly asked to redrive this graph:** find the most recent run whose `status`
  is `halted`. If none exists, report that there's nothing halted for this graph to redrive and
  stop — do not start a new run instead. Otherwise:
  - Locate the exact node marked `status: halted` in its `run-state.json` entry. It may be nested
    arbitrarily deep inside a `subgraph_state` (a halt propagates up through every enclosing
    `subgraph_state` per step 6) — recurse through nested `subgraph_state`/`items` entries until
    you find the one node actually carrying `status: halted`; there is always exactly one, since a
    halt stops the entire run immediately.
  - Clear `halt_reason` to `null` and set `status: running` at every level from the top-level run
    down to (and including) the level directly containing the halted node, undoing the halt's
    propagation at each level.
  - Reset that node's own entry to `status: pending`. Do **not** reset its attempt counter — its
    next dispatch is a fresh `attempt-{N+1}` (N = its last recorded attempt), keeping the full,
    continuously-numbered attempt history on disk. If it was also marked `bypassed` by some
    earlier branch decision, clear that too.
  - Proceed to step 3's normal walk. When this specific node is dispatched, append a **redrive
    notice** to its prompt (in addition to the node's usual body): "This is a redrive — a human has
    already addressed the cause of the previous halt at this node. Before doing any new work, check
    current on-disk/git state for artifacts left by the prior failed attempt(s) (uncommitted edits,
    partial output, existing branches) and account for them rather than assuming a clean slate (see
    GRAPH-SPEC.md's Retry idempotency note)." Redrive performs no re-verification of the fix itself
    — it trusts the human and simply re-dispatches.

- **Otherwise, if the most recent run's `status` is `halted` and the user did not explicitly ask
  to start fresh:** do not start a new run — a halted run is never silently abandoned in favor of a
  duplicate just because it was re-invoked plainly. Report the halt exactly as it was first
  reported (which node, `halt_reason`) and stop, telling the user their two options: ask to redrive
  it (once they've fixed the cause), or explicitly ask to start fresh (which *does* fall through to
  the "start a new run" bullet below, abandoning the halted one).

- **Otherwise, if any run's `run-state.json` shows `status: running`** (i.e. it was interrupted
  mid-execution, not halted) and the user did not ask to start fresh, resume the most recent such
  run: load its `run-state.json` and skip every node already marked completed there. For a node
  whose status is `running` (i.e. it was interrupted mid-execution) and whose `type` is `map`,
  resume at **item** granularity, not whole-node granularity: check `run-state.json`'s per-item
  record for that node's current attempt and skip only the items already marked completed,
  continuing from the first unfinished index (see step 5). For an interrupted `subgraph` node,
  resume by re-entering step 2 at the nested run's own `run-state.json` (see step 6) rather than
  restarting that subgraph from scratch.

- **Otherwise start a new run:** create
  `agent_works/graphs/{graph-name}/runs/{input-summary-slug}_{timestamp}/`, where
  `{input-summary-slug}` is a short slug summarizing what triggered this run (e.g. derived from
  the graph name or the user's request) and `{timestamp}` is `YYYYMMDDTHHMMSS`. Initialize
  `run-state.json` with an empty node list, `total_executions: 0`, and `status: running`.

### 3. Walk nodes in dependency order

For each node in dependency order that is not yet completed in `run-state.json`:

1. Confirm all of its `deps` are completed (per `run-state.json`); if not, skip it for now (it
   will be reached once its deps complete, or a branch may bypass it entirely — see step 3.5
   below). If the node is marked `status: bypassed` in `run-state.json`, skip it permanently in
   this normal dependency-order walk — never *proactively* dispatch a bypassed node this way, even
   once its `deps` are satisfied. This does not prevent a later branch decision from explicitly
   naming this node as its `next` target; see step 3.5's bypassed-target override, which is a
   separate path into dispatching it.
2. **Pre-dispatch capability check.** Before dispatching, verify the node's declared `agent`
   (or the default `general-purpose`) can actually do what the task needs — do this check now,
   not by discovering the gap mid-dispatch:
   - Read the node's Markdown body for concrete requirements it implies: specific MCP tools it
     names, file operations, external systems, destructive/high-privilege actions, etc.
   - Check the target agent type's actual tool list (its `.claude/agents/{agent}.md` or
     `.cursor/agents/{agent}.md` frontmatter `tools:` line) against those requirements. A tool the
     task clearly needs but the agent type doesn't have — and that has no skill-wrapped equivalent
     either — is a gap, not something to paper over.
   - If the task will hit an action that needs a permission this session doesn't already have
     (destructive git, force-push, an external API call, etc.), don't assume it'll go through —
     treat it the same as a missing tool.
   - **If a gap is found, do not work around it.** Do not silently substitute a different agent
     type, narrow the task's scope, or proceed hoping it resolves itself. Halt the run at this
     node with `halt_reason: capability_gap`, setting this node's own entry to `status: halted` in
     `run-state.json` (create the entry if it doesn't exist yet — this halt happens before the
     node's first dispatch, so it may have no prior entry), write `run-state.json`, and report to
     the user exactly what's missing (which tool/permission, which node) so they can fix the
     graph's `agent` declaration, grant the permission, or tell you explicitly how to proceed. Only
     dispatch once the user has resolved it or explicitly approved a specific path forward —
     never make that call unilaterally, even when the substitution seems obviously safe.
   - The same rule applies if the dispatch itself is blocked or rejected by a permission prompt
     the user denies at dispatch time: that is **not** a technical failure eligible for step 8's
     retry, and not a signal to try a different agent or a reduced-scope approach. Halt with
     `halt_reason: capability_gap`, set this node's `status: halted` the same way, write
     `run-state.json`, and report it — do not retry, substitute, or route around it on your own
     judgment.
3. Dispatch the node according to its `type` (see sections below).
4. Immediately after the dispatch attempt finishes (success, technical failure, or a halt
   decision), update and write `run-state.json` for this node — never batch updates across
   multiple nodes. See "run-state.json bookkeeping" below.
5. If the node declares `branches`, evaluate them now (step 7) to determine what runs next.
   **The chosen `next` node is dispatched immediately, overriding normal dependency-order walk
   and overriding any prior `completed` status it may have in `run-state.json`** — a branch
   targeting an already-completed node (a loop-back, e.g. a review sending work back to an
   earlier implementation node) forces a new `attempt-{N+1}/` for that node, not a skip. After
   the branch target finishes, resume the normal dependency-order walk from wherever it left off
   (the branch target's own dependents, if any, then back to any nodes still pending in the
   original order). If it has no `branches`, continue to its dependents as normal.

   **Bypassing un-chosen branch targets.** Every `next` value across this node's `branches` list
   (and its `default`, if any) other than the one actually chosen this time must be marked
   `status: bypassed` in `run-state.json` — *unless* that node has already independently reached
   `completed` via some other path (e.g. it's the loop-back target itself, already completed from
   an earlier pass). A node marked `bypassed` is excluded from the normal dependency-order walk
   permanently (step 3.1), even once its `deps` become satisfied. This matters whenever two
   mutually-exclusive branch targets happen to share the same `deps` (e.g. a "success" node and a
   "blocked" node both depending on the same decision node) — without this, the walk would
   eventually dispatch the un-chosen sibling too, once its (now-satisfied) `deps` make it
   eligible, producing contradictory results for what was meant to be an either/or outcome.

   **Bypassed-target override.** A node marked `bypassed` by one branch decision is not
   permanently unreachable — it only means the normal dependency-order walk (step 3.1) will never
   pick it up on its own. If a *different, later* branch decision elsewhere in the run explicitly
   names that same node as its `next` (e.g. two unrelated decision points that both may need to
   route to the same shared "needs manual review" terminal), dispatch it exactly as step 3.5
   already does for an already-completed target: force a fresh `attempt-{N+1}/` and update its
   `run-state.json` status away from `bypassed` to `running`/`completed` for this new attempt. This
   matters whenever a single terminal node is a legitimate `next` target for more than one
   upstream decision node in the same graph — `define-graph` should still make sure such a shared
   terminal's own prompt can tell which upstream path actually triggered it (e.g. by checking which
   of several possible prior outputs exist), since the two routes may carry different context.
6. If the node run halted (unresolved branch, exhausted retries, or capability gap), stop the
   entire run: set `status: halted` with the appropriate `halt_reason`, write `run-state.json`,
   and report the halt to the user. Do not proceed to remaining nodes.

When every node has been marked completed and no halt occurred, set `status: completed` in
`run-state.json` and report success.

### 4. Dispatching a leaf node

- Increment `total_executions`; determine this attempt's folder:
  `{seq}_{node-id}/attempt-{N}/` (N = 1 for the first execution, N+1 for each retry).
- Dispatch a subagent in the foreground (blocking) with the node's `agent` type (default
  `general-purpose`), its `model` override if the node declares one (passed as the dispatch tool's
  own `model` param — omit the param entirely when the node has no `model:` field, letting the
  agent type's own default apply), and a prompt built from the node's Markdown body. Append explicit
  instructions to the prompt telling the subagent to write its full output to
  `{run-folder}/{seq}_{node-id}/attempt-{N}/output.md`, and — if this node declares `branches` —
  to end that file with a single-line `Result: <short phrase>` conclusion.
- Also append a readiness check instruction, covering both input and state: before doing any real
  work, the subagent must independently verify (a) that any input files the prompt references
  (e.g. prior nodes' `output.md` / `items.json` paths) actually exist and are non-empty, and (b)
  by reading `{run-folder}/run-state.json` itself, that this node's declared `deps` are actually
  recorded there as `completed`. This is a safety net independent of the orchestrator's own `deps`
  check in step 3.1 — it catches both a stale/missing/wrong-attempt input path and a mismatch
  between what the files on disk show and what `run-state.json` claims (e.g. leftover output from
  an abandoned earlier run, or a dep marked completed that never actually produced its file). If
  either check fails, the subagent must not guess or proceed: it should write `output.md`
  reporting exactly what was missing or mismatched (which input, or which dep/state
  discrepancy), and — if the node declares `branches` — end with `Result: missing input` (or
  `Result: state mismatch`, as applicable) so the branch evaluation (step 7) can route it
  accordingly (typically to `default` or a manual-flag node) rather than silently treating it as a
  technical failure or a normal outcome.
- Wait for the subagent call to return (it is blocking; do not proceed until it finishes).
- Treat the node as a **technical failure** if the subagent call crashes/errors, or returns
  without `output.md` having been written. Otherwise treat it as a successful execution (branch
  evaluation, if any, happens next and is independent of this success/failure judgment).

### 5. Dispatching a `type: map` node

- The node's `map_over` field names another node whose `items.json` (in that node's latest
  attempt folder) is the source list. Read and parse that `items.json`.
- Create this map node's own `attempt-{N}/` folder (bump N only when the *whole map node*
  re-runs, e.g. via a branch loop-back — not per item).
- Iterate items **sequentially, in list order, one at a time** (no parallel dispatch), numbering
  items `item-1`, `item-2`, ... — **1-indexed**, matching the 1-indexed `{seq}` node prefixes (item
  `i` of the source array, 0-based, becomes folder `item-{i+1}`). If this map node's current
  `attempt-{N}/` already has some `item-{i}/` folders marked completed in `run-state.json` (i.e.
  you're resuming a run interrupted mid-map), skip those and start from the first index without a
  completed record — do not re-dispatch already-completed items. For each remaining item at index
  `i`:
  - Create `{seq}_{node-id}/attempt-{N}/item-{i}/`.
  - Substitute `{{item}}` (the whole item, stringified) and `{{item.field}}` (individual fields)
    into the node's template prompt body.
  - Dispatch this substituted prompt exactly as a leaf node (step 4), writing to
    `item-{i}/attempt-1/output.md` (item retries bump only `item-{i}`'s own `attempt-N`, never the
    map node's `attempt-N`).
  - Update `run-state.json` immediately after this item finishes, before moving to the next item.
  - If the map node itself declares `ref`/`ref_from` (in place of `agent`), the item's template is
    a nested subgraph invocation, not a leaf agent call: write the substituted template text to
    `item-{i}/attempt-1/context.md` (this is never dispatched as a prompt to any agent directly),
    then apply step 6 within `item-{i}/` instead of writing a plain `output.md`, passing
    `context.md`'s content down per step 6's invocation-context rule.
- The map node as a whole is complete once every item has a completed (or exhausted-retry)
  attempt. For a plain (non-subgraph) item template, if an item never resolves after `retry`
  technical-failure attempts, treat the whole map node as a technical failure for the purposes of
  step 3.6 (`halt_reason: retries_exhausted`, with `status: halted` set on the map node itself).
  For a map-of-subgraphs item (`ref`/`ref_from` declared), do **not** flatten a nested halt into a
  generic technical failure: if that item's nested run reaches `status: halted` (per step 6), the
  whole map node halts too, propagating the nested run's *actual* `halt_reason` and the exact
  halted node's identity unchanged (the same node found by recursing through
  `item-{i}/.../subgraph_state`, per step 2's redrive lookup) — never rewritten to
  `retries_exhausted` regardless of what the nested halt actually was. This is the same
  propagate-the-nested-halt-reason behavior step 6 already specifies for an ordinary (non-map)
  subgraph node; a map-of-subgraphs item is not an exception to it.

### 6. Dispatching a `type: subgraph` node

- Resolve the target graph name first:
  - If the node declares `ref`, use it as-is — fixed at authoring time.
  - If the node declares `ref_from: {node-id}` instead, read that sibling node's (same graph,
    same run) latest `output.md` and take its trailing `Graph: <graph-name>` line as the target.
    If that node's latest `output.md` has no such line — missing, malformed, or the node never
    completed — treat this **as a technical failure of this subgraph node**: apply step 8's retry
    logic (re-attempt name resolution + execution in a new `attempt-{N+1}/`), and if retries are
    exhausted, halt with `halt_reason: retries_exhausted`. Never guess a graph name.
- The node's `ref` field (static or resolved via `ref_from`) names another graph:
  `agent_works/graphs/{ref}/graph.md`.
- Recurse into this skill's own algorithm (steps 1 and 3 — step 1 loads `agent_works/graphs/{ref}/graph.md`
  the same way) against that graph, but with its execution **nested under the current node's
  folder** — i.e. its `run-state.json` and node folders live at
  `{current-run-folder}/{seq}_{node-id}/attempt-{N}/`, not under `agent_works/graphs/{ref}/runs/`.
  The referenced graph's own top-level `runs/` folder is only used when that graph is invoked
  directly as a top-level `run-graph` call, not when reached via a subgraph node. Step 2 does
  **not** apply recursively as written (it scans `runs/*/run-state.json` to pick among multiple
  timestamped runs, which is a top-level-only concept) — at the nested level there is exactly one
  possible location, `{current-run-folder}/{seq}_{node-id}/attempt-{N}/run-state.json`; simply
  check whether it already exists (resume, applying the same node/item/subgraph-level skip rules
  from step 2 within this nested scope) or create it fresh.
- Node executions performed inside the recursion still count toward the **top-level** run's
  `total_executions` — pass that counter down into the recursion rather than tracking a separate
  one.
- **Invocation context (map-of-subgraphs only).** If this subgraph dispatch was reached via step
  5's map-item path (i.e. a sibling `context.md` exists at `item-{i}/attempt-1/context.md`), append
  that file's content to the prompt of **every** node dispatched anywhere in this nested run (all
  leaf and map-item dispatches, every attempt including retries and loop-backs) — not only its
  entry points, since which specific node's prompt actually needs the context depends on the
  target graph's own structure. Treat it as invocation-supplied context available for the lifetime
  of this nested run, the same as text "given directly as part of the run-graph request" would be
  for a top-level invocation. Ordinary (non-map) subgraph dispatches have no `context.md` and
  nothing is appended.
- The subgraph node is complete when its nested run reaches `status: completed`; it is a halt if
  the nested run halts — when this happens, set this subgraph node's own `status` to `halted` and
  its `halt_reason` to the nested run's `halt_reason` (in addition to that same `halt_reason` living
  inside its `subgraph_state`), propagating it upward one level at a time this way through however
  many levels of nesting separate this node from the top-level run, so a run halted deep inside
  nested subgraphs shows `status: halted` on every enclosing subgraph node's own entry, not only
  inside the innermost `subgraph_state`. This is what step 2's redrive lookup and clearing logic
  walks back down through.

### 7. Evaluating `branches`

After a node with `branches` completes successfully (i.e. `output.md` was produced):

- Read the node's `output.md` in full, and specifically its trailing `Result: <short phrase>`
  line if present.
- Judge, in the declared order of the `branches` list, which `condition` best matches the
  output/result. Use your own judgment on the plain-language condition text — this is not a
  structured match.
- If a condition matches, the run proceeds to that condition's `next` node — dispatched next
  regardless of where it sits in dependency order, and regardless of whether it was already
  marked `completed` (see step 3.5: a loop-back re-dispatches with a fresh `attempt-{N+1}/`) or
  `bypassed` (see step 3.5's bypassed-target override: a different upstream decision may have
  bypassed it earlier, but this decision still dispatches it fresh).
- If none match and a `default` is declared, proceed to `default` (same override rule applies).
- If none match and no `default` is declared, this is an **unresolved branch**: halt the run at
  this node (`halt_reason: unresolved_branch`), overriding this node's own `run-state.json` status
  to `halted` — even though it did produce `output.md` (this isn't a technical failure), `halted`
  is the authoritative marker for "this is the node the run stopped at," taking precedence over
  what its status would otherwise be.
- In every case (matched condition, `default`, or unresolved), log the outcome into
  `run-state.json` for this node: which condition text matched (or `"default"`, or
  `"unresolved"`), and which node (if any) was chosen next.

### 8. Retry on technical failure

- `retry` (default 0) only applies to **technical failure** (crash, or no `output.md` produced) —
  never to a branch outcome you disagree with, and never to a completed map item's individual
  content quality.
- On technical failure, if the node's completed-attempt count so far is `<= retry`, re-dispatch
  the same node (or map item) in a new `attempt-{N+1}/` folder, incrementing `total_executions`.
- If retries are exhausted and the node still failed technically, halt the run at this node
  (`halt_reason: retries_exhausted`), overriding this node's own `run-state.json` status to
  `halted` (superseding whatever transient `failed` status its last attempt recorded) — this is
  the authoritative marker for "this is the node the run stopped at."

### 9. `run-state.json` bookkeeping

Write/update `run-state.json` immediately after **every single node execution** — not batched,
not deferred to the end of the run. This includes:

- Each leaf node dispatch (success or technical failure).
- Each individual map item's dispatch, before moving to the next item.
- Each level of a subgraph recursion, both when it starts and when it resolves.
- Every branch evaluation outcome (matched condition / default / unresolved).
- Every halt decision, with its `halt_reason` (`unresolved_branch`, `retries_exhausted`, or
  `capability_gap`).

Track per node: node id, status (`pending` / `running` / `completed` / `failed` / `halted` /
`bypassed`), current attempt number, and (if applicable) the branch decision logged in step 7.
`bypassed` comes from step 3.5; `halted` is set, overriding whatever the node's status would
otherwise be, by whichever of the three halt paths actually triggered (step 3.2's capability check,
step 7's unresolved branch, or step 8's exhausted retries) — always on the exact node the run
stopped at, never on any other node. Track for the
run overall: `status`, `total_executions`, and `halt_reason` if halted. This is what makes a later
resume (step 2) accurate — resuming must be able to rely on `run-state.json` alone to know exactly
which nodes/items/subgraph levels are done and which are not.

Minimal schema (fields beyond this may be added if useful, but these are required):

```json
{
  "status": "running",
  "total_executions": 4,
  "halt_reason": null,
  "nodes": {
    "01_planner": {
      "status": "completed",
      "attempt": 1,
      "branch_decision": null
    },
    "02_per_task_impl": {
      "status": "running",
      "attempt": 1,
      "items": {
        "item-1": { "status": "completed", "attempt": 1 },
        "item-2": { "status": "pending", "attempt": 0 }
      }
    },
    "03_review": {
      "status": "completed",
      "attempt": 1,
      "branch_decision": { "matched": "review found critical issues", "next": "02_per_task_impl" }
    }
  }
}
```

A `type: subgraph` node's entry additionally carries its nested run's own state under a
`subgraph_state` key (same shape as this whole file, recursively) instead of `items`. If the node
declared `ref_from` rather than a static `ref`, also record the resolved graph name at the time of
this attempt as `resolved_ref` on that node's entry, for auditability.

## Halting

A run halts (stops without completing) for exactly three reasons, always recorded as
`halt_reason` in `run-state.json`:

- `unresolved_branch` — a completed node's `branches` had no matching condition and no `default`.
- `retries_exhausted` — a node (or map item) failed technically more times than its `retry` count
  allows.
- `capability_gap` — the pre-dispatch check (step 3.2) found the declared agent lacks a tool or
  permission the node's task needs, or a dispatch was blocked/rejected by a permission prompt.
  Never resolved by retrying, substituting a different agent, or narrowing scope on your own
  judgment — only by the user fixing the graph, granting the permission, or explicitly directing
  a specific path forward.

On halt, report to the user which node halted and why. Re-invoking `run-graph` on this graph does
**not** resume it automatically — a halted run stays halted until the user explicitly asks to
redrive it (see step 2), which resets just the halted node and continues, or explicitly asks to
start fresh, which abandons it in favor of a brand-new run.
