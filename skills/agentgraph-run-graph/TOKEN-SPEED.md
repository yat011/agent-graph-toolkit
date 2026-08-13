# Spec: Agent-graph toolkit token and speed (index, router, memory)

Portable spec for updating the **agent-graph toolkit** (`~/.claude/skills/agentgraph-*`).
CatHome is the first consumer. Copy this file plus `DEPENDENCIES.md` (in the toolkit) to reuse
the same contract in another repo.

Verified 2026-08-13 against CatHome `feature-kickoff` runs and a live probe of
codebase-memory in this Grok session.

## Problem

CatHome `feature-kickoff` runs spend tokens and wall time inside **repeated full test suites
and extra agent seats**, not in `output.md` size.

Evidence from `agent_works/graphs/feature-kickoff/runs/`:

| Run | Executions | Waste |
|---|---|---|
| `playmode-ui-coverage_…20260812T212843` | 28, 7 tasks, success | 7 implementers each required a full PlayMode suite (`standard-task` prompt). Item 7 ran PlayMode again (~1:09, 392 tests). `06_final_review` ran PlayMode (~1:09) **and** EditMode (~0:17). Seven `04_success` seats only restated the review (200–700 bytes). Plan said tasks 1–2 can run in parallel and 3–6 only need the helper; the engine walked items in order and ignored `dependencies`. |
| `playmode-test-suite-cleanup_…20260811T130757` | 26 | 3 of 7 items stopped at `05_manual_flag`. Next items did not inherit the blocker. |
| `uitk-vr-playmode-verification_…20260810T170335` | 24 | Planner + tech-plan reviewer looped 3 times (6 frontier seats). Spec on disk is 66 KB. |

Written artifacts for the Aug 12 success are ~43 KB. The cost is tool dumps (full `tests-run`)
and serial host-agent sessions.

`run-graph.js` `dispatchMap` previously walked `itemsSource` in array order and never
read `item.dependencies`. That gap is closed: unmet deps are skipped, permanently
blocked remaining items stay pending and the map completes, and a cycle halts with
`unmet_dependencies`. Parallel ready-set (multiple `dispatch` payloads per `next`) is
still later.

## codebase-memory: live check (2026-08-13, after install)

**CLI + CatHome index are connected.** MCP tools appear in a session only after the host
restarts and loads the new stdio entry. Do not recreate `agent_works/memory/` — those files
went stale because the graph never maintained them.

| Check | Result |
|---|---|
| Skill on disk | Yes — `~/.claude/skills/codebase-memory/SKILL.md` |
| Binary | Yes — `C:\Users\devya\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe` `0.10.3` |
| CLI | `list_projects` → `C-Users-devya-CatHome` 23781 nodes / 58297 edges; `index_status` `ready` |
| Query | `search_graph(name_pattern=".*CatWalkController.*")` hits `Assets/Local/Scripts/Cat/CatWalkController.cs` (Class 5–599). `PlayModeUiTestUtilities` hits `Assets/Tests/PlayMode/PlayModeUiTestUtilities.cs`. |
| Unity junk | `index_status.not_indexed.dirs` includes `Library`, `Temp`, `Logs`. `search_graph(file_pattern=".*(Library\|PackageCache).*")` = 0 hits. No indexer `--exclude` flag; `.gitignore` + hardcoded skips. |
| Claude MCP | CatHome keys in `~/.claude.json` now include stdio `codebase-memory-mcp`. `ai-game-developer` kept on `C:/Users/devya/CatHome`. Also `~/.claude/.mcp.json`. |
| Grok MCP | `[mcp_servers.codebase-memory-mcp]` in `~/.grok/config.toml`; `grok mcp list` shows it. **This session** still only has `tasks` + `voice` (started before the entry). Restart Grok to load the tools. |
| `auto_index` | `true` |

Install notes: `C:\Users\devya\CatHome\agent_works\summary\cbm-install.md`.

### History: live check (2026-08-13, pre-install)

**It was not working in this Grok session before install.** Treat that row as history.

