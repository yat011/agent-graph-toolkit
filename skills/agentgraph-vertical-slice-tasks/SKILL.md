---
name: agentgraph-vertical-slice-tasks
description: How to size and sequence tasks in a plan's task breakdown — vertical slices, prefactoring, blocking edges, the wide-refactor exception. Use when writing or reviewing a standalone standard-phase work order. Not for feature-kickoff — use agentgraph-phase-sizing.
---

# Vertical-Slice Task Sizing

**Not for feature-kickoff.** Kickoff plans use `agentgraph-phase-sizing`. This skill remains for
standalone `standard-phase` single-bug / chore runs.

Adapted from the `mattpocock-skills` plugin's `to-tickets` skill for this project's task-list
format — tasks stay in `agent_works/plans/{slug}.tasks.json`. The live `feature-kickoff` graph
fans phases through `standard-phase`, not this skill.

## Cut vertical, not horizontal

Each task should cut a complete path through every layer it touches, not one layer across
everything — "add the field to the schema" is horizontal; "add the field end-to-end, including the
code that uses it and a test proving it works" is vertical. A completed task should be
independently verifiable on its own — that's what its required test cases are checking.

## Prefactor first

If a task needs groundwork to make the real change land cleanly, that groundwork is its own
earlier task (a dependency), not folded into the task it's enabling. "Make the change easy, then
make the easy change."

## Dependencies are blocking edges

A task's `dependencies` list is exactly the set of tasks that must complete before it can start —
the same concept `to-tickets` calls "blocked by." Check both directions: nothing the task actually
needs is missing, and nothing listed is spurious (doesn't actually gate this task).

## Exception: wide refactors

A **wide refactor** — one mechanical change whose blast radius fans across the whole codebase
(rename a shared symbol, retype a widely-used field) — can't be cut into independent vertical
slices; a single edit breaks many call sites at once. Don't force it into one task. Sequence it
**expand → migrate (batched by blast radius) → contract** instead, each phase its own task(s), so
the codebase stays green between tasks.
