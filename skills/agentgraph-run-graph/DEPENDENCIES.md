# Agent-graph toolkit — required skills and tools

Copy this file with `GRAPH-SPEC.md` and the `agentgraph-*` skills when standing the toolkit up
in another repo.

## Required (toolkit)

| Name | Kind | Path / command | Role | How to verify |
|---|---|---|---|---|
| `agentgraph-define-graph` | skill | `skills/agentgraph-define-graph/SKILL.md` | Author `graph.md` | Skill loads; `GRAPH-SPEC.md` exists beside it |
| `GRAPH-SPEC.md` | spec | `skills/agentgraph-define-graph/GRAPH-SPEC.md` | Node schema + conventions | File present |
| `agentgraph-run-graph` | skill | `skills/agentgraph-run-graph/SKILL.md` | Host loop + dispatch | Skill loads |
| `run-graph.js` | CLI | `skills/agentgraph-run-graph/run-graph.js` | State machine | `node run-graph.js --help` |
| Node.js | runtime | `node` on PATH | Runs the CLI | `node -v` |
| Host subagent tool | host | Claude `Agent`, Grok `spawn_subagent`, Cursor subagent | Executes each leaf | Can dispatch one dummy subagent |

## Required conventions (no extra install)

| Name | Kind | Where | Role |
|---|---|---|---|
| `agent_works/graphs/{name}/graph.md` | files | project | Graph defs + `runs/` |
| `agent_works/plans/*.md` + `*.tasks.json` | files | project | Plan + machine task list |
| `agent_works/specs/` | files | project | Feature specs (`feature-kickoff` drop location) |
| `agent_works/INDEX.md` | file | project | Router: paths and skill names only |
| `CLAUDE.md` / `AGENTS.md` | files | project | Process rules |
| `agent_works/manual_actions/` | files | project | Human follow-ups |
| Evidence citation | convention | GRAPH-SPEC | `path:line @ <commit>` or `@ uncommitted` |
| Sticky-research | convention | GRAPH-SPEC | Loop-back reads prior `attempt-N` |
| `Result:` line | convention | GRAPH-SPEC | Branch judgment |

Do not create `agent_works/memory/`.

## Required — structural index (codebase-memory)

Required for `feature-kickoff` / `standard-task`. Missing, empty, or unindexed → planner /
`04_load_tasks` / implementer stop (`Result: CBM missing`). No repo-wide grep fallback.

| Name | Kind | Source | Role | How to verify |
|---|---|---|---|---|
| `codebase-memory` | skill | `~/.claude/skills/codebase-memory/SKILL.md` | When to call CBM tools | Skill file present |
| `codebase-memory-mcp` | MCP server + CLI | [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | Structural index (`search_graph`, `trace_path`, `detect_changes`, `index_repository`, `list_projects`, `index_status`) | `codebase-memory-mcp --version`; `cli list_projects`; `cli index_status` |

Treat the index as stale (do not proceed on it) when tools are missing, `index_status` is empty,
`detect_changes` lists files you were about to trust, or source was just written and the index
is pre-write.

## Optional

| Name | Kind | Source | Role | How to verify |
|---|---|---|---|---|
| RTK | CLI + Claude hook | project install | Compress CLI output before it hits Claude context | `rtk --version` |
| Beads `bd` | CLI | [gastownhall/beads](https://github.com/gastownhall/beads) | Cross-session ready-queue | `bd version` |

Neither is assumed by graph prompts.

## Hosts

| Host | Subagent dispatch |
|---|---|
| Grok Build TUI | `spawn_subagent` |
| Claude Code | `Agent` |
| Cursor | subagents |

Swap `06_final_review` / implementer test commands for the consuming repo's runner.

## Health check

```
1. node <toolkit>/run-graph.js --help
2. Host can spawn one subagent
3. agent_works/INDEX.md exists (create empty if not)
4. CBM: list_projects + index_status ready, else INDEX `CBM: missing` and do not start 05_run_tasks
5. A scoped test command exists
```
