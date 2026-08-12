# 0003 — A never-green lane reports `unproven`, not Failed

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

A red lane carries two very different meanings: "the thing under test
regressed" or "the lane itself has never been shown to work". The lab
produced **two false bug reports against snosi in one week** (snosi#504,
#505, both closed invalid) by reading a never-green lane's red as evidence
about the image, when the defects were in the lane — a SIGPIPE/pipefail
inversion and a wrong image tier
([docs/roadmap.md](../roadmap.md), Phase A). The dashboard is the surface
other people act on, so it must not present an unproven lane's failure as a
finding.

## Decision

`unproven` is a first-class lane state, distinct from `Failed`, computed
and rendered end to end:

- The collector ([scripts/collect_runs.py](../../scripts/collect_runs.py))
  computes `everGreen` per lane — whether any run in the retention window
  ever reached `Succeeded` — and writes it into `runs.json`.
- The dashboard ([site/src/dashboard.mjs](../../site/src/dashboard.mjs))
  maps a failing latest run with `everGreen === false` to `unproven`;
  [site/src/pages/index.astro](../../site/src/pages/index.astro) renders it
  deliberately muted rather than alarming, with the note "This lane has
  never passed. Treat its red as a question about the lane, not a finding
  about the image."
- A lane that has succeeded at least once and now fails renders as a real
  failure.

## Consequences

- A brand-new lane cannot generate a false regression report; it renders as
  a question about itself until its first green.
- `everGreen` is computed over the runs the cluster still retains (failed
  workflows kept 30 days, successful 7 — see
  `workflow-controller-configmap`), so a lane whose only green aged out of
  retention can regress to `unproven`. That is accepted: absence of
  retained evidence *is* absence of evidence.
- Enforced by unit tests
  ([site/test/dashboard.test.mjs](../../site/test/dashboard.test.mjs):
  "never-green failures are shown as unproven"; a green-then-failed lane
  stays `Failed`) and exercised end to end by
  [e2e/dashboard.spec.ts](../../e2e/dashboard.spec.ts).

## Alternatives considered

- **Render never-green lanes red like any failure:** the status quo that
  produced both false reports; rejected on that evidence.
- **Hide never-green lanes entirely:** hides the work item; an unproven
  lane still needs to be visible so someone makes it pass.
- **A manual allowlist of "trusted" lanes:** decays instantly; `everGreen`
  is computed from the same run data the page already shows.

## References

- Shapes: [docs/roadmap.md](../roadmap.md) (Phase A, item A4),
  [README.md — Reporting](../../README.md#reporting)
- Implemented by: [scripts/collect_runs.py](../../scripts/collect_runs.py),
  [site/src/dashboard.mjs](../../site/src/dashboard.mjs),
  [site/src/pages/index.astro](../../site/src/pages/index.astro)
- Enforced by: [site/test/dashboard.test.mjs](../../site/test/dashboard.test.mjs),
  [tests/test_collect_runs.py](../../tests/test_collect_runs.py)
- Builds on: [ADR-0004](0004-one-way-evidence-pipeline.md) (the pipeline
  that carries the state), [ADR-0010](0010-vacuous-success-is-forbidden.md)
  (the same "green must mean something" principle at submission time)
