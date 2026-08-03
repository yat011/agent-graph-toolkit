---
name: agentgraph-code-review-standards
description: Two-axis (Standards vs Spec) review structure plus a Fowler smell-baseline checklist. Use when reviewing a diff against project conventions and the originating spec/plan.
---

# Code Review Standards

Adapted from the `mattpocock-skills` plugin's `code-review` skill for this project — there's no
issue tracker here, so the Spec axis is sourced from `agent_works/plans/{slug}.md` (and
`agent_works/specs/{slug}.md` if it references one) instead of tracker issues, and one reviewer
judges both axes in a single pass rather than two parallel sub-agents. See the plugin skill for
the full tracker-based workflow this project doesn't use.

## Two axes, reported separately

Judge every review along two independent axes and report them under separate headings — never
merge or rerank findings across them. A change can pass one and fail the other: code that follows
every convention but implements the wrong thing, or code that does exactly what was asked but
breaks a project convention.

- **Standards** — does the diff follow this repo's documented conventions (its own CLAUDE.md or
  equivalent: SOLID/DRY, no duplicate code, no defensive null checks, whatever file/language
  scope restrictions it declares) plus the smell baseline below?
- **Spec** — does the diff faithfully implement what the plan/spec asked for?

## Standards: smell baseline

On top of the project's own explicit rules (which always win where they conflict with a smell
below), check the diff against these Fowler smells (*Refactoring*, ch. 3). Each is a judgement
call ("possible Feature Envy"), never a hard violation, and skip anything tooling already
enforces:

- **Mysterious Name** — a name that doesn't reveal what it does or holds.
- **Duplicated Code** — the same logic shape appears in more than one hunk or file.
- **Feature Envy** — a method reaching into another object's data more than its own.
- **Data Clumps** — the same few fields/params keep travelling together.
- **Primitive Obsession** — a primitive/string standing in for a domain concept.
- **Repeated Switches** — the same switch/if-cascade on the same type recurs.
- **Shotgun Surgery** — one logical change forces scattered edits across many files.
- **Divergent Change** — one file/module edited for several unrelated reasons.
- **Speculative Generality** — abstraction/parameters/hooks added for a need the spec doesn't have.
- **Message Chains** — long `a.b().c().d()` navigation the caller shouldn't depend on.
- **Middle Man** — a class/function that mostly just delegates onward.
- **Refused Bequest** — a subclass/implementer that ignores or overrides most of what it inherits.

## Spec: what to check

- Requirements the spec/plan asked for that are missing or partial.
- Behavior in the diff that wasn't asked for (scope creep).
- Requirements that look implemented but where the implementation looks wrong.
- Tests that would still pass if the spec's requirement were violated (loosened assertions,
  widened tolerances, missing coverage for a stated requirement) — a real finding, not a nitpick.

Quote the spec/plan line for each Spec finding, and cite file:line (plus the smell name, if
applicable) for each Standards finding.
