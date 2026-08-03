# agent-graph-toolkit

A small, file-based system for turning a written plan into an executable graph of AI-subagent
calls — and running that graph with full resume/retry/halt semantics — without any external
orchestration engine. The main agent (Claude Code, Cursor, or any similar tool) *is* the runtime:
it reads a `graph.md` file and drives execution itself.

Originally built inside a private project, using patterns adapted from the `mattpocock-skills`
Claude Code plugin (task sizing, code review structure, test quality, research discipline). This
repo extracts the reusable core plus one fully generic worked example.

## What's here

```
skills/
  define-graph/       # turns a written plan into a graph.md file (spec: GRAPH-SPEC.md)
  run-graph/           # executes a graph.md — dependency order, branching, retry, redrive
  vertical-slice-tasks/      # how to size/sequence a plan's task breakdown
  code-review-standards/    # two-axis (Standards vs Spec) review structure
  test-quality-bar/          # what makes a test worth keeping
  research-primary-sources/  # citation discipline for research subagents
agents/
  planner.md               # spec + tech plan + task breakdown
  tech-plan-reviewer.md    # adversarial plan review, Approve/Reject
  researcher.md             # read-only research (external docs or codebase)
  code-writer.md            # implements a well-specified change
  reviewer.md               # read-only code review, two-axis
examples/feature-pipeline/
  graphs/feature-kickoff/   # branch -> plan -> review -> fan out tasks -> final regression check
  graphs/standard-task/     # per-task subgraph: environment check -> implement -> review
```

`skills/` and `agents/` are the generic engine — domain-agnostic, no assumptions about your stack.
`examples/feature-pipeline/` is a worked example showing the mechanics in action (branches, retry,
loop-back attempt limits, a cheap gating check pinned to a fast model, map-of-subgraphs) with
placeholder "build/test environment" and "test suite" steps — swap those for whatever your actual
project's tooling is.

## Using it with Claude Code

Claude Code reads skills from `.claude/skills/{name}/SKILL.md` and subagents from
`.claude/agents/{name}.md`. Copy (don't symlink) what you want into your project:

```
your-project/.claude/skills/define-graph/    <- from skills/define-graph/
your-project/.claude/skills/run-graph/       <- from skills/run-graph/
your-project/.claude/agents/planner.md       <- from agents/planner.md
...
```

## Using it with Cursor

Cursor 2.4+ has the same two mechanisms at different paths: `.cursor/skills/{name}/SKILL.md` and
`.cursor/agents/{name}.md`. The frontmatter shape is close but not identical — Cursor's agent
files use `model: inherit|fast|<id>` and `readonly: true` instead of the prose-only "you do not
modify files" convention used here. Copy the same content over and adjust frontmatter as needed.

Either way, this repo intentionally does not ship `.claude/` or `.cursor/` folders itself — that
wrapping belongs to whatever project consumes this content, not to the toolkit.

## Format reference

`skills/define-graph/GRAPH-SPEC.md` is the single source of truth for the `graph.md` file format:
node types (`leaf`/`map`/`subgraph`), the YAML schema, the `runs/` folder layout, and every
convention (result-line branching, retry idempotency, map-of-subgraphs invocation context, dynamic
subgraphs via `ref_from`). `skills/run-graph/SKILL.md` is the step-by-step execution algorithm,
including the three halt reasons (`unresolved_branch`, `retries_exhausted`, `capability_gap`) and
redrive (resuming a halted run after a human fixes the cause).

## License

MIT — see `LICENSE`.
