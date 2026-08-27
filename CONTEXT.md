# agent-graph-toolkit

A set of Claude Code skills and agent prompts for turning a plan into a runnable multi-step
workflow and executing it with minimal per-step coordinator overhead.

## Language

**Graph**:
An executable workflow — a LangGraph `StateGraph` (plain Python) defining Nodes and the edges
between them. The toolkit's core authorable unit.
_Avoid_: Workflow, pipeline.

**Node**:
One step in a Graph — a Python function that either dispatches a Worker via an Executor, or
applies plain-code branching/fan-out logic with no Worker involved.

**Worker**:
The headless CLI subprocess invocation that carries out one Node's actual task (planning,
implementing, reviewing, etc.). Stateless per call — never `--resume`d; a Node's own file-based
context (paths passed in its prompt) plus the Graph's checkpointer state are the only continuity
mechanism across attempts.
_Avoid_: Subagent (ambiguous with the Coordinating agent's own subagent-dispatch), executor.

**Worker CLI**:
The Worker CLI a Run uses for every Worker dispatch: `claude`, `grok`, `cursor`, or `grok-orca`.
`grok-orca` is a Worker CLI identity — inner TUI is still Grok; Orca hosts the pane.
Each engine process resolves it once as `--cli` > user setting > `claude`, then keeps that
value in memory for every dispatch until the process exits. A later `resume`/`redrive` is a
new process and resolves again. The checkpointed value is not an input to selection.
_Avoid_: Adapter, Executor (Executor is the in-process subprocess seam, not the vendor).

**Executor**:
The pluggable piece of code a Node calls to dispatch work to a Worker via one specific mechanism.
Swapping executors (e.g. real subprocess → a test fake) never requires changing a Graph, because
a Node is just a Python function calling another Python function.

**Run**:
One execution instance of a Graph, identified by a run id (also the checkpointer's `thread_id`),
with its own checkpoint database and folder under `agent_works/{graph_name}/runs/{run_id}/`.

**Template graph**:
One of the built-in, reusable Graphs shipped inside a skill's own `templates/{name}/graph.py`
(currently `feature-kickoff`, `standard-task`). Loaded dynamically by name at run time — never
copied into a project.

**Coordinating agent**:
The Claude Code session that invokes a skill to start, resume, or redrive a Run. Distinct from a
Worker, and — unlike the retired JS engine — does not drive Node-by-node dispatch itself once a
Run starts; the Graph engine (a Python process) owns that.

**Role prompt**:
The stable craft and identity of one Worker role. Prefixed onto every dispatch of that role, and
also usable as a Claude Code agent. Distinct from the Coordinating agent.
_Avoid_: Agent definition, persona, instruction, system prompt.

**Work order**:
The per-dispatch brief a Node gives a Worker: what to read, the `Result:` line that Node's router
matches, and this-run context. Contains no role craft.
_Avoid_: Instruction, node prompt.

**Monitor**:
A read-only view onto one or more Runs' current state. Never mutates a Run — acting on a paused
Run (redrive) stays on the existing CLI.

**Fleet**:
The set of Runs a Monitor is currently showing. Defaults to every Run that is not yet Completed;
Completed Runs remain inspectable on request.

**Run status**:
The five states a Run is shown in — Running, Paused-awaiting-redrive, Blocked, Failed, Completed.
Running and Completed are inferred purely from whether the Run has pending Nodes left to execute:
the toolkit has no separate signal for "a process is touching this Run right this instant," so
Running means "not finished," not "actively executing this instant." Paused-awaiting-redrive marks
a halted Run that only a human's `redrive` can resume. Blocked marks a halted Run that resolves
itself as other work completes (e.g. an unmet-dependency wait), needing no `redrive`. Failed marks
a halted Run whose Outcome recorded failure.
_Avoid_: "active"/"live" alone — name which of the five states, since none of them mean "a process
is currently executing this."
