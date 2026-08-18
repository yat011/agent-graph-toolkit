```
01_create_feature_branch ──► 02_planner ──► 03_tech_plan_reviewer
                                             ├─[Approve]──────────────────────────► 04_load_tasks
                                             ├─[Reject, attempted 3 times]────────► 07_blocked_plan_rejected
                                             └─[Reject, attempted < 3 times]──────► 02_planner  (loop back)

04_load_tasks
 ├─[loaded, env working]─► 05_run_tasks (map: implement per task, no review)
 │                              │
 │                              ▼
 │                         06_batch_review
 │                              ├─[accepted]──────────────────────► 09_success
 │                              ├─[rejected, attempts < 3]────────► 10_fix ──► 06_batch_review
 │                              └─[rejected, attempts = 3]────────► 08_needs_manual_review
 └─[env down]─────────────────────────────────────────────────────► 08_needs_manual_review

10_fix
 ├─[implemented]──► 06_batch_review
 └─[stopped]──────► 08_needs_manual_review

07_blocked_plan_rejected   (terminal — needs human)
08_needs_manual_review      (terminal — needs human)
09_success                  (terminal — summary)
```

Faster feature-delivery graph than `feature-kickoff`: grilled spec → branch → reviewed
tech plan → task list → implement each task with **no per-task review** → one batch
review of the whole diff. A reject goes to a single fix node, then back to batch
review, at most 3 review attempts, then the summary node.

No external research anywhere in this graph. Codebase facts come from CBM when
connected, and from files in this repo.

The spec is already approved ground truth from a grilling session under
`agent_works/specs/`. This graph does not draft or rewrite a spec.

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
session: read it in full, but do not rewrite, restate, or re-derive it.

Follow `agentgraph-vertical-slice-tasks` for task sizing (cut vertical, prefactor first,
dependencies as blocking edges).

**No external research.** Do not use `web_search`, `open_page`, `web_fetch`, or spawn a
`researcher` / `explore` subagent. Every codebase fact comes from CBM (`search_graph` /
`trace_path` / `detect_changes`) when connected, and from reading files in this repo.

**Trust rule (applies to every codebase claim in the spec you rely on to build the plan, not just
ones already cited):** a claim is trusted without re-reading only if the spec's `## Verified
Evidence` section has an entry citing it (`path:line @ <commit-hash> — <claim>`) that passes both
checks — the cited path has no uncommitted local changes, and `git diff <commit-hash> HEAD --
<path>` is empty (treat a `git diff` that errors outright the same as a failed check). A citation
of the form `path:line @ uncommitted` never passes — always re-verify it directly. Any claim you
rely on that isn't covered by a passing citation — including every claim in a spec with no
`## Verified Evidence` section at all — must be verified by reading that file yourself before you
build the plan around it. Scope your own investigation to sequencing, which existing
modules/tests to touch, concrete task boundaries, and test cases per task.

**Code index (CBM):** Prefer CBM. Read `agent_works/INDEX.md` first. Process rules are
`CLAUDE.md` / `AGENTS.md` and the current spec/plan — do not create `agent_works/memory/`.
If INDEX says `CBM: connected` (or a ping of `list_projects` / `index_status` succeeds), send
structural questions to CBM first. After you write source files, do not trust a pre-write index
for those files — `detect_changes` or re-read them. If INDEX says `CBM: missing` or the tools do
not exist, ping once; if still down, write `CBM: missing` plus one line of evidence into
`INDEX.md` and a **warning** in `output.md`, then continue with targeted file reads. Do not stop.
Never grep `Library/`, `PackageCache/`, `Temp/`, `ThirdParty/` unless the task names that path.

