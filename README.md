# agent-graph-toolkit

Turn a written plan into a runnable LangGraph agent graph, then run it. You invoke the skills;
a Python engine — not a coordinating LLM re-reading instructions at every step — drives execution.

## Key tech

A small `uv`-managed Python package (`agentgraph_engine/`, requires Python >=3.13) plus a pair of
`SKILL.md` files. Graphs are plain [LangGraph](https://github.com/langchain-ai/langgraph)
`StateGraph` Python code — never a hand-authored markdown/YAML format, never LangGraph Platform
(the paid hosted product).

- `agentgraph-define-graph` — turns a plan into a `graph.py` (idiomatic LangGraph code, no custom
  builder DSL). It never runs anything.
- `agentgraph-run-graph` — starts/resumes/redrives a named graph via the `agentgraph` CLI. Each
  Node is a Python function that either dispatches a Worker (a headless `claude -p` subprocess
  call) or applies plain-code branching/fan-out logic.

`CONTEXT.md` is this toolkit's glossary (Graph, Node, Worker, Executor, Run, Template graph,
Coordinating agent).
`skills/agentgraph-run-graph/ENGINE-CLI.md` is the `agentgraph` CLI's command contract.

## Simple example

A grilled spec is already on disk. Ask the host:

```
> run the feature-kickoff graph
```

That loads `agentgraph-run-graph`, which starts `agentgraph_engine`'s compiled `feature-kickoff`
template (loaded dynamically from `skills/agentgraph-run-graph/templates/feature-kickoff/graph.py`
— never copied into a project) and walks:

```
create_feature_branch -> planner -> tech_plan_reviewer
                                     |-[accepted]-------------------------------> load_phases
                                     |-[rejected, attempted < 3 times]----------> planner  (loop back)
                                     |-[reject×3]-------------------------------> pause (redrive planner, reset)
                                     `-[manual / unrecognized]------------------> pause (redrive reviewer, reset)

load_phases
 |-[accepted, env working]-> pick_next_phase <-> run_one_phase (compiled standard-phase, per-item thread)
 |                              `-[all items done]--------------------------------> additional_test
 |                                                                                    |-[accepted]------------> final_reviewer ----> success
 |                                                                                    |-[rejected, fix unused]-> integration_fix -> additional_test
 |                                                                                    |-[rejected, fix used]--> pause (redrive integration_fix)
 |                                                                                    `-[manual]--------------> pause (redrive additional_test)
 |                                                                                  final_reviewer reject/manual -> pause (redrive final_reviewer)
 `-[manual, env down]---------------------------------------------------------------> pause (redrive load_phases)
```

A custom graph is two skill invocations:

```
> define a graph for agent_works/plans/example-plan.md
> run the example-plan graph
```

If a run halts, ask to redrive it after you fix the cause — do not start a fresh run unless you
mean to abandon the old one.

## What's here

```
agentgraph_engine/                    # the Python engine (uv-managed package)
  constants.py                        # shared route labels, Result: phrases, halt reasons
  routing.py                          # GateConfig + classify_gate / gate_route
  states/                             # composed per-node TypedDict records
  nodes/                              # engine-shared node functions (pause + hello_graph halted sink)
  pause.py                            # interrupt() pause helper + redrive payload
  dispatch.py                         # headless-CLI Worker dispatch + Result: line parsing
  runs.py                             # run id / checkpoint path conventions (SqliteSaver)
  graph_loader.py                     # importlib-based dynamic graph.py loading
  cli.py                              # `agentgraph` start/resume/status/redrive
  examples/hello_graph/               # minimal worked example (sequence, map, checker, 1 CLI dispatch)
skills/
  agentgraph-define-graph/              # plan -> graph.py
  agentgraph-run-graph/                 # run a graph.py via the `agentgraph` CLI
    ENGINE-CLI.md                      # the `agentgraph` CLI's command contract
    templates/feature-kickoff/graph.py  # spec -> plan -> per-phase implement / conditional review
                                        # then additional_test (+ one integration_fix) then final-reviewer
    templates/standard-phase/graph.py   # per-phase subgraph used by feature-kickoff
    templates/standard-task/graph.py    # standalone always-review subgraph (single-bug / chore)
  agentgraph-phase-sizing/              # how to size kickoff phases (sequential, context-window)
  agentgraph-vertical-slice-tasks/      # standalone standard-task sizing; not for kickoff
  agentgraph-code-review-standards/     # Standards vs Spec review
  agentgraph-test-quality-bar/
  agentgraph-research-primary-sources/
agents/
  planner.md               # plan + phases (spec only if the invocation asks)
  tech-plan-reviewer.md
  researcher.md
  code-writer.md
  reviewer.md
  final-reviewer.md        # e2e / integration gate after additional_test is green
tests/                       # pytest — dispatch, graph loading, both ported graphs, checkpoint resume
```

Templates load dynamically by name via `agentgraph_engine.graph_loader` — never copied into a
project's `agent_works/`. Swap the environment-check and test-suite steps in your own graph for
your project's tooling.

## Install

```
uv sync
```

Copy (or symlink) `skills/`, `agents/`, and `agentgraph_engine/` into the host's project. Then
talk to the host: `run the feature-kickoff graph`.

Mechanical nodes dispatch with `model="cheap"`, mapped to the CLI's cheapest available model.

Prefer [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) when connected.
Missing is a warning in `INDEX.md`, not a stop.

## License

MIT — see `LICENSE`.
