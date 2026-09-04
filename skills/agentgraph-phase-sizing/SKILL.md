---
name: agentgraph-phase-sizing
description: How to size and sequence phases in a feature-kickoff plan — context-window sized sequential slices, review policy, linear dependencies. Use when writing or reviewing a plan's Phases section and its machine-readable phase list.
---

# Phase Sizing

Feature-kickoff runs **sequentially**. A phase is one implementer session that fits one context
window — not a parallel ticket. Review is a flag on the phase, not a separate item.

## Size to context, not to independence

A phase covers everything that shares implicit decisions (types, naming, error policy, test
seams) and fits one focused implementer pass. Fold trivial neighbors in. Split only at context-rot
or a true decision boundary. Merge if one phase would not fill a session.

## Cut vertical, not horizontal

A phase may span layers if they are the same change. "Add the field to the schema" is horizontal;
"add the field end-to-end, including the code that uses it and tests proving it" is vertical.

## Prefactor first

Groundwork that would pollute the feature's context is its own earlier phase. If the prefactor is
small, keep it as the first section of phase 1.

## Dependencies are a sequence, not a ready-queue

A linear chain 1→2→3 is the normal shape. Empty `dependencies` on several phases is a smell —
you probably split for parallelism the graph will not use.

## Review is not a phase

Do not emit "implement X" and "review X" as separate items. Set `review` on the phase. Do not
emit a phase whose only job is "run the suite" or "e2e / final review" — that is
`additional_test` then a `final-reviewer` agent. The last implementing phase must leave the
feature integration-complete (wiring done, not "unit tests green, scene TBD").

## Exception: wide refactors

A **wide refactor** — one mechanical change whose blast radius fans across the whole codebase —
can't be one phase. Sequence it **expand → migrate (batched by blast radius) → contract**. Each
migrate batch is a phase because the writer's context is the blast radius, not because we want
parallel workers.

## Count: 2–5 phases

Default target. 1 is allowed. More than 5 requires an explicit justification in the plan text
(e.g. wide migrate batches).

## Review policy

Each phase JSON object has required `review`:

| Value | When |
|-------|------|
| `always` | New behavior, shared types, Unity scene/prefab changes, or security-sensitive code. Position-independent — not "because this phase is last." |
| `if_substantial` | Default for most phases. Engine skips the reviewer when the working-tree diff is small (under the line threshold, no new public type, no scene/prefab). |
| `never` | Pure mechanical repeat of an already-reviewed operation, or docs-only content folded into the phase that caused the docs change |

Default to `if_substantial`. Justify `always` or `never`.
