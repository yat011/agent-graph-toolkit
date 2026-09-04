---
name: agentgraph-define-graph
description: Use when the user wants to turn a written plan into an executable agent graph for review — e.g. "define a graph for this plan", "turn this plan into a graph", "break this plan into nodes". Reads a plan file, breaks it into nodes, writes graph.py (project tier by default; user tier only when the user explicitly asks), and iterates with the user until they confirm it. Does not execute the graph — that's a separate skill.
---

# agentgraph-define-graph

Turns a written plan (`agent_works/plans/*.md`) into a `graph.py` file — plain, idiomatic
LangGraph `StateGraph` Python code, never a hand-rolled builder DSL and never a markdown/YAML
format — and iterates with the user on the design until they confirm it.

`agentgraph-run-graph` resolves a graph by name in this order: **project**
(`agent_works/graphs/{name}/graph.py`) **> user** (`~/.agents/graphs/{name}/graph.py`, via
`Path.home()`) **> template** (`skills/agentgraph-run-graph/templates/{name}/graph.py`).

**This skill only writes `graph.py`. It never calls `agentgraph-run-graph`, never invokes the
`agentgraph` CLI to start a run, and never dispatches a Worker.** Defining a graph and running a
graph are separate skills — running is out of scope here even if the user seems eager to proceed;
hand off to the `agentgraph-run-graph` skill (or ask them to invoke it) once they confirm.

Before doing anything else, read `CONTEXT.md` (repo root) for this toolkit's glossary (Graph,
Node, Worker, Executor, Run, Template graph) and `agentgraph_engine/dispatch.py`'s module
docstring for the exact `dispatch_worker`/`dispatch_with_retry` signatures this skill's generated
code must call — do not re-derive or duplicate either from memory. Reading the two shipped
templates (`skills/agentgraph-run-graph/templates/{feature-kickoff,standard-phase}/`) is
the fastest way to see the idiom this skill must reproduce: composed per-node `TypedDict` records
from `agentgraph_engine.states`, one explicit node function per unit of work (in that template's
`nodes.py`), a router function per branching node, `build_graph(checkpointer=None)` compiling
and returning the graph. `graph.py` is wiring only — no node bodies or prompt-builders.
Kickoff-style graphs size work with `agentgraph-phase-sizing` (sequential context-window phases,
not parallel tickets).

## Steps

### 1. Resolve the input plan

- If the user gave a plan path, use it.
- Otherwise, default to the most recently modified file under `agent_works/plans/`.
- Read the plan in full before proceeding.
- If the plan is too vague or thin to decompose into distinct nodes (e.g. a one-line note with
  no real steps), don't guess — tell the user what's missing and ask them to flesh out the plan
  (or point you at a different one) before continuing. Do not invent nodes not grounded in the
  plan's content.

### 2. Choose the graph name and location

- Derive `{graph-name}` from the plan file's name (kebab-case, drop the extension and any
  redundant "plan" wording if it's already implied by context), e.g.
  `agent_works/plans/agent-graph-skills.md` → `agent-graph-skills`.
- **Default write target is the project tier:** `agent_works/graphs/{graph-name}/graph.py`.
- **User-tier write is explicit-only.** Write to
  `{Path.home()}/.agents/graphs/{graph-name}/graph.py` only when the user explicitly asks to save
  a user graph (e.g. "save this as a user graph", "write this to my user graphs"). Never default
  to the user tier. Never hardcode a personal home path; compute it with `Path.home()`.
- `agentgraph-run-graph`'s CLI resolves the same name through project > user > template — no
  registration step.
