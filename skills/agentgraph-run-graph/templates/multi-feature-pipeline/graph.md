```
00_parse_idea_queue ──► 01_select_next_idea
                              │
              ┌───────────────┴────────────────────┐
        [queue exhausted]                    [ideas remain]
              │                                     │
              ▼                                     ▼
   09_all_ideas_complete              02_run_idea_to_spec (map: idea-to-spec)
        (terminal — success)                        │
                                                      ▼
                                        03_check_spec_approved
                              ┌───────────────────────┴────────────────────┐
                        [approved]                                 [rejected 3x]
                              │                                            │
                              ▼                                            ▼
                     04_commit_spec                            10_spec_rejected
                              │                                (terminal — needs human)
                              ▼
             05_run_feature_kickoff (map: feature-kickoff)
                              │
                              ▼
              06_check_feature_kickoff_result
              ┌────────────────┴─────────────────────┐
        [success]                          [blocked / needs review]
              │                                       │
              ▼                                       ▼
   07_skill_revision (map: standard-task)   11_feature_kickoff_blocked
              │                             (terminal — needs human)
              ▼
     08_integrate_and_merge
     ┌─────────┴──────────────┐
[merged cleanly]        [merge conflict]
     │                        │
     ▼                        ▼
(loop back to        12_merge_conflict
 01_select_next_idea)  (terminal — needs human)
```

Runs a user-specified, ordered batch of ideas (each an existing free-form markdown file under
`agent_works/ideas/`) end to end: draft+review a spec, commit it, run the full `feature-kickoff`
pipeline, best-effort revise the skill set based on any technical exceptions that pipeline hit,
then locally merge that idea's finished feature branch into one shared integration branch before
moving to the next idea. Processing is **strictly sequential** — one idea's entire pipeline
completes (or the whole run stops and flags a human) before the next idea starts — because each
idea's spec/implementation may need to react to code the previous idea already committed. This is
why `01_select_next_idea` is a single self-looping node rather than a `type: map` fan-out: a `map`
node's per-item context is fixed once, upfront, from static `items.json` data and has no way to
reflect what a *previous* item's execution actually did, which is exactly the dependency this loop
needs to carry forward.

`02_run_idea_to_spec`, `05_run_feature_kickoff`, and `07_skill_revision` are all `type: map` nodes
with `map_over: 01_select_next_idea`, whose `items.json` is always a **single-element** array. This
isn't fan-out — it's a deliberate reuse of the map-of-subgraphs `context.md` mechanism (see
`GRAPH-SPEC.md`'s "Map-of-subgraphs invocation context" convention), which is the only
engine-supported way to inject dynamic per-pass context (which idea, which integration branch,
what prior ideas already built) into a nested subgraph run. A plain `type: subgraph` node has no
such mechanism — it only inherits whatever ambient context already existed for the whole run
(nothing, here), so it can't carry per-pass state. Per that same convention, the Markdown body
under each of these three nodes' `yaml` blocks is written as the context itself (using
`{{item.field}}` placeholders), not as an instruction telling an agent to relay it — no agent ever
receives that text as a literal instruction.

Following this graph's own established convention (see `feature-kickoff`'s
`05_run_tasks` → `06_final_review` pair), the map nodes themselves carry no `branches` — a
dedicated downstream leaf node reads the map's single item result and makes the branch judgment.

Nothing in this graph ever pushes to a remote or opens a PR. The integration branch is local-only;
reviewing and pushing it is a manual step for the user once the run finishes.

## 00_parse_idea_queue

```yaml
deps: []
type: leaf
retry: 1
agent: general-purpose
model: haiku  # fallback: this host's low-cost model
```

This run's invocation should include a free-text, ordered list of idea names (e.g. `"cat-toys,
weather-system, achievement-polish"`) — appended below this prompt or given directly as part of
the invocation. If no such list is present anywhere in your instructions, stop and write
`output.md` stating that no idea list was supplied, rather than guessing.

For each name in the list, in order, resolve it to a file at `agent_works/ideas/{slug}.md` (try an
exact filename match first, then a reasonable kebab-case slugification of the given name). These
files are pre-existing — written elsewhere from individual grilling sessions — this node only
locates them, never creates or edits them. If any name fails to resolve to an existing file, do
not guess or skip it — write `output.md` listing exactly which name(s) failed to resolve, and stop
here without producing `items.json`.

If every name resolves, write `items.json` in this node's attempt folder as a JSON array of
`{slug, path}` objects, in the exact order given. End `output.md` with a one-line summary of how
many ideas were queued and in what order.

## 01_select_next_idea

