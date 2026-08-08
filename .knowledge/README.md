# Cross-session knowledge

This directory is the discovery entry point for tools that look for a
`.knowledge/` store. Lab keeps its cross-session context in two existing
locations:

- [`.claude/session-summary.md`](../.claude/session-summary.md) is the concise,
  replaceable handoff for unfinished work.
- [`.memory/`](../.memory/) holds durable, reusable corrections that should
  survive beyond one handoff.

Record only repository-specific facts that will help a later contributor, and
include the evidence that established them. Keep temporary status in the
session summary rather than growing an unbounded log. Never store credentials,
secrets, personal data, or private vulnerability details in committed learning
artifacts.
