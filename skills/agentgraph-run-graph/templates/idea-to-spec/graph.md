```
01_draft_spec ──► 02_review_spec ──[Reject, attempts remaining]──► 01_draft_spec
                        │
                        ├──[Approve]───────────────────────────► 03_success
                        └──[Reject, 3rd attempt exhausted]─────► 04_manual_flag
```

Worked example of a lightweight idea-to-spec graph: turn a freeform idea into a written spec via
synthesis (no interview — there's no user in the loop during an autonomous run), review it
adversarially against the actual codebase, and loop the draft/review pair up to 3 times before
giving up and flagging for a human. Illustrates a bounded two-node retry loop and the
attempt-counting convention a `branches` condition needs to self-limit. Feed the graph an idea as
part of its invocation (a feature request or problem description) — the resulting spec lands under
`agent_works/specs/`, ready for a downstream graph like `feature-kickoff` to pick up.

## 01_draft_spec

```yaml
deps: []
type: leaf
retry: 1
agent: planner
```

You will be given an **idea** (a feature request or problem description) as part of this
invocation's context — that idea is the seed for the spec you write. There is no user present to
answer follow-up questions; do not interview anyone.

**First, check whether this is a fresh draft or a revision pass:** look inside this node's own
directory for any earlier `attempt-N/output.md` (N < your current attempt). If one exists:

- This is a **revision pass**, not a fresh draft. Read that earlier `output.md` to find the exact
  spec file path (its `Spec:` line). Also read `02_review_spec`'s latest `attempt-N/output.md` for
  the reviewer's rejection findings.
- Read the existing spec file at that path and **amend it in place** to address every finding the
  reviewer raised. Do not create a differently-named file, do not start over from scratch, and do
  not discard sections the reviewer didn't flag as wrong. (Sticky-research convention: treat facts
  already established in the prior attempt as still valid unless the rejection specifically
  contradicts them — do not repeat the fresh-draft exploration below from scratch; scope any new
  exploration to exactly what the rejection's findings require re-verifying.)

If no earlier attempt exists, this is a **fresh draft**:

- Explore the repo to understand the current state of the codebase relevant to the idea. Use the
  project's own domain vocabulary throughout, and respect any ADRs in the area you're touching.
- Sketch the seam(s) at which this feature would be tested. Prefer existing seams to new ones; use
  the highest seam possible; the fewer seams, the better (ideally one). There is no user to confirm
  this choice with — make the call yourself and document your reasoning directly in the spec's
  Testing Decisions section instead of asking.
- If information needed to write a confident spec is genuinely missing from the codebase and can't
  be inferred, do not block — make the most reasonable documented assumption and flag it explicitly
  under "Further Notes" as an assumption a human should double-check, rather than leaving a gap.

**Write the spec** (this template is adapted from the `mattpocock-skills` `to-spec` skill, modified
for this project: write to this project's own spec folder, never touch or publish to any issue
tracker, and skip any ticket/triage-label step entirely — this project doesn't use one):

<spec-template>

## Problem Statement

The problem being faced, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A long, numbered list of user stories, each in the form:

1. As a `<actor>`, I want `<feature>`, so that `<benefit>`

This list should be extensive and cover all aspects of the feature.

## Implementation Decisions

A list of implementation decisions, which can include: modules to be built/modified, interfaces
of those modules, technical clarifications, architectural decisions, schema changes, API
contracts, specific interactions. Do NOT include specific file paths or code snippets that could
go stale quickly — reference them by name/description instead. Exception: a snippet that encodes a
decision more precisely than prose can (state machine, reducer, schema, type shape) may be inlined
if it's genuinely decision-bearing, not a working demo.

## Testing Decisions

What makes a good test for this feature (test external behavior, not implementation details),
which modules will be tested, and prior art for the tests (similar existing tests in the
codebase). Document your seam choice here explicitly, since there was no user to confirm it with.

## Out of Scope

What's explicitly not covered by this spec.

## Further Notes

Any further notes, open questions, or assumptions flagged during drafting.

</spec-template>

**File naming and location:** derive a short kebab-case slug from the idea (fresh-draft attempts
only — a revision pass reuses the exact path found in the earlier attempt's `output.md`) and write
the spec to `agent_works/specs/{slug}.md`.

End `output.md` with a one-line `Spec: agent_works/specs/{slug}.md` (so downstream nodes and future
retries can locate the file without re-deriving the slug) followed by a short summary of what the
spec covers.

## 02_review_spec

```yaml
deps: [01_draft_spec]
type: leaf
retry: 0
agent: tech-plan-reviewer
branches:
  - condition: "review result is Approve"
    next: 03_success
  - condition: "review result is Reject, and 01_draft_spec has completed fewer than 3 total attempts so far"
    next: 01_draft_spec
  default: 04_manual_flag
```

Read `01_draft_spec`'s latest attempt `output.md` to find the spec's file path (its `Spec:` line),
then read that spec file in full.

Count how many `attempt-N/` folders exist under this run's `01_draft_spec/` node directory so far
— that's the attempt number for this review cycle (1, 2, or 3).

Adversarially review the spec: fact-check every codebase claim it makes against the actual code
(don't just trust the spec's restatement — re-verify file paths, class/method/field names, and any
cited values directly), check for internal consistency between its decisions, and flag any gap that
would leave an implementer stuck without either a locked decision or an explicitly-flagged open
question.

End `output.md` with your usual explicit Approve/Reject verdict and reasoning, plus a final
single line in exactly one of these forms (per this graph's result-line convention):

- `Result: Approve`
- `Result: Reject, attempt {N} of 3` — where `{N}` is the attempt number you counted above (use
  this even on attempt 3, so the controller can route to the manual-flag terminal instead of
  looping again).

## 03_success

```yaml
deps: [02_review_spec]
type: leaf
retry: 0
agent: general-purpose
```

The spec has been approved. Read `02_review_spec`'s latest `output.md` and, through it,
`01_draft_spec`'s latest `output.md` to find the final spec's file path. Do not modify any files.

Write `output.md` confirming: the final spec's file path, that it passed review, and that it now
sits under `agent_works/specs/` ready for `feature-kickoff` auto-discovery.

## 04_manual_flag

```yaml
deps: [02_review_spec]
type: leaf
retry: 0
agent: general-purpose
```

The spec was rejected on 3 consecutive attempts and automatic retries are exhausted. Do not modify
or delete the spec file — leave it in place for a human to pick up.

Read every `01_draft_spec/attempt-N/output.md` and `02_review_spec/attempt-N/output.md` found on
disk for this run (all N), and write `output.md` summarizing: the spec's file path, a concise
consolidated list of the unresolved review findings across all attempts, and a note that this spec
needs direct human review/intervention before it can be trusted or handed to `feature-kickoff`. Do
not attempt to fix the spec yourself.
