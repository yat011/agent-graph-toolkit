# agent-graph-toolkit

Simple skill sets for graph orchestration and running graphs.

## What's here

```
skills/
  agentgraph-define-graph/       # turns a written plan into a graph.md file (spec: GRAPH-SPEC.md)
  agentgraph-run-graph/           # executes a graph.md — dependency order, branching, retry, redrive
  agentgraph-vertical-slice-tasks/      # how to size/sequence a plan's task breakdown
  agentgraph-code-review-standards/    # two-axis (Standards vs Spec) review structure
  agentgraph-test-quality-bar/          # what makes a test worth keeping
  agentgraph-research-primary-sources/  # citation discipline for research subagents
  agentgraph-run-graph/templates/feature-kickoff/   # branch -> plan -> review -> fan out tasks -> final regression check
  agentgraph-run-graph/templates/standard-task/     # per-task subgraph: environment check -> implement -> review
agents/
  planner.md               # spec + tech plan + task breakdown
  tech-plan-reviewer.md    # adversarial plan review, Approve/Reject
  researcher.md             # read-only research (external docs or codebase)
  code-writer.md            # implements a well-specified change
  reviewer.md               # read-only code review, two-axis
```

Skill folder names carry an `agentgraph-` prefix to avoid colliding with a consuming project's own
skill names; rename them back if you don't need that.

`skills/` and `agents/` are the generic engine — domain-agnostic, no assumptions about your stack.
`skills/agentgraph-run-graph/templates/` ships two worked example graphs showing the mechanics in
action (branches, retry, loop-back attempt limits, a cheap gating check pinned to a fast model,
map-of-subgraphs) with placeholder "build/test environment" and "test suite" steps — swap those for
whatever your actual project's tooling is. Consuming projects that reference `feature-kickoff` or
`standard-task` by name without a local copy get one auto-copied in from these templates on first
run (see `skills/agentgraph-run-graph/SKILL.md`).

## Installation

Copy and paste what you want into your project:

```
your-project/skills/agentgraph-define-graph/    <- from skills/agentgraph-define-graph/
your-project/skills/agentgraph-run-graph/        <- from skills/agentgraph-run-graph/
your-project/agents/planner.md                   <- from agents/planner.md
...
```

Or keep one canonical copy shared across projects and link to it instead of copying.

## End-to-end example

Two separate skills, invoked one after the other in a Claude Code session.

### 1. Define the graph

```
> define a graph for agent_works/plans/example-plan.md
```

`agentgraph-define-graph` reads the plan, breaks it into nodes, and writes
`agent_works/graphs/example-graph/graph.md` (a planner that fans out a task list, a `map` node that
implements each task, and a review node that loops back on critical issues):

```
01_planner ──► 02_per_task_impl ──► 03_review
                     ^                 │
                     └──[critical issues]
                                       ├──[passed]────► 05_success
                                       └──[no match]──► 06_manual_flag
```

It shows you that diagram plus a plain-language summary of each node and asks you to confirm or
request changes — it never runs anything itself. Once you confirm, `graph.md` is ready on disk.

### 2. Run the graph

```
> run the example-graph graph
```

`agentgraph-run-graph` drives `run-graph.js` in a loop: resolve the run, dispatch each node to the
agent/model the graph specifies, record the result, evaluate any branch, repeat.

```
$ node skills/agentgraph-run-graph/run-graph.js resolve-run --graph example-graph
{"schemaVersion":1,"status":"ready","mode":"new","run_path":"agent_works/graphs/example-graph/runs/example-plan_20260804T160620"}

$ node skills/agentgraph-run-graph/run-graph.js next --run agent_works/graphs/example-graph/runs/example-plan_20260804T160620
{"schemaVersion":1,"status":"dispatch","node_id":"01_planner","node_type":"leaf","attempt":1,
 "output_path":".../01_planner/attempt-1/output.md","agent":"general-purpose","prompt":"Break the plan into a list of independent tasks...",
 "has_branches":false,"is_redrive":false}

  # caller dispatches the prompt to an Agent, writes output.md + items.json, then:
$ node skills/agentgraph-run-graph/run-graph.js record-result --run ...example-plan_20260804T160620 --node 01_planner --outcome success
{"schemaVersion":1,"status":"ok","run_path":"...","node_status":"succeeded"}

$ node skills/agentgraph-run-graph/run-graph.js next --run ...example-plan_20260804T160620
{"schemaVersion":1,"status":"dispatch","node_id":"02_per_task_impl","node_type":"map","item":"item-1","attempt":1, ...}
  # ...repeats per item, then 03_review, evaluated via record-branch, then 05_success...

$ node skills/agentgraph-run-graph/run-graph.js status --run ...example-plan_20260804T160620
{"schemaVersion":1,"status":"complete","total_executions":6,"halt_reason":null, "nodes":{...}}
```

If a node exhausts retries, hits an unresolved branch, or self-reports a capability gap, `next`
returns `{"status":"halted", ...}` instead of `complete` — fix the cause and re-invoke
`agentgraph-run-graph` (redrive) to resume from where it stopped, rather than starting over.

See `skills/agentgraph-define-graph/GRAPH-SPEC.md` for this worked example in full (including the
`runs/` folder contents at each step) and `skills/agentgraph-run-graph/CLI-CONTRACT.md` for every
command and response shape.

## Format reference

`skills/agentgraph-define-graph/GRAPH-SPEC.md` is the single source of truth for the `graph.md` file format:
node types (`leaf`/`map`/`subgraph`), the YAML schema, the `runs/` folder layout, and every
convention (result-line branching, retry idempotency, map-of-subgraphs invocation context, dynamic
subgraphs via `ref_from`). `skills/agentgraph-run-graph/SKILL.md` is the step-by-step execution algorithm,
including the three halt reasons (`unresolved_branch`, `retries_exhausted`, `capability_gap`) and
redrive (resuming a halted run after a human fixes the cause).

## License

MIT — see `LICENSE`.