```yaml
deps: [00_parse_idea_queue]
type: leaf
retry: 1
agent: general-purpose
branches:
  - condition: "every idea in 00_parse_idea_queue's queue has already been completed (integrated by a prior 08_integrate_and_merge pass)"
    next: 09_all_ideas_complete
  - condition: "one or more ideas in the queue have not yet been completed"
    next: 02_run_idea_to_spec
```

No `default:` is set deliberately: unlike this graph's other branch nodes, neither outcome here is
a safe catch-all for an ambiguous judgment — defaulting to "exhausted" risks a false success that
silently drops unprocessed ideas, and defaulting to "keep processing" risks reprocessing an idea
that was actually already done. If the judgment is ever genuinely unclear, the engine's built-in
no-match/no-default behavior (halt with `unresolved_branch`) is the safer outcome than guessing.

This node is the target of a loop-back edge from `08_integrate_and_merge` — it runs once per idea,
and its own attempt history is how progress through the batch is tracked (there is no separate
counter). Do the following, in order:

1. Read `00_parse_idea_queue`'s `items.json` for the full ordered idea queue.
2. List every earlier `attempt-N/` folder that exists under this same node's own directory in this
   run (i.e. attempts before your current one), and read each one's `output.md`. Each prior attempt
   that selected an idea ends with `Result: idea selected: {slug}` — collect that set of slugs.
   Everything in the queue not in that set is still pending.
3. Establish (or confirm) the batch's local integration branch. Check this from **actual git
   state every attempt**, not from whether prior `attempt-N/` folders exist for this node — a
   technical-failure retry could leave a prior attempt folder behind even if that earlier attempt
   crashed before actually creating the branch, so folder presence alone is not a reliable signal
   of whether the branch already exists (per the retry-idempotency convention: check real state,
   not a history proxy):
   - Derive a short kebab-case batch slug the same deterministic way every attempt: the first
     idea's slug (element 0 of `00_parse_idea_queue`'s `items.json`) plus today's date
     (`YYYYMMDD`). Call the result `feature/multi-{batch-slug}`.
   - If the current branch is already `feature/multi-{batch-slug}`: nothing further to do here.
   - Otherwise, check `git status --short -- . ':(exclude)agent_works/graphs/'` (excluded because
     `agentgraph-run-graph` itself continuously writes this run's own `run-state.json`/`output.md`
     bookkeeping under `agent_works/graphs/`, which is expected and not uncommitted human work).
     If anything else is uncommitted, do not proceed — write `output.md` explaining the working
     tree is dirty (listing exactly which files) and stop.
   - Otherwise, check whether `feature/multi-{batch-slug}` already exists as a branch (`git branch
     --list`). If it does, check it out with `git checkout feature/multi-{batch-slug}` (no `-b`).
     If it doesn't exist yet, create it with `git checkout -b feature/multi-{batch-slug}`. Never
     force anything, never touch remotes.
4. If every idea from the queue is already in the completed set: write `output.md` stating the
   queue is exhausted, naming the integration branch, and end with `Result: queue exhausted`. Do
   not write `items.json` in this case.
5. Otherwise, pick the next not-yet-completed idea in queue order. Write a **single-element**
   `items.json` (a JSON array with exactly one object) in this node's attempt folder with fields:
   `slug`, `idea_path`, `ordinal` (1-based position in the full queue), `integration_branch`,
   `prior_ideas_summary` — a short plain-text summary of what every already-completed idea in this
   batch actually built (empty string on the first idea) — and `reconciliation_note`. **Always
   include `reconciliation_note` as a key, even when empty** — the downstream template references
   `{{item.reconciliation_note}}` unconditionally, and the substitution engine renders a missing
   key as the literal text `undefined`, not as blank. Set it to `""` on the first idea (when
   `prior_ideas_summary` is empty); otherwise set it to this exact instruction text: "Check the
   current codebase state for conflicts between this idea and what prior ideas in this batch
   already built. If a conflict exists, resolve it with the minimal spec change that removes the
   conflict — do not redesign around it."
   End `output.md` naming the selected idea's slug and end with `Result: idea selected: {slug}`.

## 02_run_idea_to_spec

```yaml
deps: [01_select_next_idea]
type: map
map_over: 01_select_next_idea
ref: idea-to-spec
retry: 0
```

This idea's content is at `{{item.idea_path}}` (slug `{{item.slug}}`). This is idea #{{item.ordinal}}
in a multi-feature batch currently running on local integration branch `{{item.integration_branch}}`.

Summary of what earlier ideas in this batch have already built: {{item.prior_ideas_summary}}

{{item.reconciliation_note}}