If a previous attempt of `03_tech_plan_reviewer` already exists in this run
(`../03_tech_plan_reviewer/attempt-*/output.md` relative to this node's run folder), read its
latest rejection reasons in full and revise the plan and task list to explicitly address every one
of them. Also read this same node's own immediately preceding `attempt-{N-1}/output.md`
(sticky-research convention) — treat facts already established there as still valid unless the
rejection specifically contradicts them. Scope fresh investigation to exactly what the rejection
requires re-verifying.

Write the tech plan under `agent_works/plans/{feature-slug}.md`, starting with a `Spec:
agent_works/specs/{slug}.md` line pointing at the input spec followed by the tech plan and task
breakdown (tasks with at least 3 test cases each, reasonable size) — do not duplicate the spec
inline. Run this full pipeline only on a fresh (non-retry) attempt; a retry attempt revises the
existing plan/tasks per the sticky-research scoping above, still writing to the same paths.
Also write the same task list as `agent_works/plans/{feature-slug}.tasks.json` — a JSON array of
objects with `id`, `title`, `description` (≤ 800 characters), `test_cases` (array),
`dependencies` (array of other task ids), and optional `kind` (`implement`|`verify`|`mechanical`),
`test_scope`, `full_suite` (boolean, default false; at most one true per batch) fields, mirroring
the plan's Tasks section exactly.
Write/refresh `agent_works/INDEX.md` (paths and skill names only). End `output.md` with the plan
file path and the tasks JSON file path, each on their own line.

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

Scope this review to the **plan and tasks**, not the spec's decisions. Apply the same trust rule
`02_planner` used — a claim is trusted only if `## Verified Evidence` cites it
(`path:line @ <commit-hash>`) and that citation still passes. Fact-check every claim the plan
depends on that isn't covered by a passing citation. When you verify such a claim yourself, append
it to the spec file's `## Verified Evidence` section additively.

**VE scope:** pin only paths a task will edit, or that a task's description / `test_scope`
explicitly names. Do not cite `Library/`, `PackageCache/`, `Temp/`, or `ThirdParty/` unless
a task names that path.

**No external research.** Fact-check against CBM (if connected) and files in this repo only.
CBM missing is a warning, not a stop.

Perform your standard adversarial review of the plan and every task, cross-checking that the
tasks JSON faithfully mirrors the plan's Tasks section. End `output.md` with
`Verdict: Approve` or `Verdict: Reject — <one-line reason>`, then the same conclusion as
`Result: Approve` or `Result: Reject — <one-line reason>`.

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

Verify the project's build/test environment is working **once, for the whole batch**. This
replaces each task's own environment check. If the environment is not working, do not load the
task list — end `output.md` with `Result: environment not working` and stop.

If the environment is working, prefer CBM. Call `list_projects` and/or `index_status`.
Write `CBM: connected` or `CBM: missing` plus one line of evidence into `agent_works/INDEX.md`.
If CBM is missing, record a **warning** in `output.md` and continue — do not stop.

Then read `02_planner`'s latest `output.md` for the tasks JSON file path and read that JSON file.
Each task is expected to have at least: `id`, `title`, `description`, `test_cases` (array), and
`dependencies` (array of other task ids, may be empty). Optional: `kind`, `test_scope`,
`full_suite`. Copy those through unchanged.

Write the task list to `items.json` in this node's attempt folder as a JSON array (copy it
verbatim). End `output.md` with a one-line summary of how many tasks were loaded (and a CBM
warning if applicable), then `Result: environment working`.

## 05_run_tasks

```yaml
deps: [04_load_tasks]
type: map
map_over: 04_load_tasks
retry: 1
agent: code-writer
```

Implement this task only. There is no per-task review — a later `06_batch_review` node reviews
the whole batch at once. Do **not** stage or commit.

Requirements: {{item.title}} — {{item.description}}

Test cases to cover (at minimum): {{item.test_cases}}

kind: {{item.kind}}
test_scope: {{item.test_scope}}
full_suite: {{item.full_suite}}
dependencies: {{item.dependencies}}

**No external research.** Every codebase fact comes from CBM when connected and from files in
this repo. If something is not in the repo, stop with `Result: stopped — <short reason>`.

Follow this project's own conventions. Build/compile and run **this task's scoped tests** before
finishing. Scope: `test_scope` if present; otherwise only the tests for the files this task owns.
Do **not** run the unfiltered project suite unless `full_suite` is true or `kind` is `verify`.
Report actual pass/fail counts. If `kind` is `verify`, change no product files unless a failure
has a mechanical fix inside this task's file list.

Prefer CBM. If INDEX says `CBM: missing`, warn in `output.md` and continue with targeted file
reads. After you write source, `detect_changes` or re-read those files. Never grep `Library/`,
`PackageCache/`, `Temp/`, `ThirdParty/` unless the task names that path. Do not create
`agent_works/memory/`.

If the build/test environment is unavailable, or a failure needs a design decision beyond a
mechanical fix, end with `Result: stopped — <short reason>`. If the project needs a manual
follow-up you cannot perform, save a checklist under `agent_works/manual_actions/` if this
project uses that convention.

End `output.md` with exactly one of:
- `Result: verified` — `kind` is `verify`, or `full_suite` is true and you changed no product files
- `Result: implemented` — real changes made (or genuinely not needed) and scoped tests green
- `Result: stopped — <short reason>`

## 06_batch_review

```yaml
deps: [05_run_tasks]
type: leaf
retry: 0
agent: reviewer
branches:
  - condition: "the review's Result line is accepted"
    next: 09_success
  - condition: "the review's Result line is rejected, and 06_batch_review has already been attempted 3 times in this run"
    next: 08_needs_manual_review
  - condition: "the review's Result line is rejected, and 06_batch_review has been attempted fewer than 3 times in this run"
    next: 10_fix
  default: 08_needs_manual_review
```

Review **all changes from this run at once**. There is no per-task review. Do not use
`web_search`, `open_page`, `web_fetch`, or spawn extra agents.

If this is a loop-back after `10_fix`, read `10_fix`'s latest `output.md` and this node's own
immediately preceding `attempt-{N-1}/output.md` (sticky-research convention). Judge whether the
fix addressed the prior rejection; do not re-derive the whole review from scratch.