| Check | Result |
|---|---|
| Skill on disk | Yes — `~/.claude/skills/codebase-memory/SKILL.md` (documents 14 MCP tools) |
| MCP in this session | No — only `tasks` and `voice` are connected. CatHome Grok sessions have `ai-game-developer`, not codebase-memory. |
| Binary on PATH | No — `codebase-memory-mcp` / `cbm` missing. `~/.local/bin` has `rtk.exe` etc., not the CBM binary. |
| Index cache | No — `~/.cache/codebase-memory-mcp` does not exist (`cbm_resolve_cache_dir` in `codebase-memory-mcp/src/foundation/platform.c`) |
| Claude project MCP | CatHome `mcpServers` is `ai-game-developer` only (`~/.claude.json`) |

So the skill was a **card without a server**. Nodes must not assume `search_graph` exists
when INDEX says `CBM: missing`.

Source for the product: [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
(local clone `C:\Users\devya\code\codebase-memory-mcp`). Claimed ~500 tokens vs ~80K grep
(skill line 7). Staleness tools **when the server is up**:

- `index_status` — node/edge counts; empty ⇒ re-run `index_repository`
- `detect_changes` — `git diff <base>…HEAD` plus dirty files, mapped to symbols
  (`src/mcp/mcp.c` `handle_detect_changes`)
- `config set auto_index true` + watcher — git-based incremental updates (README Auto-Index)

When CBM is down, **stop the graph**. Do not invent `agent_works/memory/` as a fallback.

## Memory can be outdated — yes. Rules

Two stores. Never trust a store that fails its check.

### 1. Codebase-memory graph (required)

Before the first structural query in a run:

1. Confirm the MCP tools exist (`list_projects` or equivalent). If missing → **stop the
   graph** (`Result: CBM missing` / `stopped — CBM missing`). Do not grep the repo.
2. `index_status`. If empty or project missing → `index_repository` on the repo root
   (exclude `Library/`, `Temp/`, `PackageCache/`, `node_modules/`).
3. `detect_changes` vs the branch merge-base. If cited files changed, do not trust old
   snippets — `get_code_snippet` / `Read` those paths again.
4. After a node writes C# / scripts, the next node that needs structure must
   `detect_changes` or re-read. An index taken before the write is stale.

### 2. Conversation / `output.md`

Do not paste prior item `output.md` into the next dispatch. Hand a **path**. Superpowers
caught a 42k-char dispatch that was 99% pasted history; the same failure mode is
controller bloat here.

## Goals

1. **Scoped tests per implementer.** Full suite runs once, at `06_final_review` (or a
   single `kind: verify` / `full_suite: true` item — never both).
2. **Router.** Cheap model on bookkeeping seats. Host honors `dependencies` and may
   dispatch independent ready map items in parallel. Skip review for `kind: verify`
   no-op items. Prefer `Result:` line over stuffing full `output.md` into the controller.
3. **Index.** `agent_works/INDEX.md` is the routing document. CBM is required.
   Never grep `Library/`, `PackageCache/`, `ThirdParty/` unless the task names that path.
4. **Memory.** No `agent_works/memory/` stash. Process rules live in `CLAUDE.md` / the
   current spec/plan. Commit-pinned citations belong in the spec/plan.
5. **Portable.** Every skill/tool this depends on is listed in
   `~/.claude/skills/agentgraph-run-graph/DEPENDENCIES.md`.

## Non-goals

- Replacing the host-agent runtime with Conductor, Ruflo, Gas Town, or GSD's 80 commands.
- Vector / embedding memory.
- Deleting `04_success` in this slice (keep it, pin a cheap model, keep the prompt tiny).
  Engine-enforced map waves are a later toolkit task.

## Solution (toolkit)

### A. `items.json` fields (additive)

Required (already): `id`, `title`, `description`, `test_cases`, `dependencies`.

Optional:

| Field | Default | Meaning |
|---|---|---|
| `kind` | `implement` | `implement` \| `verify` \| `mechanical` |
| `test_scope` | (none) | Project-specific filter (Unity: `testClass` / `testMethod`). Implementer runs **only** this. |
| `full_suite` | `false` | If true, this item may run the unfiltered suite. At most one such item per batch. |

`description` ≤ 800 characters. Essays live in the plan file; the item carries files + bullets.

### B. Test contract (`standard-task`)

- Default: compile + **scoped** tests named by `test_scope` or by the files this task owns.
- `Do not write output.md until the full test suite passes` is **removed**.
- `kind: verify` and `full_suite: true`: run the unfiltered suite, change no product files
  unless a failure has a mechanical fix in this task's file list.
- `06_final_review` remains the batch-level unfiltered suite (and must not duplicate a
  `full_suite: true` item that just ran — if that item exists and is green, final review
  reuses its counts and only re-runs if those counts are missing or the worktree changed).

### C. Model router (node `model:`)

| Seat | Model |
|---|---|
| `01_create_feature_branch`, `04_load_tasks`, `04_success`, `09_success` | cheapest available (`haiku` or host equivalent) |
| `kind: mechanical` implement | cheapest |
| Reviewer on a small test-only diff | mid |
| Planner, tech-plan reviewer, `kind: implement` | agent default / frontier |

Always pass `model` on dispatch. An omitted model inherits the expensive session default.

### D. Host duties (engine now honors map `dependencies`)

`dispatchMap` skips items whose `dependencies` are unmet and does not dispatch a blocked
item. Permanently blocked remaining items stay pending and the map completes (final review
flags missing `04_success`). A cycle halts with `unmet_dependencies`. Host still:

1. May dispatch independent ready items in parallel via the host's parallel subagent API
   (the engine still returns one `dispatch` per `next()`).
2. Judge branches from the `Result:` line first; open the full `output.md` only if the
   line is missing or ambiguous.
3. After `05_manual_flag`, write the blocker into that node's `output.md` and
   `agent_works/manual_actions/`. Do not create `agent_works/memory/` or `open-questions.md`.
4. When `kind: mechanical` and `dispatch.model` is null, pass the cheapest host model.

### E. Index

On first `02_planner` of a feature (and refresh on approve):

Write/update `agent_works/INDEX.md`:

- spec path, plan path, tasks JSON path, branch
- skill **names** to load (not bodies)
- CBM status: `connected` \| `missing`

Nodes read INDEX first. They do not ingest the spec (18–66 KB here) unless the task
cannot be done from `context.md` + named files.

### F. Planner loop

Keep the existing 3-attempt cap. Retry attempts **only revise** the existing plan/tasks
(sticky-research). Do not re-run parallel research. Third reject → `07_blocked`.

## CatHome apply (this slice)

1. Update local `standard-task` + `feature-kickoff` graphs to the new prompts.
2. Create `agent_works/INDEX.md`. Do not create `agent_works/memory/`.
3. Point `CLAUDE.md` at INDEX / scoped-test rule (short).
4. CBM is **required**. Missing → stop the graph.

## Acceptance

1. A new `standard-task` implementer prompt does not contain “full test suite” as a
   precondition for `Result: implemented`.
2. `06_final_review` is documented as the single default unfiltered suite.
3. `items.json` optional fields are in GRAPH-SPEC.
4. `DEPENDENCIES.md` lists every skill/tool and how to verify it.
5. CatHome has `INDEX.md` + updated local graphs. No `agent_works/memory/`.
6. Spec records the 2026-08-13 **pre-install** miss and the **after-install** connected
   result (CLI index + query proof). Re-check: `Get-Command codebase-memory-mcp`;
   `cli list_projects` / `cli index_status`.

## Tasks (toolkit remaining after this apply)

See `agent_works/plans/agent-graph-token-speed.tasks.json` if generated; otherwise:

1. ~~Engine: `dispatchMap` honors `dependencies` and can surface a ready set.~~
   Done — `dispatchMap` skips unmet deps, completes the map when remaining items are
   permanently blocked, halts with `unmet_dependencies` on a cycle. Parallel ready-set
   (multiple `dispatch` payloads per `next`) is still a later toolkit task.
2. ~~Install/configure CBM for Grok + Claude; index CatHome excluding Unity junk; add a
   `04_load_tasks` ping (`list_projects` / `index_status`) that records CBM status in INDEX.~~
   Done 2026-08-13 — see `agent_works/summary/cbm-install.md`. Restart the Grok host so
   this session's MCP list includes `codebase-memory-mcp`.
3. Optional: skip dispatching `04_success` when review already wrote `Result: accepted`
   (host synthesizes the folder) — only after final-review still finds `04_success`.