## 03_check_spec_approved

```yaml
deps: [02_run_idea_to_spec]
type: leaf
retry: 0
agent: general-purpose
model: haiku  # fallback: this host's low-cost model
branches:
  - condition: "the nested idea-to-spec run for item-1 reached its 03_success terminal (spec approved)"
    next: 04_commit_spec
  - condition: "the nested idea-to-spec run for item-1 reached its 04_manual_flag terminal (spec rejected on 3 attempts)"
    next: 10_spec_rejected
  default: 10_spec_rejected
```

Under `02_run_idea_to_spec`'s latest attempt folder, find `item-1`'s latest nested attempt, and
check which of `idea-to-spec`'s own terminal nodes it reached: look for an `output.md` under a
`03_success/attempt-*/` folder (approved) versus a `04_manual_flag/attempt-*/` folder (rejected)
inside that nested run. Read whichever one exists.

End `output.md` stating the idea's slug, the resolved spec file path (from that terminal node's own
`output.md`), and a single-line `Result: approved` or `Result: rejected` conclusion.

## 04_commit_spec

```yaml
deps: [03_check_spec_approved]
type: leaf
retry: 1
agent: general-purpose
```

Read `03_check_spec_approved`'s latest `output.md` for the approved spec's file path and idea slug.
Run `git status --short` and confirm that spec file is the only relevant uncommitted change outside
`agent_works/graphs/` (same exclusion reasoning as `01_select_next_idea` — that path is this run's
own bookkeeping). Stage and commit exactly that spec file, with a commit message noting it's the
approved spec for idea `{slug}`.

This step exists because `feature-kickoff`'s own `01_create_feature_branch` node treats *any*
uncommitted change outside `agent_works/graphs/` as a dirty-tree blocker and refuses to proceed —
so the spec must land clean, committed on the current (integration) branch, before
`feature-kickoff` runs next.

If this step already ran in a prior attempt (the spec file shows no uncommitted changes because it
was already committed), treat it as already done rather than erroring.

End `output.md` confirming the commit (or that it was already committed) and the spec file path.

## 05_run_feature_kickoff

```yaml
deps: [04_commit_spec]
type: map
map_over: 01_select_next_idea
ref: feature-kickoff
retry: 0
```

The approved, now-committed spec for this idea (slug `{{item.slug}}`) is the most recently
modified file under `agent_works/specs/` matching that slug. Proceed exactly as this graph
normally would: branch off whatever is currently checked out (the multi-feature batch's local
integration branch, `{{item.integration_branch}}`), plan, implement, and review.

## 06_check_feature_kickoff_result

```yaml
deps: [05_run_feature_kickoff]
type: leaf
retry: 0
agent: general-purpose
model: haiku  # fallback: this host's low-cost model
branches:
  - condition: "the nested feature-kickoff run for item-1 reached its 09_success terminal"
    next: 07_skill_revision
  - condition: "the nested feature-kickoff run for item-1 reached its 07_blocked_plan_rejected or 08_needs_manual_review terminal"
    next: 11_feature_kickoff_blocked
  default: 11_feature_kickoff_blocked
```

Under `05_run_feature_kickoff`'s latest attempt folder, find `item-1`'s latest nested attempt, and
check which of `feature-kickoff`'s own terminal nodes it reached — look for an `output.md` under a
`09_success/attempt-*/` folder versus a `07_blocked_plan_rejected/attempt-*/` or
`08_needs_manual_review/attempt-*/` folder inside that nested run. Read whichever one exists.

End `output.md` stating the idea's slug, the feature branch name `feature/{slug}` that
`feature-kickoff` created, and a single-line `Result: success` or `Result: blocked` conclusion.

## 07_skill_revision

```yaml
deps: [06_check_feature_kickoff_result]
type: map
map_over: 01_select_next_idea
ref: standard-task
retry: 0
```

Your requirements: inspect the `feature-kickoff` run just completed for idea `{{item.slug}}` — read
every `output.md` under `05_run_feature_kickoff`'s latest attempt folder for `item-1`'s nested run
(including any nested per-task `standard-task` runs inside it) for evidence of technical
exceptions, tool-execution failures, retries, or halts encountered along the way. Scoped to skill
files only (files under `.claude/skills/**`), nothing else in scope: if you find such evidence,
revise/improve whichever skill(s) it points to so the same issue is less likely to recur. If you
find no such evidence, make no changes and say so explicitly in your own `output.md` rather than
inventing something to change.

