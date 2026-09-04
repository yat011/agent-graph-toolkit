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

## Worker CLI

`start`, `resume`, and `redrive` each resolve the Worker CLI **once per process**:

1. `--cli claude|grok|cursor|grok-orca|muse` on that command, if present
2. else `worker_cli` in `~/.agents/agentgraph.json`, if present
3. else `claude`

The resolved value is kept in memory for every dispatch in that invoke. A later `resume` /
`redrive` is a new process and resolves again; the checkpointed `worker_cli` is written so
`status` can show it, not as an input to the next process's selection. `status` does not
dispatch and does not take `--cli`.

The settings file is optional (read if present; `start` does not create it):

```json
{ "worker_cli": "claude" }
```

## Commands

### `agentgraph start --graph <name> [--slug <slug>] [--spec <path>] [--input-json <path>] [--agent-works-root <path>] [--recursion-limit N] [--cli claude|grok|cursor|grok-orca|muse]`

Starts a brand-new Run of the named graph (`feature-kickoff`, `standard-phase`, a project graph,
or a user graph), loaded dynamically via `agentgraph_engine.graph_loader` (never copied into a
project). Resolution order is project (`agent_works/graphs/{name}/graph.py`) then user
(`~/.agents/graphs/{name}/graph.py`, via `Path.home()`) then built-in template
(`skills/agentgraph-run-graph/templates/{name}/graph.py`). Creates
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

### `agentgraph resume --run <run_path> [--resume-value <value>] [--recursion-limit N] [--cli claude|grok|cursor|grok-orca|muse]`

Resumes a Run paused at a LangGraph `interrupt()` call (not a halt — see below), using the same
`run_path`'s `checkpoints.sqlite`. `--resume-value` becomes the interrupted node's `interrupt()`
return value via `Command(resume=...)`. Nodes already completed before the interrupt are **not**
re-executed — this is the mechanism behind the migration's demonstrated checkpoint-resume proof
(see `agentgraph_engine/examples/hello_graph/` and its test).

### `agentgraph status --run <run_path>`

Prints the Run's current checkpointed state: `{"run_path": ..., "next": [...], "values": {...}}`.
`next` is the node(s) LangGraph would run on the next `invoke`/`resume` call (empty if the Run
reached `END`).

### `agentgraph redrive --run <run_path> [--message <text>] [--recursion-limit N] [--cli claude|grok|cursor|grok-orca|muse]`

Continues a **paused** Run (`interrupt()` at `pause_node` or inside a nested-task wrapper) or
re-attempts a **halted** hello_graph sink.

If the checkpoint has interrupts, `redrive` issues `Command(resume=...)`. The pause node
(or nested map wrapper) then `Command(goto=redrive_node, update=...)`. Every pause zeroes nested
`attempt_count` so a redrive cannot immediately re-hit the same cap. `--message` is stored as
`redrive_message` and appended to the target node's Worker prompt (use it to tell a reviewer a
finding is non-blocking, etc.). `redrive_node` in the interrupt payload is the jump target:
the **gate itself** after `Result: manual` / unrecognized; the **writer** after reject budget;
the **failed node** after a technical death.

Time-travel (`get_state_history` + `update_state` + `invoke(None)`) remains only for graphs that
still END on a technical halt (hello_graph). Production templates pause instead of routing to END.

An unrecognized `Result:` line on a gate is `unrecognized_result` and pauses **immediately** —
there is no self-retry hop.

### `agentgraph monitor [--agent-works-root <path>] [--interval <seconds>]`

A read-only Textual TUI over every Run under `--agent-works-root` (default `agent_works`) —
**not** the one-JSON-object-to-stdout contract the other four commands share; it draws an
interactive terminal UI and does not exit until the user quits. There is no `--cli`: `monitor`
never dispatches a Worker.

A fleet table lists one row per Run (`run_id`, `graph_name`, status, current node) via
`agentgraph_engine.monitor.discovery.discover_runs` / `.status.fleet_rows` — nested map-item
threads (`{run_id}:item-*`) are never a second fleet row, only children on their parent's detail
view. The table hides `Completed` Runs by default; `c` toggles them back in. `Enter` opens the
selected Run's detail view (status, current node, per-node timings from the checkpoint chain,
ASCII topology with the current node highlighted, and a selectable table of nested children).
Selecting a child row and pressing `Enter` again drills into that child's *own* status, current
node, timings, and topology — compiled from the child's own graph (always `standard-phase`, per
`nodes.py:645-680`, regardless of the parent's `graph_name`), not the parent's. `Escape` returns
to the previous screen; `q` quits. A timer re-runs discovery and checkpoint reads every
`--interval` seconds (default `3`; non-positive values are rejected before the TUI starts).

