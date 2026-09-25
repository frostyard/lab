# 0011 — Roster product polls and qualify their QA evidence by freshness

- **Status:** Proposed
- **Date:** 2026-09-25

## Context

All three scheduled product polls use the `image-poller` WorkflowTemplate. A
template-only rollup mixes their results, while a successful poll can mean an
unchanged digest with no QA run. Workflow phase and human summary text alone
cannot establish image QA. Old retained results can also outlive a current
image's relevance. [ADR-0003](0003-unproven-is-distinct-from-failed.md) protects
never-green lanes from false product findings, and
[ADR-0004](0004-one-way-evidence-pipeline.md) registers generic lanes by template;
neither distinction is sufficient for product-specific QA evidence.

## Decision

For scheduled `image-poll-{snow,floe,snowfield}-latest` workflows, identify the
product lane by the **workflow name** (`laneKey`), not the shared template name.
Maintain an explicit product roster in the collector and dashboard, checked
against the actual image-poll CronWorkflow manifest names. Show all rostered
products even when no retained poll exists, with unknown status and no invented
run. Generic, non-product lanes retain template-based registration and the
never-green `unproven` rule of ADR-0003.

Publish `qaOutcome` per product poll from a recognized Behave scenario-count
summary and the workflow phase: `passed` requires at least one passed scenario,
no failed or undefined scenarios, and phase `Succeeded`; `failed` requires
nonzero observed scenario counts and phase `Failed` (even if the reported
scenarios passed before the workflow failed); `not-run` means phase `Succeeded`
with an absent or null result (including an unchanged digest); an empty string
is `unknown`, as are malformed results and zero-count failures. A
confirmed product QA failure renders red **even if no earlier pass was
retained**; this is a product-only refinement to ADR-0003, not a change to
other never-green lanes. Workflow phase alone never establishes a QA outcome.

Measure poll freshness against the published snapshot's `generated` timestamp,
not the viewer's clock. Evidence older than **six hours** (two three-hour poll
intervals) is stale, not current green QA; an old confirmed QA failure remains
red, explicitly labelled stale evidence. Missing, invalid or future timestamps
cannot establish fresh QA. Older snapshots without `laneKey` or `qaOutcome`
may be separated by workflow name, but their QA remains unknown rather than
being inferred from phase or result text. Stale is an overlapping age qualifier:
old unknown/legacy and not-run polls retain their unverified QA label and count
as both unknown and stale; old passed polls are stale, not green. Manual
VM/installer workflow results are separate evidence, **not continuous product QA**.

## Consequences

- Product failures become visible without a prior green, while unchanged polls
  and expired passes cannot be mistaken for verified current image QA.
- Product additions require updating collector and dashboard rosters alongside
  CronWorkflow manifests; parity tests catch drift. This exception to ADR-0004's
  registration-by-template convenience is intentional; its one-way publication
  pipeline and generic-lane behavior remain unchanged.
- Retention and delayed publication can leave unknown or stale entries; a
  snapshot cannot assert live cluster state. Readers of ADR-0003 or ADR-0004
  alone may miss this product-only refinement because accepted ADRs remain
  immutable; the current reporting docs and ADR index link here instead.

## Alternatives considered

- **Use `image-poller` as one lane:** mixes products and hides which image was
  checked or failed.
- **Infer QA from workflow phase, summary text or a previous green:** an
  unchanged-digest success can run no QA, and legacy summaries are not a
  machine-checkable verdict.
- **Treat old passes as current, or old failures as unqualified current red:**
  both overstate what a retained snapshot proves.
- **Edit or wholly supersede ADR-0003/0004:** would erase still-valid
  non-product `unproven` semantics and the one-way evidence pipeline.

## References

- Shapes: [README.md — Reporting](../../README.md#reporting),
  [quality.md — Triaging product QA evidence](../quality.md#triaging-product-qa-evidence),
  [roadmap.md — Status at a glance](../roadmap.md#status-at-a-glance)
- Implemented by: [scripts/collect_runs.py](../../scripts/collect_runs.py),
  [site/src/dashboard.mjs](../../site/src/dashboard.mjs)
- Enforced by: [tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py),
  [tests/test_collect_runs.py](../../tests/test_collect_runs.py),
  [site/test/dashboard.test.mjs](../../site/test/dashboard.test.mjs)
- Builds on: [ADR-0003](0003-unproven-is-distinct-from-failed.md),
  [ADR-0004](0004-one-way-evidence-pipeline.md)
