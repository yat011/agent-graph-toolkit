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

## Quick example

`run-graph.js` is a stateful CLI: each call reads `run-state.json`, computes the next step, writes
it back, and prints one JSON object. A minimal loop against the bundled `standard-task` template:

```
$ node skills/agentgraph-run-graph/run-graph.js resolve-run --graph standard-task
{"schemaVersion":1,"status":"ready","mode":"new","run_path":"agent_works/graphs/standard-task/runs/standard-task_20260804T160620"}

$ node skills/agentgraph-run-graph/run-graph.js next --run agent_works/graphs/standard-task/runs/standard-task_20260804T160620
{"schemaVersion":1,"status":"dispatch","node_id":"01_check_environment","node_type":"leaf","attempt":1,
 "output_path":"agent_works/graphs/standard-task/runs/standard-task_20260804T160620/01_check_environment/attempt-1/output.md",
 "agent":"general-purpose","model":"haiku","prompt":"...","has_branches":true,"is_redrive":false,
 "copied_templates":["standard-task"]}

$ node skills/agentgraph-run-graph/run-graph.js status --run agent_works/graphs/standard-task/runs/standard-task_20260804T160620
{"schemaVersion":1,"status":"running","total_executions":1,"halt_reason":null,
 "nodes":{"01_check_environment":{"status":"running","attempt":1,"branch_decision":null}}}
```

The caller (a skill or agent) dispatches `prompt` to `agent` on `model`, writes the result to
`output_path`, then reports back via `record-result` / `record-branch` and calls `next` again — see
`skills/agentgraph-run-graph/CLI-CONTRACT.md` for every command and response shape.

## Format reference

`skills/agentgraph-define-graph/GRAPH-SPEC.md` is the single source of truth for the `graph.md` file format:
node types (`leaf`/`map`/`subgraph`), the YAML schema, the `runs/` folder layout, and every
convention (result-line branching, retry idempotency, map-of-subgraphs invocation context, dynamic
subgraphs via `ref_from`). `skills/agentgraph-run-graph/SKILL.md` is the step-by-step execution algorithm,
including the three halt reasons (`unresolved_branch`, `retries_exhausted`, `capability_gap`) and
redrive (resuming a halted run after a human fixes the cause).

## License

MIT — see `LICENSE`.
