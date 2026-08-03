---
name: test-quality-bar
description: What makes an automated test worth keeping — seams, anti-patterns, coverage floor. Use when writing or updating tests for a code change in this project.
---

# Test Quality Bar

Adapted from the `mattpocock-skills` plugin's `tdd` skill for this project's non-interactive
pipeline — see that skill for the full interactive red-green loop, seam-confirmation dialogue, and
mocking guidance this project doesn't use.

## What a good test is

Tests verify behavior through the public interface the plan/spec already describes, not
implementation details — code can change entirely and the test shouldn't need to. A good test
reads like a specification: it should be obvious from the test's name and body what capability it
proves exists.

## Seams — pick from the plan, don't invent live

A **seam** is the public boundary a test observes without reaching inside. There's no user to
confirm seams with mid-task here — the plan already approved by `tech-plan-reviewer` implies them
(the behavior it describes, its inputs/outputs). Test at those seams. If the plan is genuinely
silent on where the boundary is, flag that in your report rather than guessing and testing an
internal.

## Anti-patterns to avoid

- **Implementation-coupled** — mocking internal collaborators, testing private methods, or
  asserting through a side channel instead of the real interface. Tell: the test breaks on a
  refactor even though behavior didn't change.
- **Tautological** — the assertion recomputes the expected value the same way the code does, so it
  can never disagree with the code. The expected value must come from an independent source (a
  known-good literal, a worked example, the spec's own numbers).
- **Horizontal slicing** — writing every test up front, then all the implementation. Write one
  test, make it pass, repeat — each test should respond to what implementing the last one actually
  taught you, not to a shape imagined before any code existed.

## Coverage floor, not ceiling

The plan's listed test cases are the minimum required, not a cap — add more if you find an edge
case or branch the plan didn't call out while implementing.
