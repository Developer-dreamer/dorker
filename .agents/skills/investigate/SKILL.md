---
name: investigate
description: "Investigate Dorker's code, execution flow, history, or engineering options when the maintainer wants an explanation, diagnosis, or plan without implementation. Use for research and planning requests; do not turn findings into code changes."
---

# Investigate

Help the maintainer understand the system and make a decision. Follow the applicable
AGENTS.md and the resources and access boundaries specified in the request.

## Scope

- Inspect code and authorized resources without changing project files, data,
  configuration, or external documents. Provide code examples in the response
  when useful. Save an investigation artifact only when specifically requested.
- Treat the requested question as the boundary. If another resource is necessary
  but outside the stated scope, identify the gap and ask for access or context.
- This skill does not authorize implementation. A later explicit request can
  transition to collaborative build or delegated handoff.

## Investigation

- Trace the relevant entry point, callers, data models, state changes, and storage.
  For library behavior, inspect the installed version and consult authoritative
  documentation when needed. Avoid recommending from names or summaries alone.
- Examine focused Git history, tests, and accepted decisions when they help explain
  the current design. Distinguish observed facts, inferred intent, and proposals;
  historical behavior can have been superseded.
- Investigate enough to answer the question. Do not impose a full architecture
  audit or specification process on a local issue. Reproductions must stay within
  authorized side effects; prefer inspection and isolated, synthetic examples.
- Explain findings through actual files and execution paths. When a choice is
  needed, compare the relevant alternatives, recommend an approach, and state
  what evidence or uncertainty affects that recommendation.

## Finish

Answer the question with supporting file references, material unknowns, and the
next decision or experiment if needed. Stop at the explanation or plan. Do not
start implementing a recommended approach merely because it appears preferable.
