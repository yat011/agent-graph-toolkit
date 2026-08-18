```
01_inspect_and_revise
 ├─[no evidence found, no changes made]───────────────► 03_success
 ├─[evidence found, skill(s) revised]─────────────────► 02_review
 └─[target run could not be resolved/inspected]───────► 04_manual_flag

02_review
 ├─[passed]────────────────────────────────────────────► 03_success
 ├─[rejected, attempts < 3]────────────────────────────► 01_inspect_and_revise  (loop back)
 └─[rejected, attempts = 3]────────────────────────────► 04_manual_flag

03_success        (terminal — success)
04_manual_flag     (terminal — needs human)
```

Standalone extraction of the skill-revision step originally embedded as `multi-feature-pipeline`'s
`05_skill_revision` map-of-subgraphs node. That node received "which run to inspect" via the
map-of-subgraphs `context.md` mechanism (`{{item.slug}}` plus a hardcoded nested path specific to
`multi-feature-pipeline`'s own node layout) — a mechanism only available to a node nested inside a
`type: map` fan-out, not to a standalone top-level graph. Here, the same fixed process (read a
completed run's outputs for evidence of technical exceptions, revise whichever skill(s) it points
to) is baked directly into `01_inspect_and_revise`'s prompt, and "which run to inspect" is instead
sourced from this graph's own top-level invocation context — the same pattern
`feature-kickoff`'s `01_create_feature_branch` uses for "the spec path" (freeform text given
directly as part of the `agentgraph-run-graph` request, or a documented fallback). Mirrors `standard-task`'s implement/review/success/manual-flag shape
(retry-loop, result-line convention, sticky-research convention on loop-back) rather than
reinventing it, since the underlying quality bar — a real review pass before anything gets
committed — is exactly as valuable for a skill edit as for any other code change.

## 01_inspect_and_revise

```yaml
deps: []
type: leaf
retry: 1
agent: code-writer
branches:
  - condition: "no evidence of technical exceptions/failures was found in the target run, so no skill changes were made"
    next: 03_success
  - condition: "evidence was found and one or more skill files were revised"
    next: 02_review
  - condition: "the target run could not be resolved or inspected (missing/invalid reference, run not found)"
    next: 04_manual_flag
  default: 04_manual_flag
```

You will be given, as part of this invocation's context (appended below this prompt, or given
directly as part of the `agentgraph-run-graph` request), a reference to which run to inspect — e.g.
an exact run folder path under some `agent_works/graphs/{graph-name}/runs/`, or a graph name (and
optionally a slug) to resolve to the most recently modified run folder matching it. If no such
reference is present anywhere in your instructions, do not guess — write `output.md` stating that
no target run was specified, and end with `Result: stopped — no target run specified`.

**First, check whether this is a fresh attempt or a revision pass:** look inside this node's own
directory for any earlier `attempt-N/output.md` (N < your current attempt). If one exists, this is
a **revision pass** — read `02_review`'s latest `attempt-N/output.md` for the specific rejection
reasons, and also this same node's own immediately preceding `attempt-{N-1}/output.md` (sticky-
research convention: treat the target run and evidence you already identified there as still valid
unless the rejection specifically contradicts them, rather than re-reading the entire target run
from scratch). Address only what the rejection raised. If no earlier attempt exists, proceed fresh:

1. Resolve the target run's folder from the invocation context. If given an exact path, use it
   directly. If given a graph name (± slug) instead, resolve it to the most recently modified
   folder under `agent_works/graphs/{graph-name}/runs/` whose name matches the given slug, or the
   single most recent run under that graph if no slug was given. If it doesn't resolve to an
   existing folder, end `output.md` explaining what didn't resolve and finish with
   `Result: stopped — <short reason>`.
2. Read every `output.md` file found anywhere under that run's folder, recursively — including any
   nested `map`-item or `subgraph` runs inside it — for evidence of technical exceptions,
   tool-execution failures, retries, or halts encountered along the way.
3. Scoped to skill files only (files under `.claude/skills/**`, or wherever this project keeps its
   skill files — check for a plugin-based skill cache too if relevant), nothing else in scope: if
   you find such evidence, revise/improve whichever skill(s) it points to so the same issue is
   less likely to recur. If you find no such evidence, make no changes — do not invent something to
   change.

Before editing any `SKILL.md` (new or existing), invoke the `mattpocock-skills:writing-for-agents`
skill and follow its guidance for skill structure, frontmatter, and writing conventions — do not
hand-roll the edit outside that process. This applies to every skill file touched in this step, not
just net-new skills. (If a future plugin update renames or removes `writing-for-agents`, verify the
current skill name before invoking rather than trusting this reference blindly.)

End `output.md` stating which run you inspected and either a summary of the evidence found and what
you changed, or an explicit statement that no such evidence was found and no changes were made.
Finish with exactly one of:

- `Result: no changes needed` — no evidence found.
- `Result: revised` — evidence found, one or more skill files changed.
- `Result: stopped — <short reason>` — the target run couldn't be resolved or read.

## 02_review

```yaml
deps: [01_inspect_and_revise]
type: leaf
retry: 1
agent: reviewer
branches:
  - condition: "review passed with no significant issues"
    next: 03_success
  - condition: "review rejected the changes, and 01_inspect_and_revise has already been attempted 3 times in this run"
    next: 04_manual_flag
  - condition: "review rejected the changes, and 01_inspect_and_revise has been attempted fewer than 3 times in this run"
    next: 01_inspect_and_revise
  default: 04_manual_flag
```

Read the summary from the latest `01_inspect_and_revise/attempt-*/output.md` in this run's folder,
plus the actual changed skill file(s), and review them against the guidance the
`mattpocock-skills:writing-for-agents` skill sets out for skill structure, frontmatter, and writing
conventions, plus whether the edit plausibly addresses the technical-exception evidence it cites.
Accept or reject. End `output.md` with a single-line `Result: accepted` or
`Result: rejected — <short reason>` conclusion.

If and only if the result is `accepted`, stage and commit exactly the skill files `git status`
currently shows as modified/untracked in the working tree at that point (the current state, not a
diff against any earlier attempt's output — a loop-back retry that re-edited the same files must
never end up committing a stale rejected version alongside the current one), with a commit message
summarizing which skill(s) were revised and why.

## 03_success

```yaml
deps: [01_inspect_and_revise, 02_review]
type: leaf
retry: 0
agent: general-purpose
```

Reachable two ways: directly from `01_inspect_and_revise` (no evidence found, nothing to review) or
via `02_review`'s accepted branch. Check which applies: if `02_review/attempt-*/output.md` exists
in this run's folder, read it and the latest `01_inspect_and_revise/attempt-*/output.md`, and write
`output.md` confirming which skill(s) were revised, why, and that the change passed review and was
committed. Otherwise, read `01_inspect_and_revise`'s latest `output.md` and write `output.md`
confirming no evidence of technical exceptions was found in the inspected run and no changes were
made.

## 04_manual_flag

```yaml
deps: [01_inspect_and_revise, 02_review]
type: leaf
retry: 0
agent: general-purpose
```

Reachable two ways: directly from `01_inspect_and_revise` (the target run couldn't be resolved or
inspected) or via `02_review`'s rejected-3-times branch. Read whichever of the following exist in
this run's folder: `01_inspect_and_revise/attempt-*/output.md`, `02_review/attempt-*/output.md`.
Write `output.md` summarizing why this run couldn't complete automatically and what a human should
check next. Also save this summary as a manual follow-up checklist under
`agent_works/manual_actions/`, if this project uses that convention.
