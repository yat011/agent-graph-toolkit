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

07_blocked_plan_rejected   (terminal — needs human)
08_needs_manual_review      (terminal — needs human)
09_success                  (terminal — recap)
```

Full feature-delivery graph: a grilled spec → branch → reviewed tech plan → machine-readable
task list → `standard-task` (one subgraph run per task) → unfiltered suite check.

The spec is already approved ground truth from a grilling session under `agent_works/specs/`.
This graph does not draft or rewrite a spec.

Swap the environment-check and test-suite specifics in `04_load_tasks` / `06_final_review` /
`standard-task` for whatever this project's build/test tooling actually is.

## 01_create_feature_branch

```yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
model: cheap
```

Read the feature spec for this run: if the invoker of this graph specified a path, use it;
otherwise read the most recently modified file under `agent_works/specs/` (the drop location
for a spec written after a grilling session). If none exists, do not guess — write `output.md`
stating that no spec was found and stop here.

Derive a short kebab-case slug from the spec's subject/title.

Check the current branch name and `git status` first, before running any git command that
changes state:
- If the current branch is already `feature/{slug}`, a prior attempt already completed this step
  (e.g. this is a technical-failure retry after a crash post-checkout) — treat it as already done,
  do not run `git checkout -b` again.
- Otherwise, check for uncommitted changes **outside `agent_works/graphs/`** — e.g.
  `git status --short -- . ':(exclude)agent_works/graphs/'`. Exclude that path because
  `agentgraph-run-graph` itself writes this run's `run-state.json`/`output.md` files as the run
  progresses, before this node's dispatch even happens — on a project where `agent_works/graphs/`
  is git-tracked, that means this run's own bookkeeping is *always* freshly modified/untracked at
  this point, which is expected and not a sign of uncommitted human work. If anything is
  uncommitted outside that path, do not touch it or switch branches — write `output.md` explaining
  the working tree is dirty (listing exactly which files, from the unfiltered `git status`) and
  stop.
- Otherwise, if a branch named `feature/{slug}` already exists (e.g. left over from a prior
  attempt) check it out with `git checkout feature/{slug}` (no `-b`). Only use
  `git checkout -b feature/{slug}` when that branch doesn't exist yet. Never force anything, never
  touch remotes.

Write `output.md` containing: the spec file path, the derived slug, and the branch name (whether
newly created or already in place).

## 02_planner

```yaml
deps: [01_create_feature_branch]
type: leaf
retry: 1
agent: planner
```

Produce a **tech plan + task breakdown only** — not a spec. The input spec named by
`01_create_feature_branch`'s latest `output.md` is already approved ground truth from a grilling
session: read it in full, but do not rewrite, restate, or re-derive it. Your job is turning it
into an implementation plan and tasks, not re-litigating what it decided.

Follow `agentgraph-vertical-slice-tasks` for task sizing (cut vertical, prefactor first,
dependencies as blocking edges).

**Do not run your own agent definition's `## 1. Research` step in this context** — specifically,
do not dispatch the parallel `researcher` subagents (external or codebase) it describes. External
documentation for this feature was already the grilling session's job before the spec was
approved; re-researching it here is redundant spend. If, while turning the spec into tasks, you
hit a genuinely unseen gap (an API/library detail the spec doesn't cover and this repo has no
existing pattern for), look it up yourself directly — do not spawn a subagent for it — and cite
the URL. Do not open-ended-explore. Do not use the web to rediscover in-repo facts — those come
from CBM (`search_graph` / `trace_path` / `detect_changes`) when connected, and from reading files
in this repo.

**Trust rule (applies to every codebase claim in the spec you rely on to build the plan, not just
ones already cited):** a claim is trusted without re-reading only if the spec's `## Verified
Evidence` section has an entry citing it (`path:line @ <commit-hash> — <claim>`) that passes both
checks — the cited path has no uncommitted local changes, and `git diff <commit-hash> HEAD --
<path>` is empty (treat a `git diff` that errors outright, e.g. the hash no longer exists in this
repo's history, the same as a failed check). A citation of the form `path:line @ uncommitted` never
passes — always re-verify it directly. Any claim you rely on that isn't covered by a passing
citation — including every claim in a spec with no `## Verified Evidence` section at all — must be
verified by reading that file yourself before you build the plan around it. Scope your own
research to what the tech plan itself needs beyond what the spec already establishes: sequencing,
which existing modules/tests to touch, concrete task boundaries, and test cases per task — not
re-verifying facts already covered by a passing citation.

