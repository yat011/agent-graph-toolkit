# Dependencies

What the `agentgraph-*` skills need besides their own files.

## Runtime

- Node.js — runs `run-graph.js` (`node run-graph.js --help`)
- A host that can dispatch a subagent (Claude `Agent`, Grok `spawn_subagent`, or equivalent)

## Structural index

`feature-kickoff` and `standard-task` require [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
(`search_graph`, `trace_path`, `detect_changes`, `index_repository`, `list_projects`, `index_status`).
Missing or empty index → those graphs stop (`Result: CBM missing`).

The `codebase-memory` skill (`~/.claude/skills/codebase-memory/SKILL.md`) says when to call those tools.