1. Read `04_load_tasks`'s latest `items.json`. For every task, confirm a corresponding
   `05_run_tasks/attempt-1/item-{index}/attempt-*/output.md` exists and its `Result:` line is
   `implemented` or `verified`. Flag any task with no folder, or `Result: stopped`.
2. Review `git status` and `git diff` of product/test files this batch owns (exclude
   `agent_works/graphs/`). Check SOLID/DRY, no duplicate framework, no new defensive null
   checks, off-limits files have zero diff, and the combined diff matches the plan/tasks.
3. Unfiltered suite (once per product SHA). Reuse recorded **unfiltered** counts when
   `git status` shows no product/test changes since that run **and** one of: (a) a
   `full_suite: true` item already recorded unfiltered green counts, or (b) an earlier
   `06_batch_review` attempt in this run already recorded them. Do **not** treat scoped
   `kind: verify` counts as a full suite. Otherwise run the project's full automated test
   suite with no filtering. Report pass/fail/skip counts.

Accept only if every expected task reached `implemented` or `verified`, the combined diff is
sound, and the unfiltered suite is green (or validly reused).

If and only if the result is `accepted`, stage and commit exactly the files `git status`
currently shows as modified/untracked **outside `agent_works/graphs/`**, with commit message
`{slug}: quick-feature` (slug from `01_create_feature_branch`'s latest `output.md`). Do not
commit on reject.

End `output.md` with a per-task checklist, the review verdict, the suite summary, and exactly
one of:

```
Result: accepted
```

or

```
Result: rejected — <short reason>
```

## 07_blocked_plan_rejected

```yaml
deps: [03_tech_plan_reviewer]
type: leaf
retry: 0
agent: general-purpose
model: cheap
```

The plan/task-list review loop exhausted 3 attempts without reaching approval, so there is no
task list to run. Read every `02_planner/attempt-*/output.md` and
`03_tech_plan_reviewer/attempt-*/output.md` in this run's folder. Write `output.md` summarizing:
the latest plan file path, the latest tasks JSON file path if produced, and the unresolved
rejection reasons. Do not attempt further revisions. Also save this summary under
`agent_works/manual_actions/`, if this project uses that convention.

## 08_needs_manual_review

```yaml
deps: [04_load_tasks]
type: leaf
retry: 0
agent: general-purpose
model: cheap
```

Reachable from `04_load_tasks` (env down), `06_batch_review` (rejected 3 times), or `10_fix`
(`Result: stopped`). If `06_batch_review/attempt-*/output.md` exists, read it (and
`10_fix` / `05_run_tasks` outputs if useful) and write `output.md` summarizing why this batch
needs a human. Otherwise read `04_load_tasks`'s latest `output.md` and summarize that the
environment wasn't reachable. Also save the summary under `agent_works/manual_actions/`, if
this project uses that convention.

## 09_success

```yaml
deps: [06_batch_review]
type: leaf
retry: 0
agent: general-purpose
model: cheap
```

Write a **summary** a human can read without opening the rest of the run.
`06_batch_review` recorded `Result: accepted`.

1. Read `06_batch_review`'s latest `output.md` for the per-task checklist, review verdict, and
   suite counts.
2. Read `01_create_feature_branch`'s latest `output.md` for the spec path and branch.
3. Read `02_planner`'s latest `output.md` for the plan path and tasks JSON path.
4. Collect additional docs this run produced or cited — `agent_works/manual_actions/` and
   `agent_works/summary/` files named for this slug, or linked from implement/review outputs.
   Do not dump unrelated older checklists.

Write `output.md` with these headings:

- `## Recap` — slug, what shipped (task titles from the 06 checklist), suite result, how many
  review attempts it took
- `## Docs` — repo-relative links to the spec, plan, and tasks JSON
- `## Follow-up` — repo-relative links to each additional doc from step 4; if none, `None.`

End with `Result: recap written`.

## 10_fix

```yaml
deps: [06_batch_review]
type: leaf
retry: 1
agent: code-writer
branches:
  - condition: "fix reached a complete, tested state"
    next: 06_batch_review
  - condition: "fix stopped without completing"
    next: 08_needs_manual_review
  default: 08_needs_manual_review
```

Fix the issues `06_batch_review` just rejected. Do **not** stage or commit — the next
`06_batch_review` accept commits.

Read the latest `06_batch_review/attempt-*/output.md` for the rejection reasons. Also read this
same node's own immediately preceding `attempt-{N-1}/output.md` if it exists (sticky-research
convention) and treat facts already established there as still valid unless the new rejection
contradicts them. Address every rejected finding. Do not reopen the plan or add scope.

**No external research.** CBM when connected, plus files in this repo. Prefer CBM; if missing,
warn and continue with targeted file reads.

Run **scoped** tests for the files you touch. Do not run the unfiltered suite. Report actual
pass/fail counts.

If you cannot complete a tested fix, end with `Result: stopped — <short reason>`. Otherwise
summarize what you changed and end with `Result: implemented`.