- Check whether the chosen-tier `graph.py` already exists:
  - If it exists and the user is asking to redefine/update it, treat this as an edit to the
    existing graph (skip to step 5's loop directly).
  - If it exists and this looks like a fresh definition request for the same plan, ask the user
    whether to overwrite or pick a different name — don't silently clobber prior work.

### 3. Break the plan into nodes

Read the plan's structure (sections, task lists, "Tasks" headings, etc.) and design nodes that
cover the work end to end. For each unit of work, decide:

- **Node name and state fields** — a Python identifier (e.g. `create_feature_branch`,
  `tech_plan_reviewer`), plus which `TypedDict` state fields it reads and writes. Order nodes so
  dependencies only ever point at fields an earlier node (in execution order) actually sets.
- **Node kind** (all three compile down to plain `StateGraph` code — there is no DSL-level type
  tag to set, this is a design vocabulary only):
  - **Dispatch node** — a node function that calls
    `agentgraph_engine.dispatch.dispatch_with_retry(retry=0, role=..., task_prompt=..., output_path=...)`
    and returns a state update built from the result. Use for any discrete, one-shot piece of
    Worker work (implement a thing, review a thing, write a doc). If `dispatch_with_retry`'s result
    isn't `ok`, the node must return halt fields (`halted`, `halt_reason: "retries_exhausted"`,
    `redrive_node` = this node's own name, `reset_attempts: True`) and route to `pause_node`
    (`agentgraph_engine.pause.pause`), which `interrupt()`s. Do not add a dead-end halt/manual
    terminal wired to `END`. `agentgraph redrive` resumes the pause (`Command(resume="redrive")`)
    then `Command(goto=redrive_node)`.
    A **receipt** node (the old engine's `receipt: true`) is a node function that writes its own
    `output.md` directly (no dispatch) — see `standard-phase/graph.py`'s `success` node.
  - **Map/fan-out node** — a node function that loops **sequentially** (no concurrent dispatch,
    per this migration's settled design) over a list already present in state (produced by an
    earlier node), dispatching or recursing once per item. Honor an item's own `dependencies`
    list (other item ids) the same way `feature-kickoff/graph.py`'s `pick_next_phase` does: skip an
    item until every listed dependency has reached a success terminal; leave an item permanently
    blocked (never dispatched) if a dependency id is missing; if a nested item **pauses**,
    `interrupt()` immediately so later items do not keep running; pause with
    `halt_reason: "unmet_dependencies"` only if nothing is ready and something is still waiting
    (a cycle).
  - **Subgraph node** — compile the child with the **same checkpointer** as the parent
    (`get_build_graph(...)(checkpointer=checkpointer)`). Invoke it with a per-item `thread_id`
    (feature-kickoff uses `{parent_thread}:{item-n}`). If the child `interrupt()`s, the wrapper
    must `interrupt()` too — do not swallow `__interrupt__` and continue the map. Never
    `.invoke()` a compiled subgraph without a checkpointer if that subgraph can pause. See
    `feature-kickoff/graph.py`'s `pick_next_phase` / `run_one_phase`.
- **Branches** — add a router function (`def route_after_x(state) -> str`) wherever the plan
  implies a decision point. Every node a router reads from must have its dispatch prompt instruct
  the subagent to end its output with a single-line `Result: <short phrase>` conclusion
  (`agentgraph_engine.dispatch.extract_result_line` pulls this out of `output.md`, falling back to
  the CLI's own chat text). **The router itself does plain `.startswith()`/equality checks against
  the literal phrases you told the prompt to produce — never an LLM call, never a generic
  string-similarity matcher.** Branch judgment is code, not an LLM decision. Wire the
  router via `graph.add_conditional_edges(node_name, router_fn, {"branch_label": "target_node",
  ...})`.
- **Retries** (`dispatch_with_retry(retry=0, ...)`) — production nodes always pass `retry=0`.
  A technical CLI failure pauses for a human `redrive`; do not auto-replay the Worker. This is
  independent of a branch-driven loop-back (e.g. "review rejected, try again") — a loop-back needs
  its own attempt counter in state (an int field incremented once per entry into the looped node),
  checked by the router so the loop is self-bounding (see the "Loops must self-limit" convention
  below). If a node's dispatch prompt causes side effects that aren't safe to blindly repeat,
  phrase the prompt so it checks existing state before acting, per the retry-idempotency
  convention below.
- **Aggregation** — a node that depends on a map node's fanned-out results reads them from state
  directly (they're already in-process Python data, unlike the old file-based engine) — but if
  that data needs to reach a **Worker's prompt**, pass a file path there, never paste the content
  inline (the old "pass a path, never paste" convention still applies at the prompt boundary, even
  though it no longer applies to in-process state).

A dispatch node's `task_prompt` is a **work order** only: what to read, the `Result:` contract, and this-run context. Craft lives in `agents/{role}.md`. Do not restate the role prompt or the dispatch output-path instruction. Layout: static work order first, then append dynamic paths/payloads, then a retry appendix. Never use triple-quoted f-strings (`f"""` / `f'''`); use parenthesized implicit concatenation of ordinary strings / f-strings, one physical line per fragment. Do not tell authors to emit `kind` or `full_suite`. Named roles must have a non-empty `agents/{role}.md` (the CLI preflights this before invoke).

### 4. Write graph.py

Write `graph.py` at the path chosen in step 2 (project tier unless the user explicitly asked for
a user graph) as plain LangGraph code:

- Composed per-node state: a `TypedDict(..., total=False)` with graph-level fields (`run_dir`,
  `halted`, `halt_reason`, `halted_at_node`, `outcome`) plus one nested record per node
  (`attempt_count`, `result_line`, `output_path`; gates also have `route` / `halt_reason`).
  Reuse `agentgraph_engine.states.base.BasicNodeState` / `GateNodeState` / `BaseGraphState` rather
  than flat prefixed keys (`implement_attempt_count`, …). Each nested record is written by
  exactly one node function.
- One node function per node (dispatch nodes call `agentgraph_engine.dispatch.dispatch_with_retry`
  — never hand-roll a `subprocess` call), one router function per branching node. Do not
  introduce a generic node factory.
- `graph.add_node(...)` for every node (including a shared `pause_node` that calls
  `agentgraph_engine.pause.pause` if any node can pause — do not add dead-end
  `manual_flag` / `blocked` / `needs_manual_review` terminals wired to `END`),
  `graph.add_edge`/`graph.add_conditional_edges` for every transition,
  `graph.add_edge(START, ...)` for the entry node. Command-returning nodes should declare
  `destinations=`. `interrupt()` requires compiling with a checkpointer.
- `def build_graph(checkpointer=None): ... return graph.compile(checkpointer=checkpointer)`, plus a
  module-level `graph = build_graph()` for direct import in tests/tools that don't need a
  checkpointer.
- Make sure any loop-back router is self-bounding (an attempt-count check), per the "Loops must
  self-limit" convention — there is no global execution cap to fall back on.

### 5. Generate the review diagram — never hand-authored

Load and compile the graph you just wrote, then render it:

```
python -c "
from agentgraph_engine.graph_loader import load_graph_module, get_build_graph
m = load_graph_module('agent_works/graphs/{graph-name}/graph.py')
print(get_build_graph(m)().get_graph().draw_ascii())
"
```

(`draw_ascii()` needs the `grandalf` package — already an `agentgraph-engine` dependency; use
`.draw_mermaid()` instead if an ASCII box diagram renders awkwardly for a particular shape, e.g. a
graph with many branches). **Never hand-author or hand-maintain this diagram** — if you edit the
graph, regenerate it from the compiled result again; the diagram is a read-out of the actual
code, not a separate artifact to keep in sync by hand.

If the load/compile step itself fails, that's a real bug in the `graph.py` you just wrote — fix it
before showing anything to the user, don't paper over it with a description of what the diagram
would look like.

### 6. Confirm/redefine loop with the user

- Show the user the rendered diagram (paste the actual `draw_ascii()`/`draw_mermaid()` output)
  along with a short plain-language summary of the nodes and what each does.
- Ask the user to confirm the graph or describe changes.
- If they request changes, edit `graph.py`, then regenerate and show the diagram again (step 5) —
  never hand-edit the diagram text itself.
- Repeat until the user explicitly confirms. Do not proceed to running the graph yourself at any
  point — once confirmed, tell the user the graph is ready and that running it is a separate step
  (the `agentgraph-run-graph` skill), and stop.

## Conventions

These carry over from the retired `graph.md` engine, translated to Python:

- **Loops must self-limit.** Any router that can route back to an earlier node must check a
  bounded attempt counter (a state int field incremented once per entry into the looped node).
  When the cap is hit, pause with `interrupt()` (`redrive_node` = the code-writer,
  `reset_attempts: True`). Gate `Result: manual` / unrecognized pause with `redrive_node` =
  that gate so `agentgraph redrive --message` can instruct the reviewer. Do not route to a
  dead-end terminal. `agentgraph redrive` then `Command(goto=redrive_node)` with counters
  zeroed. There is no global execution cap.
- **Result-line convention.** Every dispatch node a router reads from must have its prompt end
  with a single-line `Result: <phrase>` conclusion; the router does plain string matching against
  the literal phrases you told the prompt to produce.
- **Retry-idempotency note.** Production nodes do not technically retry. A human `redrive` re-runs
  a node's dispatch from scratch after a pause. Phrase prompts to check existing state before
  acting (e.g. "if X already exists, treat as done") rather than assuming a clean slate.
- **Sticky-research convention for loop-back retries.** A rejection-driven loop-back (e.g. a review
  node routing back to the node it reviewed) is fixing specific flagged issues, not starting a
  fresh investigation. Have the looped-back node's prompt read its own immediately preceding
  attempt's output first (both templates' `_implement_prompt`/`_planner_prompt` helpers in
  `nodes.py` show the pattern: glob the node's own prior `attempt-*/output.md` files) and scope
  fresh investigation to exactly what the rejection requires re-checking.
- **No `capability_gap` halt category.** A failing/erroring `claude` CLI dispatch is an ordinary
  technical failure, subject to the node's own `retry` count, then `halt_reason:
  "retries_exhausted"` — never a separate judgment call, because there's no coordinating LLM
  positioned to make one per-dispatch anymore.
