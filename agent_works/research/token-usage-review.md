# Token-Usage / Architecture Review — agent-graph-toolkit

Research only. No code changed. Repo root: `C:\Users\devya\code\agent-graph-toolkit` (this
worktree: `.claude\worktrees\agent-a15630c09c92d6c09`), branch `main`. No existing
`agent_works/research/` (or any `agent_works/`) convention was found in this repo — it's a
toolkit repo, not a project that runs its own graphs — so this file was created fresh at
`agent_works/research/token-usage-review.md`.

---

## 1. Plan reviewer too strict

### Current State

The rejection criteria for a tech-plan review live in two places that compose:

- `agents/tech-plan-reviewer.md:18-24` — the "Review every task" checklist:
  ```
  - **Size**: ... does it bundle multiple unrelated changes ...
  - **Test cases**: does it list at least 3 concrete test cases (not a vague "add tests")? Do the
    listed cases meaningfully cover the task's behavior (happy path plus real edge/failure cases),
    not padding?
  - **Clarity**: is it unambiguous enough that two different implementers would build the same
    thing from it?
  - **Dependencies**: are its stated dependencies ... correct and complete — nothing missing,
    nothing spurious?
  ```
- `agents/tech-plan-reviewer.md:49` — the reject threshold:
  ```
  Reject if any finding is severe enough that implementing against the plan/task list as written
  would produce the wrong thing, miss required behavior, or produce untested code. Minor/stylistic
  observations alone should not cause a Reject.
  ```
- `skills/agentgraph-vertical-slice-tasks/SKILL.md:3-4` (loaded by the reviewer, per
  `tech-plan-reviewer.md:21`) sets task-sizing standards the reviewer also checks against.

