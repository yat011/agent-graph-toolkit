# agent-graph-toolkit

Turn a written plan into a Markdown graph, then run it. You invoke the skills; the host agent does the rest.

## Key tech

This is a set of `SKILL.md` files. No orchestration framework to install or configure.

- `agentgraph-define-graph` — write a `graph.md` from a plan. It never runs anything.
- `agentgraph-run-graph` — run a graph by name. It dispatches one subagent per node, follows branches, and can resume if a run stops.

`GRAPH-SPEC.md` (next to define-graph) is the `graph.md` format, if you want to read or edit a graph by hand.

## Simple example

A grilled spec is already on disk. Ask the host:

```
> run the feature-kickoff graph
```

That loads `agentgraph-run-graph`. On first use it copies the `feature-kickoff` template into `agent_works/graphs/feature-kickoff/`, then walks:

```
01_create_feature_branch ──► 02_planner ──► 03_tech_plan_reviewer
                                             ├─[Approve]──────────────────────────► 04_load_tasks
                                             ├─[Reject, attempted 3 times]────────► 07_blocked_plan_rejected
                                             └─[Reject, attempted < 3 times]──────► 02_planner  (loop back)

04_load_tasks
 ├─[loaded, env working]─► 05_run_tasks (map: standard-task per task) ──► 06_final_review
 │                                                                                  ├─[passed]────────► 09_success
 │                                                                                  └─[issues found]──► 08_needs_manual_review
 └─[env down]──────────────────────────────────────────────────────────────────────────────────────► 08_needs_manual_review
```

A custom graph is two skill invocations:

```
> define a graph for agent_works/plans/example-plan.md
> run the example-plan graph
```

If a run stops, ask to redrive it after you fix the cause — do not start a fresh run unless you mean to abandon the old one.

## What's here

```
skills/
  agentgraph-define-graph/              # plan → graph.md
  agentgraph-run-graph/                 # run a graph.md
    templates/feature-kickoff/          # spec → plan → per-task implement/review → suite
    templates/standard-task/            # per-task subgraph used by feature-kickoff
  agentgraph-vertical-slice-tasks/      # how to size a plan's tasks
  agentgraph-code-review-standards/     # Standards vs Spec review
  agentgraph-test-quality-bar/
  agentgraph-research-primary-sources/
agents/
  planner.md               # plan + tasks (spec only if the invocation asks)
  tech-plan-reviewer.md
  researcher.md
  code-writer.md
  reviewer.md
  graph-runner.md           # drives one hand-off hop of a graph run, then re-dispatches itself
```

Templates auto-copy into `agent_works/graphs/{name}/` on first run if no local `graph.md` exists. Swap the environment-check and test-suite steps for your project's tooling.

## Install

Copy (or symlink) `skills/` and `agents/` into the host's skill and agent folders. Then talk to the host: `run the feature-kickoff graph`.

Mechanical nodes pin `model: cheap`. The host maps that to its cheapest model.

Prefer [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) when connected. Missing is a warning in `INDEX.md`, not a stop.

## License

MIT — see `LICENSE`.