**Code index (CBM):** Prefer CBM. Read `agent_works/INDEX.md` first. CBM is the structural *code*
graph. Process rules are `CLAUDE.md` / `AGENTS.md` and the current spec/plan — do not
create `agent_works/memory/`. If INDEX says `CBM: connected` (or a ping of `list_projects` /
`index_status` succeeds), send structural questions to CBM first (`search_graph` / `trace_path` /
`detect_changes`). After you write source files, do not trust a pre-write index for those files —
`detect_changes` or re-read them. If INDEX says `CBM: missing` or the tools do not exist, ping
once; if still down, write `CBM: missing` plus one line of evidence into `INDEX.md` and a
**warning** in `output.md`, then continue with targeted file reads. Do not stop. Never grep
`Library/`, `PackageCache/`, `Temp/`, `ThirdParty/` unless the task names that path.

If a previous attempt of `03_tech_plan_reviewer` already exists in this run
(`../03_tech_plan_reviewer/attempt-*/output.md` relative to this node's run folder), read its
latest rejection reasons in full and revise the plan and task list to explicitly address every one
of them — do not just resubmit the same plan unchanged. Also read this same node's own immediately
preceding `attempt-{N-1}/output.md` (sticky-research convention) — treat the file paths, line
numbers, and other facts you already established there as still valid unless the rejection
specifically contradicts them, rather than re-running the full research pass from scratch. Scope
fresh research to exactly what the rejection's findings require re-verifying.

Write the tech plan under `agent_works/plans/{feature-slug}.md`, starting with a `Spec:
agent_works/specs/{slug}.md` line pointing at the input spec (so a reader opens both) followed by
the tech plan and task breakdown (tasks with at least 3 test cases each, reasonable size) — do not
duplicate the spec's own content inline. Run this full pipeline only on a fresh (non-retry)
attempt; a retry attempt instead revises the existing plan/tasks per the sticky-research scoping
above, still writing the result to the same paths.
Also write the same task list out as machine-readable JSON at
`agent_works/plans/{feature-slug}.tasks.json` — a JSON array of objects with `id`, `title`,
`description` (≤ 800 characters), `test_cases` (array), `dependencies` (array of other task ids),
and optional `kind` (`implement`|`verify`|`mechanical`), `test_scope`, `full_suite` (boolean,
default false; at most one true per batch) fields, mirroring the plan's Tasks section exactly.
Write/refresh `agent_works/INDEX.md` (paths and skill names only). This stable, non-attempt-scoped
path (alongside the plan file itself) is what `04_load_tasks` reads directly — it must stay in
sync with the plan's Tasks section on every revision, including loop-back retries. End `output.md`
with the plan file path and the tasks JSON file path, each on their own line.

## 03_tech_plan_reviewer

```yaml
deps: [02_planner]
type: leaf
retry: 0
agent: tech-plan-reviewer
branches:
  - condition: "the review's Result line is Approve"
    next: 04_load_tasks
  - condition: "the review's Result line is Reject, and 02_planner has already been attempted 3 times in this run"
    next: 07_blocked_plan_rejected
  - condition: "the review's Result line is Reject, and 02_planner has been attempted fewer than 3 times in this run"
    next: 02_planner
  default: 07_blocked_plan_rejected
```

Read `02_planner`'s latest `output.md` for the plan file path and tasks JSON file path, then read
both in full, plus the spec they reference (its `Spec:` line). Follow
`agentgraph-vertical-slice-tasks` when judging task size and sequencing.

Scope this review to the **plan and tasks**, not the spec's decisions themselves (don't re-litigate
*what* the spec chose to do). But the spec's underlying *codebase claims* are only exempt from your
adversarial fact-checking where they're covered by a passing citation: apply the same trust rule
`02_planner` used — a claim is trusted only if `## Verified Evidence` cites it
(`path:line @ <commit-hash>`) and that citation still passes (no uncommitted changes to the path,
`git diff <commit-hash> HEAD -- <path>` empty and not erroring; `@ uncommitted` citations never
pass). Fact-check every claim the plan depends on that isn't covered by a passing citation —
including every claim in a spec with no `## Verified Evidence` section at all — the same way you'd
adversarially fact-check a plan's own claims. When you verify such a claim yourself, append it to
the spec file's `## Verified Evidence` section using the same citation format (creating the
section if it doesn't exist), additively, without disturbing existing entries.

