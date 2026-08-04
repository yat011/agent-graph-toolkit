```
01_check_environment
 ├─[environment working]──────► 02_implement_requirements
 └─[environment not working]──► 05_manual_flag

02_implement_requirements
 ├─[implemented]──────────────► 03_review
 └─[stopped without completing]─► 05_manual_flag

03_review
 ├─[passed]───────────────────► 04_success
 ├─[rejected, attempts < 3]───► 02_implement_requirements   (loop back)
 └─[rejected, attempts = 3]───► 05_manual_flag

04_success        (terminal — success)
05_manual_flag     (terminal — needs human)
```

Per-task subgraph, invoked via `05_run_tasks`'s map-of-subgraphs in `feature-kickoff`. Illustrates:
a cheap gating check pinned to a fast model, a skip-review-on-incomplete-implementation branch, and
a bounded retry loop between implementation and review.

## 01_check_environment

```yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
model: haiku
branches:
  - condition: "the project's build/test environment is available and responding"
    next: 02_implement_requirements
  default: 05_manual_flag
```

If your invocation context (appended below this prompt, e.g. a map-of-subgraphs `context.md` from
a batch runner like `feature-kickoff`'s `05_run_tasks` node) already states the build/test
environment was confirmed for this batch, trust that — write `output.md` noting the batch-level
confirmation and end with `Result: environment working`, without re-running the checks below,
unless that same context also names a reason to doubt it (e.g. a prior item in this batch already
reported a failure).

Otherwise (no such context, e.g. this is a standalone/direct invocation), verify the environment
yourself using whatever this project provides for that (a CLI command, an MCP tool, a CI status
check). Report what you found. End `output.md` with a single-line `Result: environment working` or
`Result: environment not working` conclusion.

## 02_implement_requirements

```yaml
deps: [01_check_environment]
type: leaf
retry: 1
agent: code-writer
branches:
  - condition: "implementation reached a complete, tested state — real changes made (or genuinely not needed), full test suite green"
    next: 03_review
  - condition: "implementation stopped without completing — missing capability/tool binding, a design decision needed, blocked by external state, or any other reason no complete tested change exists"
    next: 05_manual_flag
  default: 05_manual_flag
```

Implement the requirements for this task. The requirements text is supplied by whoever invoked
this graph run (appended below this prompt, or given directly as part of their agentgraph-run-graph
request) — if no requirements text is present anywhere in your instructions, stop and write to
`output.md` that requirements are missing rather than guessing at what to build.

If this is a retry after a previously rejected review, first read the latest
`03_review/attempt-*/output.md` in this run's folder (if it exists) for the specific issues
raised, and address those in addition to the original requirements.

Follow this project's own conventions (SOLID/DRY, no duplicate code, no defensive null checks, and
whatever file/language scope restrictions it declares).

You must build/compile and run the test suite yourself before finishing — do not hand off untested
code for `03_review` to discover failures in. Do not write `output.md` until the full test suite
passes (not just the tests you added) — report the actual pass/fail counts from your own run in
`output.md`, never an unverified claim. Only stop short of green and say so explicitly if a failure
genuinely requires a design decision beyond a mechanical fix (e.g. a production-code architecture
change), and only after you've actually run the tests and root-caused it — never as a substitute
for running them. If the project requires any manual follow-up step you can't perform yourself,
save a checklist under `agent_works/manual_actions/`, if this project uses that convention.
Summarize what you changed and why, plus the final test results, in `output.md`. There is nothing
for `03_review` to productively evaluate when no complete, tested change exists — whatever the
reason (missing capability, a genuine design ambiguity, or anything else), end `output.md` with a
single-line `Result: implemented` only when you've reached that complete, tested state; otherwise
end with `Result: stopped — <short reason>` so this routes straight to manual review instead of a
pointless review pass.

## 03_review

```yaml
deps: [02_implement_requirements]
type: leaf
retry: 1
agent: reviewer
branches:
  - condition: "review passed with no significant issues"
    next: 04_success
  - condition: "review rejected the changes, and 02_implement_requirements has already been attempted 3 times in this run"
    next: 05_manual_flag
  - condition: "review rejected the changes, and 02_implement_requirements has been attempted fewer than 3 times in this run"
    next: 02_implement_requirements
  default: 05_manual_flag
```

Read the implementation summary from the latest `02_implement_requirements/attempt-*/output.md`
in this run's folder, plus the actual changed files, and review them against this project's own
guidelines: SOLID/DRY, no duplicate code, no defensive null checks, test coverage, and correctness
against the original requirements. Accept or reject. End `output.md` with a single-line
`Result: accepted` or `Result: rejected — <short reason>` conclusion.

If and only if the result is `accepted`, stage and commit exactly the files `git status` currently
shows as modified/untracked in the working tree at that point (the current state, not a diff
against any earlier attempt's output — a loop-back retry that re-edited the same files must never
end up committing a stale rejected version alongside the current one), with commit message
`<task-id>: <title>`.

## 04_success

```yaml
deps: [03_review]
type: leaf
retry: 0
agent: general-purpose
```

Read the latest `02_implement_requirements/attempt-*/output.md` and `03_review/attempt-*/output.md`
in this run's folder. Write a short final summary to `output.md` confirming the requirements were
implemented and passed review, noting what changed.

## 05_manual_flag

```yaml
deps: [01_check_environment]
type: leaf
retry: 0
agent: general-purpose
```

This run needs manual attention. Read whichever of the following exist in this run's folder:
`01_check_environment/attempt-*/output.md`, `02_implement_requirements/attempt-*/output.md`,
`03_review/attempt-*/output.md`. Write `output.md` summarizing why the run couldn't complete
automatically (build/test environment unreachable, implementation stopped without reaching a
complete tested state, or the review kept rejecting after 3 implementation attempts) and what a
human should check next. Also save this summary as a manual follow-up checklist under
`agent_works/manual_actions/`, if this project uses that convention.
