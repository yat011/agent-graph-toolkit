# Agent-graph toolkit — dependency skills and tools

Portable list. Copy this file with `GRAPH-SPEC.md` and the `agentgraph-*` skills when you
stand the toolkit up in another repo. Nothing here is a second orchestrator.

Verified 2026-08-13 on Windows + Grok Build TUI + CatHome.

## Required (toolkit itself)

| Name | Kind | Path / command | Role | How to verify |
|---|---|---|---|---|
| `agentgraph-define-graph` | skill | `~/.claude/skills/agentgraph-define-graph/SKILL.md` | Author `graph.md` | Skill loads; `GRAPH-SPEC.md` exists beside it |
| `GRAPH-SPEC.md` | spec | `~/.claude/skills/agentgraph-define-graph/GRAPH-SPEC.md` | Node schema + conventions | File present |
| `agentgraph-run-graph` | skill | `~/.claude/skills/agentgraph-run-graph/SKILL.md` | Host loop + dispatch | Skill loads |
| `run-graph.js` | CLI | `~/.claude/skills/agentgraph-run-graph/run-graph.js` | State machine | `node run-graph.js --help` |
| Node.js | runtime | `node` on PATH | Runs the CLI | `node -v` |
| Host subagent tool | host | Claude `Agent`, Grok `spawn_subagent`, Cursor subagent | Executes each leaf | Can dispatch one dummy subagent |

Without these, there is no graph.

## Required conventions (no extra install)

| Name | Kind | Where | Role |
|---|---|---|---|
| `agent_works/graphs/{name}/graph.md` | files | project | Graph defs + `runs/` |
| `agent_works/plans/*.md` + `*.tasks.json` | files | project | Plan + machine task list |
| `agent_works/specs/` | files | project | Feature specs (feature-kickoff drop location) |
| `agent_works/INDEX.md` | file | project | Router: pointers only, not essays |
| `CLAUDE.md` / `AGENTS.md` | files | project | Process rules (not a stale memory dump) |
| `agent_works/manual_actions/` | files | project | Human follow-ups |
| Evidence citation | convention | GRAPH-SPEC | `path:line @ <commit>` or `@ uncommitted` |
| Sticky-research | convention | GRAPH-SPEC | Loop-back reads prior `attempt-N` |
| `Result:` line | convention | GRAPH-SPEC | Branch judgment |

## Required — index (codebase-memory)

**Required for `feature-kickoff` / `standard-task`.** Missing, empty, or unindexed → planner / `04_load_tasks` / implementer **stop** (`Result: CBM missing` or `stopped — CBM missing`). No repo-wide grep fallback.

**Status 2026-08-13 (this machine, after install):** **connected**. Binary `0.10.3` at `%LOCALAPPDATA%\Programs\codebase-memory-mcp\codebase-memory-mcp.exe`. CatHome index `C-Users-devya-CatHome`: 23781 nodes, 58297 edges. MCP: Claude CatHome + Grok `config.toml`.

CBM is a structural *code* index. Process facts belong in `CLAUDE.md` / the current spec — not `agent_works/memory/` (removed; those files went stale).

| Name | Kind | Source | Role | How to verify |
|---|---|---|---|---|
| `codebase-memory` | skill | `~/.claude/skills/codebase-memory/SKILL.md` | When to call CBM tools | Skill file present |
| `codebase-memory-mcp` | MCP server + CLI | [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp); local clone `C:\Users\devya\code\codebase-memory-mcp`; install dir `%LOCALAPPDATA%\Programs\codebase-memory-mcp\` | Structural index (`search_graph`, `trace_path`, `detect_changes`, `index_repository`, `list_projects`, `index_status`, …) | `Get-Command codebase-memory-mcp`; `codebase-memory-mcp --version`; `cli list_projects`; cache `~/.cache/codebase-memory-mcp/{project}.db` |
| C# grammar | in binary | tree-sitter C# (vendored) | CatHome / Unity scripts | After index, `cli search_graph --name-pattern ".*CatWalkController.*"` hits `Assets/Local/Scripts/Cat/CatWalkController.cs` |

Install (already done here): local `install.ps1 --skip-config` (do not let `install` rewrite unrelated MCP configs), then add a stdio MCP entry by hand. Indexer has **no** `--exclude` flag; Unity junk is skipped via repo `.gitignore` (`Library/`, `Temp/`, `Logs/`) plus hardcoded skip dirs. `PackageCache/` lives under `Library/` so it is not walked. `config set auto_index true` is on.

**Stale if:** tools missing; `index_status` empty; `detect_changes` lists files you were about to trust; you just wrote C# and the index is pre-write. Treat stale as **not connected** — do not proceed.

## Optional — token proxy (RTK)

| Name | Kind | Path | Role | How to verify |
|---|---|---|---|---|
| RTK (Rust Token Killer) | CLI + Claude hook | `~/.local/bin/rtk.exe`; notes `~/.claude/RTK.md` | Compresses CLI output before it hits Claude context | `rtk --version`; `rtk gain` |

Grok does not use the Claude `PreToolUse` hook. Useful in Claude Code; not assumed by graph prompts.

## Optional — memory / issues (Beads)

| Name | Kind | Source | Role | How to verify |
|---|---|---|---|---|
| Beads `bd` | CLI | [gastownhall/beads](https://github.com/gastownhall/beads) | Cross-session ready-queue + `bd remember` / `bd prime` | `bd version`; `bd ready` |

Not required. Do not install Gas Town for this spec.

## Optional — methodology skills (do not replace the toolkit)

| Name | Kind | Source | What to steal |
|---|---|---|---|
| Superpowers | plugin | `obra/superpowers` (Grok: `grok plugin install superpowers@xai-official --trust`) | File-brief handoff, ledger, explicit `model` on dispatch |
| GSD Core | framework | `open-gsd/gsd-core` | Fresh-window + `STATE.md` idea (already mirrored as INDEX) |

Do not adopt their full command surfaces.

## Host-specific (per project)

| Host | Typical extras | CatHome |
|---|---|---|
| Grok Build TUI | `spawn_subagent`, optional `workflow` | This session |
| Claude Code | `Agent` tool, hooks, plugins | CatHome also used |
| Cursor | subagents | — |

### CatHome-only (not part of the toolkit)

| Name | Kind | Role | How to verify |
|---|---|---|---|
| `ai-game-developer` | Unity MCP | `tests-run`, script/asset tools | MCP connected; `tests-run` with a tiny EditMode class returns counts |
| `unity-dev-loop` | skill | Edit → compile → EditMode tests | Skill in `CatHome/.claude/skills/` |
| `unity-project-conventions` | skill | MCP wait / .cs-only edits | Same |
| `uitk-vr-review` | skill | UITK/VR review checklist | Same |
| `nodecanvas-behaviour-tree` | skill | BT edits via MCP | Same |
| `asset-organization` | skill | `Assets/Local` vs ThirdParty | Same |

When porting the toolkit, **do not copy these**. Swap `06_final_review` / implementer test commands for the new repo's runner (`pytest`, `cargo test`, …).

## Health check (paste into a new project)

```
1. node <toolkit>/run-graph.js --help
2. Host can spawn one subagent
3. agent_works/INDEX.md exists (create empty if not)
4. CBM required: list_projects + index_status ready, else INDEX `CBM: missing` and do not start 05_run_tasks
5. A scoped test command exists (not only "run everything")
```