**VE scope:** pin only paths a task will edit, or that a task's description / `test_scope`
explicitly names. Do not cite `Library/`, `PackageCache/`, `Temp/`, or `ThirdParty/` unless
a task names that path. Skip files that only explain *why* a claim is true if no task will
touch them.

Beyond that: does the plan actually implement what the spec asks (no drift, no silently dropped
requirement), are the tasks well-scoped and adequately tested, and are the plan's *own* new claims
(proposed files/modules to touch, sequencing, feasibility) correct — fact-check those directly too.

Do not use `web_search`, `open_page`, `web_fetch`, or spawn a researcher / explore agent.
Fact-check against CBM (if connected) and files in this repo only. CBM missing is a warning,
not a stop.

Perform your standard adversarial review of the plan and every task in it, cross-checking that the
tasks JSON faithfully mirrors the plan's Tasks section (same tasks, same test cases, same
dependencies — nothing dropped or invented), and form your usual
`Verdict: Approve` or `Verdict: Reject — <one-line reason>` conclusion. Then end `output.md` with
that same conclusion restated as this graph's required single-line `Result:` line — exactly
`Result: Approve` or `Result: Reject — <one-line reason>` — immediately after your `Verdict:` line,
so both your own convention and this graph's branching convention are satisfied.

## 04_load_tasks

```yaml
deps: [03_tech_plan_reviewer]
type: leaf
retry: 1
agent: general-purpose
model: cheap
branches:
  - condition: "task list loaded and environment working"
    next: 05_run_tasks
  - condition: "the project's build/test environment is not available/responding"
    next: 08_needs_manual_review
  default: 08_needs_manual_review
```

Verify the project's build/test environment is working **once, for the whole batch** — whatever
that means concretely for this project (a CLI command, an MCP tool, a CI status check, a running
dev server). This replaces each individual task's own environment check: `05_run_tasks`'s per-item
context (below) tells each `standard-task` invocation to trust this result instead of
re-checking. If the environment is not working, do not attempt to load the task list — end
`output.md` with `Result: environment not working` and stop.

If the environment is working, prefer CBM. Call `list_projects` and/or `index_status`.
Write `CBM: connected` or `CBM: missing` plus one line of evidence into `agent_works/INDEX.md`
(create or update the CBM section). If CBM is missing, empty, or this project is not indexed,
record a **warning** in `output.md` and continue — do not stop.

Then read `02_planner`'s latest `output.md` for the tasks JSON file path — by construction
this is the attempt `03_tech_plan_reviewer` approved (a rejection produces a new `02_planner`
attempt rather than reaching this node, so the latest attempt is always the approved one) — and
read that JSON file. Each task is expected to have at least: `id`, `title`, `description`,
`test_cases` (array), and `dependencies` (array of other task ids, may be empty). Optional:
`kind`, `test_scope`, `full_suite`. Copy those through unchanged.

Write the task list to `items.json` in this node's attempt folder as a JSON array (copy it
verbatim from the file found above). End `output.md` with a one-line summary of how many tasks
were loaded (and a CBM warning if applicable), then a single-line `Result: environment working`
conclusion.

## 05_run_tasks

```yaml
deps: [04_load_tasks]
type: map
map_over: 04_load_tasks
ref: standard-task
retry: 0
```

Requirements: {{item.title}} — {{item.description}}

Test cases to cover (at minimum): {{item.test_cases}}

kind: {{item.kind}}
test_scope: {{item.test_scope}}
full_suite: {{item.full_suite}}
dependencies: {{item.dependencies}}

## 06_final_review