Before editing any `SKILL.md` (new or existing), invoke the `mattpocock-skills:writing-for-agents`
skill and follow its guidance for skill structure, frontmatter, and writing conventions — do not
hand-roll the edit outside that process. This applies to every skill file touched in this step, not
just net-new skills. (This skill replaced `writing-great-skills`, which no longer exists as of the
`mattpocock-skills` plugin's 1.2.1 release — if a future plugin update renames or removes
`writing-for-agents` too, verify the current skill name before invoking rather than trusting this
reference blindly.)

## 08_integrate_and_merge

```yaml
deps: [07_skill_revision]
type: leaf
retry: 0
agent: general-purpose
branches:
  - condition: "the feature branch merged into the integration branch with no conflicts"
    next: 01_select_next_idea
  - condition: "the merge produced conflicts"
    next: 12_merge_conflict
  default: 12_merge_conflict
```

Read `06_check_feature_kickoff_result`'s latest `output.md` for the idea's slug, feature branch
name, and the integration branch name (from `01_select_next_idea`'s latest `items.json`).

Check `07_skill_revision`'s item-1 nested run: look for which of `standard-task`'s terminal nodes it
reached (`04_success/attempt-*/` vs `05_manual_flag/attempt-*/`). If it reached `05_manual_flag`,
write a short manual follow-up note under `agent_works/manual_actions/` (per this project's
`CLAUDE.md` convention) describing that idea `{slug}`'s skill-revision pass needs human attention,
with a pointer to that nested run's `output.md`. This never blocks the merge below — the idea's
actual feature work already succeeded.

Check out the integration branch, then locally merge `feature/{slug}` into it (`git merge
feature/{slug}`, no flags that skip conflict detection). Never push, never touch remotes.

- If the merge completes cleanly (or fast-forwards): end `output.md` stating which idea slug was
  just integrated, and a single-line `Result: merged cleanly` conclusion.
- If the merge produces conflicts: run `git merge --abort` so the working tree is left clean, list
  exactly which files conflicted in `output.md`, and end with a single-line `Result: merge
  conflict` conclusion. Do not attempt to resolve the conflict yourself.

## 09_all_ideas_complete

```yaml
deps: [01_select_next_idea]
type: leaf
retry: 0
agent: general-purpose
```

Every idea in the batch has been drafted, implemented, and merged onto the local integration
branch. Read `01_select_next_idea`'s latest `output.md` for the integration branch name. Write
`output.md` summarizing: the integration branch name, every idea slug processed (from
`00_parse_idea_queue`'s `items.json`), and a note that nothing was pushed — reviewing and pushing
this branch is a manual step for the user.

## 10_spec_rejected

```yaml
deps: [03_check_spec_approved]
type: leaf
retry: 0
agent: general-purpose
```

This idea's spec was rejected on 3 consecutive attempts inside `idea-to-spec` and needs direct
human review. Read `03_check_spec_approved`'s latest `output.md` for the idea's slug and spec file
path. For the consolidated review findings, find the underlying `idea-to-spec` run's own
`04_manual_flag` output: under `02_run_idea_to_spec`'s latest attempt folder, `item-1`'s latest
nested attempt, look for `output.md` under a `04_manual_flag/attempt-*/` folder inside that nested
run, and read it. Write `output.md` summarizing exactly what's blocking and for which idea, and
save the same summary as a manual follow-up note under `agent_works/manual_actions/`.

## 11_feature_kickoff_blocked

```yaml
deps: [06_check_feature_kickoff_result]
type: leaf
retry: 0
agent: general-purpose
```

This idea's `feature-kickoff` run ended at a blocked/needs-manual-review terminal and needs direct
human attention before this batch can continue. Read `06_check_feature_kickoff_result`'s latest
`output.md` for the idea's slug and feature branch name. For details, find the underlying
blocked/needs-review terminal's own output: under `05_run_feature_kickoff`'s latest attempt
folder, `item-1`'s latest nested attempt, look for `output.md` under a
`07_blocked_plan_rejected/attempt-*/` or `08_needs_manual_review/attempt-*/` folder inside that
nested run (whichever exists), and read it. Write `output.md` summarizing exactly what's blocking
and for which idea, and save the same summary as a manual follow-up note under
`agent_works/manual_actions/`.

## 12_merge_conflict

```yaml
deps: [08_integrate_and_merge]
type: leaf
retry: 0
agent: general-purpose
```

Merging this idea's finished feature branch into the batch's integration branch produced conflicts
that need direct human resolution. Read `08_integrate_and_merge`'s latest `output.md` for the idea's
slug, the two branch names, and the exact list of conflicting files. Write `output.md` summarizing
this, and save the same summary as a manual follow-up note under `agent_works/manual_actions/`.
