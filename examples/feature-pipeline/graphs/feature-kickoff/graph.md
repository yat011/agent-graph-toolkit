```
01_create_feature_branch ──► 02_planner ──► 03_tech_plan_reviewer
                                             ├─[Approve]──────────────────────────► 04_load_tasks
                                             ├─[Reject, attempted 3 times]────────► 07_blocked_plan_rejected
                                             └─[Reject, attempted < 3 times]──────► 02_planner  (loop back)

04_load_tasks
 ├─[loaded, environment working]─► 05_run_tasks (map: standard-task per task) ──► 06_final_review
 │                                                                                  ├─[passed]────────► 09_success
 │                                                                                  └─[issues found]──► 08_needs_manual_review
 └─[environment not working]─────────────────────────────────────────────────────────────────────────► 08_needs_manual_review

07_blocked_plan_rejected   (terminal — needs human)
08_needs_manual_review      (terminal — needs human)
09_success                  (terminal — success)
```

This is a worked example of a full feature-delivery graph: turn an approved spec into a branch, a
reviewed tech plan, a machine-readable task list, then fan the task list out to `standard-task`
(one subgraph run per task) and finish with a full-suite regression check. It illustrates the
generic graph patterns (branching, retry, loop-back attempt limits, a cheap gating check pinned to
a fast model, map-of-subgraphs) — swap the environment-check and test-suite specifics in
`04_load_tasks`/`06_final_review`/`standard-task` for whatever your own project's build/test
tooling actually is.

## 01_create_feature_branch

```yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
```

Read the feature spec for this run: if the invoker of this graph specified a path, use it;
otherwise read the most recently modified file under `agent_works/specs/` (the expected drop
location for a spec written up after stress-testing an idea, e.g. via a "grilling"-style
skill). If none exists, do not guess — write `output.md` stating that no spec was found and stop here.

Derive a short kebab-case slug from the spec's subject/title.

Check the current branch name and `git status` first, before running any git command that
changes state:
- If the current branch is already `feature/{slug}`, a prior attempt already completed this step
  (e.g. this is a technical-failure retry after a crash post-checkout) — treat it as already done,
  do not run `git checkout -b` again.
- Otherwise, if there are uncommitted changes, do not touch them or switch branches — write
  `output.md` explaining the working tree is dirty and stop.
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

Produce a spec + tech plan + task breakdown, plus the same task breakdown as a machine-readable
task list, for the feature described in the input spec named by `01_create_feature_branch`'s
latest `output.md`.

If a previous attempt of `03_tech_plan_reviewer` already exists in this run
(`../03_tech_plan_reviewer/attempt-*/output.md` relative to this node's run folder), read its
latest rejection reasons in full and revise the plan and task list to explicitly address every one
of them — do not just resubmit the same plan unchanged.

Follow your own standard process end to end (parallel research, spec, plan, tasks with at least 3
test cases each and reasonable size, write the plan under `agent_works/plans/{feature-slug}.md`).
Also write the same task list out as machine-readable JSON at
`agent_works/plans/{feature-slug}.tasks.json` — a JSON array of objects with `id`, `title`,
`description`, `test_cases` (array), and `dependencies` (array of other task ids) fields,
mirroring the plan's Tasks section exactly. This stable, non-attempt-scoped path (alongside the
plan file itself) is what `04_load_tasks` reads directly — it must stay in sync with the plan's
Tasks section on every revision, including loop-back retries. End `output.md` with the plan file
path and the tasks JSON file path, each on their own line.

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
both in full. Perform your standard adversarial review of the plan and every task in it,
cross-checking that the tasks JSON faithfully mirrors the plan's Tasks section (same tasks, same
test cases, same dependencies — nothing dropped or invented), and form your usual
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
model: haiku
branches:
  - condition: "task list loaded and the project's build/test environment is available and responding"
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

Otherwise, read `02_planner`'s latest `output.md` for the tasks JSON file path — by construction
this is the attempt `03_tech_plan_reviewer` approved (a rejection produces a new `02_planner`
attempt rather than reaching this node, so the latest attempt is always the approved one) — and
read that JSON file. Each task is expected to have at least: `id`, `title`, `description`,
`test_cases` (array), and `dependencies` (array of other task ids, may be empty).

Write the task list to `items.json` in this node's attempt folder as a JSON array (copy it
verbatim from the file found above). End `output.md` with a one-line summary of how many tasks
were loaded, then a single-line `Result: environment working` conclusion.

## 05_run_tasks

```yaml
deps: [04_load_tasks]
type: map
map_over: 04_load_tasks
ref: standard-task
retry: 0
```

The build/test environment was already confirmed working for this batch by `04_load_tasks` —
trust that and skip re-verification, unless there's independent reason to doubt it (e.g. a prior
item in this batch reported an environment failure).

Requirements: {{item.title}} — {{item.description}}

Test cases to cover (at minimum): {{item.test_cases}}

## 06_final_review

```yaml
deps: [05_run_tasks]
type: leaf
retry: 0
agent: general-purpose
branches:
  - condition: "every task in 04_load_tasks's items.json has a matching item run that reached standard-task's 04_success terminal node, and the full test suite passed with no failures"
    next: 09_success
  - condition: "any task was skipped, any item run ended at standard-task's 05_manual_flag (or never reached 04_success), or the test suite has failures"
    next: 08_needs_manual_review
  default: 08_needs_manual_review
```

Quick final check — confirm nothing was skipped, then run the full test suite.

1. Read `04_load_tasks`'s latest `items.json` in this run's folder for the full expected task
   list. For every task in it, confirm a corresponding
   `05_run_tasks/attempt-1/item-{index}/attempt-*/run-state.json` exists and its nested
   `standard-task` run reached the `04_success` terminal node. Flag, by task id/title, any task
   with no matching item folder (skipped entirely) or whose nested run ended anywhere other than
   `04_success` (e.g. `05_manual_flag`, or incomplete).
2. Run the project's full automated test suite (however this project's tooling exposes that), with
   no filtering, so every test in the project runs — not just tests touched by these tasks. Report
   the pass/fail/skip counts and the names of any failing tests.

End `output.md` with: a per-task checklist (task id/title → completed / skipped / failed), the
test run summary, and a single-line `Result: passed` or
`Result: needs manual review — <short reason>` conclusion.

## 07_blocked_plan_rejected

```yaml
deps: [03_tech_plan_reviewer]
type: leaf
retry: 0
agent: general-purpose
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
```

Reachable two ways: directly from `04_load_tasks` (the build/test environment was down before any
task ran — no `06_final_review` output exists yet), or via `06_final_review`'s `issues found`
branch. Check which applies: if `06_final_review/attempt-*/output.md` exists in this run's folder,
read it (and, if useful, the per-item outputs under `05_run_tasks`) and write `output.md`
summarizing why this batch run needs manual attention (skipped/failed tasks, and/or failing
tests). Otherwise, read `04_load_tasks`'s latest `output.md` and write `output.md` summarizing
that the build/test environment wasn't reachable before any task could run. Either way, also save
this summary as a manual follow-up checklist under `agent_works/manual_actions/`, if this project
uses that convention.

## 09_success

```yaml
deps: [06_final_review]
type: leaf
retry: 0
agent: general-purpose
```

Read `06_final_review`'s latest `output.md`. Write a short final summary to `output.md`
confirming every task from the task list was implemented, reviewed, and passed, and that the
full test suite is green.