The threshold line (`tech-plan-reviewer.md:49`) already says minor/stylistic findings alone
shouldn't cause a Reject. The actual over-strictness is upstream of that line, in the **"Test
cases" bullet** (`tech-plan-reviewer.md:19-20`): it makes "at least 3 concrete test cases per
task" itself a hard, countable requirement, independent of whether the plan/spec actually needed
that granularity. A missing/thin test-case enumeration is exactly the kind of "missing test
detail an implementer would naturally discover during implementation" the user wants excluded —
but as worded, a reviewer following the checklist has a numeric hook to Reject on ("only 2 test
cases listed") even when nothing in the task is wrong or under-specified in a way that would
produce incorrect behavior.

### Problem

The reviewer's mandate mixes two different failure modes without separating them:
1. The plan **contradicts the spec** or **asserts something false about the codebase** (checked at
   `tech-plan-reviewer.md:14`: "What does it assert about the codebase ... that you haven't
   verified? Check every such claim ... and flag any that are wrong or unverifiable.") — this is
   the kind of finding that should gate implementation.
2. The plan is missing **incidental detail** (exact test-case count, edge-case enumeration,
   phrasing clarity) that a competent implementer would fill in naturally while writing the code
   and its tests — this should not gate implementation on its own.

Currently both feed the same Reject verdict with no distinction, and bullet (`:19-20`) turns
"missing test detail" into an explicit, checkable rejection trigger.

### Proposed Change

Edit `agents/tech-plan-reviewer.md`. Diff:

```diff
@@ tech-plan-reviewer.md:18-24 ("Review every task")
-- **Test cases**: does it list at least 3 concrete test cases (not a vague "add tests")? Do the listed cases meaningfully cover the task's behavior (happy path plus real edge/failure cases), not padding?
+- **Test cases**: does the task name *at least one* concrete test case, or is testing left as a
+  vague "add tests" with zero examples? A thin or incomplete test-case list is not itself a
+  reject-worthy finding — implementers are expected to flesh out edge/failure cases during
+  implementation — but a task with *no* stated test case, or one whose only stated case
+  contradicts the spec's behavior, is.
```

```diff
@@ tech-plan-reviewer.md:49 (reject threshold)
-Reject if any finding is severe enough that implementing against the plan/task list as written would produce the wrong thing, miss required behavior, or produce untested code. Minor/stylistic observations alone should not cause a Reject.
+Reject only for (a) a plan/task-list claim that contradicts the spec's stated behavior, or (b) a
+plan/task-list claim about the codebase that is factually incorrect (verified false, not merely
+unverified-but-plausible). Everything else — incomplete test-case enumeration, missing edge-case
+detail, phrasing an implementer could reasonably resolve while building or testing the task —
+goes in the report as a non-blocking note, never a Reject. Minor/stylistic observations alone
+should not cause a Reject.
```

This also implies loosening the "Size"/"Clarity" bullets similarly if the intent is strictly
contradiction/factual-error only — but the task explicitly asked to scope the fix to "missing
minor details or missing test detail," so the diff above targets exactly that bullet and the
threshold line rather than rewriting the whole checklist.

---

## 2. Orchestrator session length / hand-off pattern

### Current State

- `skills/agentgraph-run-graph/SKILL.md:11-14`: "You (the main agent) are still the runtime for
  the two things a script cannot judge: actually making each subagent-dispatch call, and reading
  its output to judge which outcome occurred."
- `skills/agentgraph-run-graph/SKILL.md:46-73` ("## Loop"): the orchestrator repeats `next` →
  dispatch one `Agent`-tool call → read `output.md` → `record-result`/`record-branch` → `next`
  again, **in the same session**, for every node in the graph, until `complete`/`halted`. There is
  no hand-off step anywhere in this loop.
- `GRAPH-SPEC.md:9-10`: "The main agent is the runtime: it reads a Markdown graph spec (`graph.md`)
  and drives execution itself via its own subagent-dispatch tool... There is no external framework
  or execution engine."
- Searching this repo (`skills/`, `agents/`, `README.md`, `CLI-CONTRACT.md`, `GRAPH-SPEC.md`) for
  the word "fork" returns **zero matches** — fork-agent delegation is not mentioned anywhere, so it
  is neither explicitly disallowed nor explicitly enabled. The orchestrator loop is simply
  authored as one continuous session making sequential `Agent`-tool calls; nothing in the skill
  text considers spawning a fresh top-level session mid-run.
- Run-state format (existing, on disk) is fully specified in
  `skills/agentgraph-define-graph/GRAPH-SPEC.md:58-65`: `run-state.json` tracks, per node,
  `status` (`pending`/`running`/`completed`/`halted`/`bypassed`), `attempt`, `branch_decision` (if
  the node declares `branches`); a `map` node additionally carries an `items` map recursively; a
  `subgraph` node carries a nested `subgraph_state` (same shape) plus `resolved_ref`;
  `total_executions`/`halt_reason` are tracked once at the top level. Location:
  `agent_works/graphs/{graph-name}/runs/{run-folder}/run-state.json`
  (`GRAPH-SPEC.md:25-37`). Every field the CLI reads/writes is already itemized in
  `skills/agentgraph-run-graph/CLI-CONTRACT.md:1-84` (the `next`/`record-result`/`record-branch`
  responses), and `state-store.js:14-26` shows `readState`/`writeState` are plain
  read-JSON/atomic-write-JSON — no session-affinity or in-memory state the file doesn't capture.

So: today it's one long-lived orchestrator session, dispatching every node sequentially via its
own `Agent`-tool calls, for the whole run. "Fork" is absent from the vocabulary entirely, not
banned.

### Problem

Because `run-state.json` is already the complete source of truth for "what's done, what's next"
(per the Checkpoint-every-node convention at `GRAPH-SPEC.md:348-351`: "writes/updates
`run-state.json` immediately after each node execution completes... so resume is always
accurate"), the orchestrator's own session history carries **no information a fresh session
couldn't reconstruct from disk** — it is pure accumulated context with no informational payoff,
and it grows without bound across a long multi-node run (large graphs like `feature-kickoff`,
`multi-feature-pipeline`). This is exactly the "stall-at-80-percent... end-of-long-context
disease" `unlazy`'s `references/orchestration.md:3-5` describes for driver-style loops.

### Proposed Change

Introduce an explicit hand-off point in `agentgraph-run-graph`'s loop (`SKILL.md:46-73`): after
every dispatch-and-record cycle (or every N, e.g. N=5, node completions), the orchestrator ends its
own turn by producing a short hand-off brief and directing a **fresh** top-level session (not a
same-context subagent call) to resume — mirroring `resolve-run --run {run_path}` semantics that
already exist for exactly this "pick a run back up" case (`CLI-CONTRACT.md:11-27`).

What needs to be serializable so a zero-context fresh session can resume correctly — everything
below is already either on disk today or trivially derivable from what's on disk:

- `run_path` (already returned by `resolve-run`/`next`, `CLI-CONTRACT.md:25,54`) — the one string
  a fresh session needs to reconstruct everything else via `node run-graph.js status --run
  <run_path>` (`CLI-CONTRACT.md:82-84`).
- The graph name and `graphs-root` (needed only if non-default; `CLI-CONTRACT.md:11-13`).
- Nothing about *history* needs to be serialized — `run-state.json` already is the full
  node-by-node ledger (`GRAPH-SPEC.md:58-65`), and `status` (`CLI-CONTRACT.md:82-84`) already
  surfaces `total_executions`, `halt_reason`, and per-node state in one call.

Concretely, this requires no new run-state schema field — the existing schema is already
sufficient — only a new *behavioral* instruction in `SKILL.md`'s loop: after step 2's
record-result/record-branch, instead of unconditionally looping back to `next`, the orchestrator
should end its turn with a one-line hand-off message ("resume run `{run_path}` for graph
`{graph-name}`") and let the host start a brand-new session that begins the loop at step 2 with
`node run-graph.js status --run {run_path}` (new read, not in current `CLI-CONTRACT.md` loop
usage, but the command already exists at `CLI-CONTRACT.md:82-84`) to reorient before calling
`next`. This is a SKILL.md text change only — `run-graph.js`/`engine.js`/`state-store.js` need no
code change, since checkpointing is already unconditional
(`GRAPH-SPEC.md:348-351`).

---

## 3. Delta / incremental re-run

### Current State

- Resume/redrive mechanics are fully described in `skills/agentgraph-run-graph/SKILL.md:19-31` and
  implemented in `skills/agentgraph-run-graph/lib/engine.js:545-576` (`resolveRun`):
  - **Resume** (no flags): if the latest run for a graph is `status: 'running'`, `next` simply
    continues from wherever `run-state.json` says execution stopped
    (`engine.js:563-564`); nodes already `status: 'completed'`/`'bypassed'` are skipped
    (`engine.js:108`, `175`, `184`, `225`, `256`, `322`, `363`, `467`, `916`).
  - **Redrive** (`--redrive`): finds the most recent `halted` run
    (`engine.js:550`) and calls `performRedrive`, which sets `entry.redrive_pending = true`
    (`engine.js:647`) — per `SKILL.md:29-31` and `CLI-CONTRACT.md`'s framing, this "resets just
    the halted node and continues," i.e. only the one node that caused the halt is re-dispatched;
    everything upstream stays as recorded.
  - **Fresh** (`--fresh`): starts an entirely new run folder
    (`engine.js:567-575`), discarding nothing but starting from zero — no reuse of prior node
    outputs at all.
- The only mechanism that ever **re-executes an already-successful node** is a `branches`
  loop-back declared in `graph.md` (e.g. `06_batch_review` rejecting back to `10_fix`, then
  `10_fix` → `06_batch_review` again — `templates/quick-feature/graph.md:12-19,395-425`). That
  path is bounded by an explicit attempt-count condition in the branch text itself (per the
  "Loops must self-limit" convention, `GRAPH-SPEC.md:258-263`), and it re-runs the whole loop body
  every time — it is not scoped to "just the node with the bug."
- `CLI-CONTRACT.md:1-84` lists the entire command surface: `resolve-run`, `next`, `record-result`,
  `record-branch`, `record-halt`, `status`. There is no command to mark an arbitrary
  already-`completed` node as needing re-execution, and no notion of "downstream dependents" being
  computed for anything other than the deps-ordering that governs forward dispatch
  (`GRAPH-SPEC.md:77`: `deps` = "node ids that must complete first" — a purely forward-looking
  field; nothing in the schema records the *reverse* edge, i.e. which nodes depend on a given one).

### Problem

If a user finds a late bug traceable to one specific already-completed node's output (e.g. task 3
of 6 in a `05_run_tasks` map was subtly wrong, discovered only during `06_batch_review` or even
after `09_success`), the current mechanism offers exactly three options: (1) resume, which does
nothing because the node is already `completed`; (2) redrive, which only applies to a `halted`
run and only resets the one node that caused the *halt* (not an arbitrary node the user names);
(3) fresh, which discards the whole run and re-executes everything, including nodes that were
fine. There is no "re-run node X and everything that transitively depends on X's output" path —
targeted incremental re-run doesn't exist.

### Proposed Change

1. **Add reverse-dependency computation.** `graph.md`'s `deps` field (`GRAPH-SPEC.md:77`) already
   encodes the forward edges; a "downstream of node X" set is a pure function of the existing
   `graph.md` parse (`lib/graph-parser.js`) — no schema change needed to *compute* it, only a new
   traversal in `engine.js` (e.g. `downstreamOf(graph, nodeId)` walking `deps` in reverse).
2. **Add a `dirty`/`invalidated` node status.** Extend the status enum used in `run-state.json`
   (currently `pending`/`running`/`completed`/`halted`/`bypassed`, `GRAPH-SPEC.md:58-59`) with a
   new value, e.g. `invalidated`, distinct from `pending` (never run) so the run's history/attempt
   folders for that node are preserved for audit rather than overwritten.
3. **Add a CLI command**, e.g. `node run-graph.js invalidate --run <run_path> --node <id>`,
   alongside the existing `record-result`/`record-branch`/`record-halt` commands
   (`CLI-CONTRACT.md:62-81`), that: sets the named node's `run-state.json` entry to `invalidated`,
   then walks `downstreamOf` and sets every transitively-dependent node (including map items whose
   `itemsSource[].dependencies` chain through it, per the existing dependency-honoring logic at
   `GRAPH-SPEC.md:376-387`) to `invalidated` as well, bumping nothing else. Response shape would
   mirror the existing `{status:"ok", run_path, node_status}` pattern used by `record-result`
   (`CLI-CONTRACT.md:66`).
4. **Teach `next` to treat `invalidated` like `pending` for dispatch purposes** — i.e. change the
   skip conditions currently keyed to `status === 'completed' || status === 'bypassed'`
   (`engine.js:108,175,184,225,256,322,363,467,916`) to also *not* skip `invalidated`, so the next
   `next` call after an `invalidate` re-dispatches exactly the invalidated node and its downstream
   set, in dependency order, while every untouched node stays `completed` and is never
   re-dispatched.

This reuses the existing `attempt-N/` folder convention (`GRAPH-SPEC.md:406-413` — "Every node
execution... gets its own `attempt-N/` folder") for the re-run, so a targeted fix produces
`attempt-2` (or higher) exactly like today's retry/loop-back attempts do, keeping the full audit
trail rather than mutating history in place.

---

## 4. unlazy repo comparison

### Current State

`C:\Users\devya\code\unlazy` exists and is a single `SKILL.md`-based orchestration skill (its own
package, not a project instance), with references at
`references/{method.md, orchestration.md, gates.md, token-economy.md}`.

**CatHome:** searched broadly (`/c/Users/devya`, session caches, `.claude/projects/`,
`.cursor/`, `.codeium/`, `.gemini/`, etc.). `CatHome` exists only as an unrelated Unity game
project at `C:\Users\devya\CatHome` (a `.sln`/Unity Assets tree, e.g.
`C:\Users\devya\CatHome\Assets\Editor\CatHomeSaveSetup.cs`) with no `3d model postscaling` /
`navlink` orchestration-run artifacts, and no `runs/`, `examples/`, `agent_works/`, or `logs/`
directory containing an agent-graph-style run log anywhere under it or under `unlazy`. **No
CatHome orchestration run log was found — stating this explicitly per the task's instruction
rather than guessing.**

unlazy's actual mechanism (from its reference docs, in place of a CatHome case study):

- **Orchestrated mode** (`references/orchestration.md:1-30`): the driver session plans (writes
  `PLAN.md` with a contract + tree + one gates file per leaf), then for each leaf: dispatches a
  **fresh subagent** whose entire brief is "the contract section of PLAN.md... its own gates file,
  verbatim" — explicitly **not** the driver's history, not other leaves' outputs, not the whole
  PLAN.md (`references/token-economy.md:25-29`). The driver re-verifies every leaf's self-reported
  gates itself (`orchestration.md:20-24`) rather than trusting it, and appends one status-log line
  per leaf to `PLAN.md` (`orchestration.md:25-26`).
- **Gate files** (`references/gates.md:1-66`) are the persisted state format: checkbox +
  `CHECK:`/`EXPECT:`/`EVIDENCE:` lines per outcome, parsed by `scripts/gate-check.mjs` and enforced
  by `scripts/stop-hook.mjs` (structural, not model-judged) — a shell command replaces "the model
  re-derives whether this is true" (`token-economy.md:12-15`).
- **Append-only status log** (`token-economy.md:30-33`): "Rewriting a file's head invalidates
  prompt cache for everything after it; appending keeps the stable prefix stable" — an explicit
  prompt-cache argument for append-only bookkeeping files, which `agent-graph-toolkit` doesn't
  currently reason about (its `run-state.json` is fully rewritten via atomic rename on every
  update, `lib/state-store.js:28-54`, not appended).
- **Cost-tiering by leaf type** (`orchestration.md:57-64`, `token-economy.md:38-42`): mechanical
  leaves go to a cheap model, design/integration/verification leaves stay on the strong model —
  directly analogous to agent-graph-toolkit's existing `model: cheap` convention
  (`GRAPH-SPEC.md:389-391`), already present.
- **Explicit small-task threshold** (`orchestration.md:66-71`, `token-economy.md:43-46`): "Below
  roughly half an hour of real work... stay solo." agent-graph-toolkit has no equivalent
  guidance anywhere in `SKILL.md`/`GRAPH-SPEC.md` — every graph run always dispatches subagents
  per node regardless of how small the graph is.

### Borrowable techniques

- **Lean leaf briefs, explicitly stated as a rule.** agent-graph-toolkit already passes a
  composed prompt per node (`CLI-CONTRACT.md:31`: "readiness-check text, redrive notice,
  invocation-context all pre-appended") and already tells nodes to "read `output.md` at a path,
  not paste it into the prompt" (`GRAPH-SPEC.md:117,394`) — the mechanism is equivalent to
  unlazy's leaf-brief scoping, just not named as an explicit design principle the way
  `token-economy.md:25-29` states it. Worth stating the "never receives the driver's transcript"
  framing explicitly in `GRAPH-SPEC.md`'s conventions section for clarity, though functionally
  it's already close.
- **Structural (non-model) enforcement layer (stop-hook).** unlazy's `stop-hook.mjs`
  (`gates.md`, `orchestration.md:48-50`) structurally blocks a session from ending while gates are
  unmet — a layer agent-graph-toolkit has no equivalent of. This is a genuinely new mechanism, not
  present in `CLI-CONTRACT.md`'s command surface, that could be adapted: e.g. a hook that checks
  `run-state.json`'s halt_reason before allowing a session to end without calling `record-halt`
  first, catching an orchestrator that silently drifts away from an incomplete run.
- **Solo-mode threshold for small graphs.** Directly portable: add explicit guidance to
  `SKILL.md`/`README.md` recommending against invoking `agentgraph-run-graph`'s per-node subagent
  dispatch machinery for a graph small enough (e.g. 2-3 leaf nodes, no map/subgraph) that it costs
  more in dispatch/context-reestablishment overhead than it saves — mirroring
  `orchestration.md:66-71` verbatim in spirit.
- **Append-only bookkeeping for prompt-cache stability** is the one clear architectural mismatch:
  `run-state.json`'s current atomic full-rewrite (`state-store.js:28-54`) is necessary for it
  (JSON can't be sanely appended), but if `agent-graph-toolkit` ever adds a human-readable
  companion log (e.g. for the hand-off brief proposed in item 2), it should follow unlazy's
  append-only status-log pattern rather than rewrite-in-place, for the same cache-stability reason.

Nothing in unlazy looks like it should **outright replace** an agent-graph-toolkit mechanism —
the two systems solve the same problem differently (unlazy: prose contract + shell-verified gate
files; agent-graph-toolkit: a deterministic `run-state.json`-driven CLI engine) and
agent-graph-toolkit's approach is already more machine-checkable than unlazy's gate-file
convention for the state-tracking piece specifically. The complementary pieces (stop-hook,
small-task threshold, explicit lean-brief principle) are the actual borrowable items.
