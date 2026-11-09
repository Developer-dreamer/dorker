---
name: handoff
description: "Complete a bounded Dorker change independently when the maintainer explicitly delegates implementation and verification. Use for an authorized handoff with understood outcomes; an exploratory question or ordinary collaborative build is not a handoff."
---

# Handoff

Carry an explicitly delegated outcome through implementation and verification.
Follow the applicable AGENTS.md. Skill selection does not settle missing product
decisions or expand authorization beyond the requested outcome.

## Establish the boundary

- Confirm from the request what outcome is delegated and how it will be evaluated.
  Inspect the actual code, relevant tests and history, and named decision records.
- State a concise scope and approach. Resolve consequential ambiguity with the
  maintainer; do not invent acceptance criteria and treat them as approved.
  Once scope is clear, proceed without repeated planning approvals or pauses for
  routine implementation choices.
- Match verification to the task. A small fix can use a focused reproduction;
  compatibility work may require checking equivalent behavior across backends.
  Do not require a specification framework for every delegated change.

## Execute

- Implement the bounded outcome, run suitable checks, inspect failures, and fix
  regressions within scope. Review the resulting diff against the requested
  behavior and accepted decisions, including meaningful failure cases.
- Preserve existing work. Keep unrelated improvements out of the change, even
  when discovered during investigation. Report them separately.
- Bring new behavioral or architectural decisions back to the maintainer. Diagnose
  a blocker and provide evidence and options; do not repeatedly retry an unchanged
  failure or continue speculative edits. Continue work that is independent of it.
- Maintain a concise explanation of consequential changes so the maintainer can
  trace the result. Additional documents and external actions require the task's
  authorization; a handoff alone does not authorize publishing or deployment.

## Handoff the result

Return the implemented behavior, important choices, relevant files and execution
path, checks actually run, and remaining limitations. Stop when the delegated
outcome is implemented and verified, or when a specific unresolved decision or
blocker requires the maintainer. Distinguish incomplete verification from success.