```yaml
deps: [05_run_tasks]
type: leaf
retry: 0
agent: general-purpose
model: cheap
branches:
  - condition: "every task in 04_load_tasks's items.json has a matching item run that reached standard-task's 04_success terminal node, and the full test suite passed with no failures"
    next: 09_success
  - condition: "any task was skipped, any item run ended at standard-task's 05_manual_flag (or never reached 04_success), or the test suite has failures"
    next: 08_needs_manual_review
  default: 08_needs_manual_review
```

Quick final check — confirm nothing was skipped, then run the unfiltered suite (or reuse).

1. Read `04_load_tasks`'s latest `items.json` in this run's folder for the full expected task
   list. For every task in it, confirm a corresponding
   `05_run_tasks/attempt-1/item-{index}/attempt-*/run-state.json` exists and its nested
   `standard-task` run reached the `04_success` terminal node. Flag, by task id/title, any task
   with no matching item folder (skipped entirely) or whose nested run ended anywhere other than
   `04_success` (e.g. `05_manual_flag`, or incomplete).
2. Unfiltered suite (once per product SHA). Reuse recorded **unfiltered** counts — do not run
   the suite again — when `git status` shows no product/test changes since that run **and**
   one of: (a) a `full_suite: true` item already recorded unfiltered green counts, or (b) an
   earlier `06_final_review` attempt in this run already recorded them. Do **not** treat scoped
   `kind: verify` counts as a full suite. Otherwise run the project's full automated test suite
   with no filtering. Report pass/fail/skip counts and failing names.

End `output.md` with: a per-task checklist (task id/title → completed / skipped / failed), the
test run summary, and a single-line `Result: passed` or
`Result: needs manual review — <short reason>` conclusion.

## 07_blocked_plan_rejected

```yaml
deps: [03_tech_plan_reviewer]
type: leaf
retry: 0
agent: general-purpose
model: cheap
```

The plan/task-list review loop exhausted 3 attempts without reaching approval, so there is no task
list to run. Read every `02_planner/attempt-*/output.md` and `03_tech_plan_reviewer/attempt-*/
output.md` in this run's folder to see the full history of what was tried and why each attempt was
rejected. Write `output.md` summarizing: the latest plan file path, the latest tasks JSON file path
if produced, and a concise list of the unresolved rejection reasons, so a human can take over from
here. Do not attempt further revisions yourself. Also save this summary as a manual follow-up
checklist under `agent_works/manual_actions/`, if this project uses that convention.

## 08_needs_manual_review

```yaml
deps: [04_load_tasks]
type: leaf
retry: 0
agent: general-purpose
model: cheap
```

Reachable two ways: directly from `04_load_tasks` (the build/test environment was down before any
task ran — no `06_final_review` output exists yet), or via `06_final_review`'s `issues found`
branch. Check which applies: if `06_final_review/attempt-*/output.md` exists in this run's folder,
read it (and, if useful, the per-item outputs under `05_run_tasks`) and write `output.md`
summarizing why this batch run needs manual attention (skipped/failed tasks, and/or failing
tests). Otherwise, read `04_load_tasks`'s latest `output.md` and write `output.md` summarizing
that the environment wasn't reachable before any task could run. Either way, also save
this summary as a manual follow-up checklist under `agent_works/manual_actions/`, if this project
uses that convention.

## 09_success

```yaml
deps: [06_final_review]
type: leaf
retry: 0
agent: general-purpose
model: cheap
```

Write a **Recap** a human can read without opening the rest of the run. Every task
reached `04_success` and `06_final_review` recorded `Result: passed`.

1. Read `06_final_review`'s latest `output.md` for the per-task checklist and suite
   pass/fail/skip counts.
2. Read `01_create_feature_branch`'s latest `output.md` for the spec path and branch.
3. Read `02_planner`'s latest `output.md` for the plan path and tasks JSON path.
4. Collect **additional docs this run produced or cited** — do not dump unrelated older
   checklists:
   - `agent_works/manual_actions/` files named for this feature slug, or that an
     implement/review `output.md` in this run linked
   - `agent_works/summary/` files for this slug, if any

Write `output.md` with these headings:

- `## Recap` — one or two short paragraphs: slug, what shipped (task titles from the
  06 checklist), suite result
- `## Docs` — repo-relative links to the spec, plan, and tasks JSON
- `## Follow-up` — repo-relative links to each additional doc from step 4; if none,
  write `None.`

End with `Result: recap written`.