`monitor` only opens Runs' `checkpoints.sqlite` files read-only
(`agentgraph_engine.monitor.checkpointer.open_readonly_checkpointer`) — a poll tick never calls
`.invoke()`, `Command(...)`, or `update_state`, and never redrives a paused or halted Run.

## Halting vs pausing

Production templates (`feature-kickoff`, `standard-phase`) **pause** with `interrupt()` instead of
routing to a dead-end terminal. The CLI summary then has `interrupted: true` (and usually
`halted: true` as well — halt fields record *why* and *where to redrive*). Use `agentgraph redrive`
to continue. `agentgraph resume --resume-value` is for author-placed interrupts that expect a
resume value (hello_graph's checkpoint gate).

hello_graph still ENDs on a technical Worker death (`halted` sink). That path keeps the
time-travel redrive fallback.

Halt / pause reasons:

- `retries_exhausted` — a node's headless-CLI dispatch failed (non-zero exit with no `Result:`
  line, or the worker didn't write its required output file). Production templates use `retry=0`,
  so the first technical failure pauses. Redrive target: the failed node; attempt counters
  **are** reset.
- `unmet_dependencies` — the sequential map had remaining items still waiting on unfinished
  `dependencies` (a cycle) with nothing ready to dispatch. A missing dependency id is left
  `blocked` rather than pausing the whole map. A **paused** upstream item interrupts the map
  immediately so later items do not keep running.
- `manual_requested` — a gate's `Result:` line started with `manual` (or implement `Result: stopped`).
  Redrive target: the **gate** (`review` / `tech_plan_reviewer`); implement `stopped` still redrives
  implement. Attempt counters reset. Pass `--message` to instruct the reviewer.
- `reject_attempts_exhausted` — a gate's reject-loop budget was already at the cap. Redrive
  target: the code-writer; attempt counters reset.
- `unrecognized_result` — a gate's `Result:` line matched none of accepted / rejected / manual.
  Pauses immediately; no self-retry hop. Redrive target: the **gate**; attempt counters reset.

## Branch judgment

Every ported node's routing is a plain Python function reading the `Result: <phrase>` line
extracted from the dispatched Worker's output (`agentgraph_engine.dispatch.extract_result_line`)
— string-matching only, never an LLM judgment call. A Coordinating agent invoking
`agentgraph start`/`resume` never judges a branch itself; that logic is compiled into the graph.

## Permission mode by model tier

On the Claude Worker CLI, `--permission-mode auto` lets the model's own action-classifier judge
each tool call, but that classifier requires Sonnet-tier and above. A dispatch resolved to the
`haiku` model instead uses `--permission-mode acceptEdits --allowedTools Write` (no classifier
needed; `Write` is the one tool this dispatch path's contract requires). Grok uses
`--permission-mode auto` and passes the work order as the value of `-p` / `--single` (Grok does
not read that prompt from stdin). Cursor uses `--auto-review` (plus `--approve-mcps` and `--trust`, never
`--force`). Muse runs `muse exec --json` with the work order as its positional prompt
(`exec` never reads stdin), the graph tier as `--reasoning-effort` (cheap `low`, sonnet
`high`, opus `max`), and `--approval-mode never --disable-sandbox --trust-workspace
`--user-input-auto-resolve` so a headless worker can write files, run shell commands, and
never block on input; its `run` lifts the JSONL `run_terminal` text into the envelope
before parsing. `DispatchResult.ok` comes back `False` — an ordinary technical failure, retried/halted
like any other — if a dispatch's permission mode still blocks the write it needs.

## Map/fan-out and subgraph composition

`pick_next_phase` / `run_one_phase` (in `feature-kickoff`) replace a Python loop over
`standard-phase`. `run_one_phase` invokes a **compiled** `standard-phase` graph that shares the
parent's checkpointer, each item on its own `thread_id` (`{parent}:{item-n}`). If the child
pauses, the wrapper `interrupt()`s immediately so later map items do not keep running. On
`agentgraph redrive`, the wrapper resumes the child with `Command(resume="redrive")` inside that
item's thread. Permanently blocked items (missing dependency id) are left `blocked` and not
dispatched. A paused upstream item stops the map (interrupt), it is not treated as done.

Each item's artifacts land under `{run_dir}/05_run_phases/item-{n}/` using the same
`{node_id}/attempt-{n}/output.md` convention as every other node. On-disk idempotency only treats
a nested `04_success` receipt as done — a paused item is re-entered, not skipped. After implement,
review is dispatched only when the phase `review` field requires it (`always`, `if_substantial`
when the diff is over threshold, never skipped). After all phases, `additional_test` runs the
planner's unfiltered script; one `code-writer` `integration_fix` is allowed if that script fails;
a green suite then dispatches the `final-reviewer` agent (no auto-fix on reject).

`interrupt()` requires a checkpointer (`InMemorySaver` in tests; sqlite per Run in the CLI).
