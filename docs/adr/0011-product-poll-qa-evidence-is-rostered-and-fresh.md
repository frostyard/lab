# 0011 — Roster product polls and qualify their QA evidence by digest and freshness

- **Status:** Accepted
- **Date:** 2026-10-23

## Context

Every scheduled product poll uses the `image-poller` WorkflowTemplate, so a
template-only rollup mixes their results. A successful poll can mean an
unchanged digest with no QA run, and the dashboard showed that as "QA not run":
about 78% of product polls in 446 snapshots looked unverified.
[ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md) stores a digest
in `image-polling-digests` only after QA succeeds, so an unchanged digest is
itself proof of an earlier pass. [ADR-0003](0003-unproven-is-distinct-from-failed.md)
protects never-green lanes, and [ADR-0004](0004-one-way-evidence-pipeline.md)
registers generic lanes by template; neither distinction covers product-specific
QA evidence.

## Decision

For scheduled `image-poll-{snow,floe,snowfield,sundog}-latest` workflows,
identify the product lane by the **workflow name** (`laneKey`), not the shared
template name.

The roster is every product snosi publishes, in this order: snow, floe,
snowfield, sundog. It must match snosi's `build-images.yml` product matrix.
Tests check the roster against the lab's own CronWorkflows and ConfigMap keys;
the check against snosi is manual. Show all rostered products even when no
retained poll exists, with unknown status and no invented run. Generic lanes
keep template-based registration and the never-green `unproven` rule of
ADR-0003.

Publish `qaOutcome` per product poll from a recognized Behave scenario-count
summary, the workflow phase, and the digest outputs of the poll:

- `passed`: at least one passed scenario, no failed or undefined scenarios, and
  phase `Succeeded`.
- `failed`: nonzero observed scenario counts and phase `Failed` (even if the
  reported scenarios passed before the workflow failed).
- `carried`: phase `Succeeded`, absent or null result, `changed` is `"false"`,
  a valid remote digest, and `stored-digest` equal to that digest. ADR-0002
  stores the digest only after QA succeeds, so the poll carries the last pass
  for that digest. The run records `digest`; the lane records `qaRun`, the
  newest retained passed run for the same digest, or `None`. The dashboard
  shows the short digest and, when retained, the run that verified it.
- `not-run`: phase `Succeeded` without that proof, for example `remote-digest`
  is `none`, the stored digest differs, or an old run has no digest outputs.
- `unknown`: empty or malformed results, zero-count failures, and records
  without `qaOutcome`.

A confirmed product QA failure renders red **even if no earlier pass was
retained**; this is a product-only refinement to ADR-0003.

Two freshness checks apply:

- Poll evidence older than **six hours** by the published snapshot's
  `generated` timestamp is stale. This means the poll stopped while publication
  went on. An old confirmed failure stays red and explicitly stale; an old pass
  or carried pass is stale, not green.
- The page warns when the snapshot is missing, invalid, more than five minutes
  in the future, or older than **two hours** by the viewer's clock. This means
  the lab or the publisher stopped. The check runs in a small client script, so
  the page still renders with JavaScript off and the banner stays hidden.

Older snapshots without `laneKey` or `qaOutcome` may be separated by workflow
name, but their QA remains unknown rather than being inferred from phase or
result text. Manual VM/installer workflow results are separate evidence, **not
continuous product QA**.

## Consequences

- Unchanged-digest polls show a qualified pass instead of "QA not run", with
  the digest and the verifying run visible when retention still holds them.
- A dead lab or publisher becomes visible in the browser within two hours,
  instead of a page that stays green forever with a stale snapshot.
- Adding a product requires updating the collector and dashboard rosters, a
  CronWorkflow manifest, the ConfigMap key, and snosi's product matrix. Parity
  tests catch drift in the lab; the snosi check stays manual.
- Retention and delayed publication can leave unknown or stale entries; a
  snapshot cannot assert live cluster state.
- The snapshot warning needs a client script. With JavaScript off it stays
  hidden, and only the six-hour poll-age rule applies.

## Alternatives considered

- **Keep `not-run` for an unchanged digest:** hides real passes and made most
  snapshots look unverified.
- **Use `image-poller` as one lane:** mixes products and hides which image was
  checked or failed.
- **Carry a pass without matching the digest:** would attribute an old pass to
  a different image.
- **Measure freshness only against `generated`:** a dead lab keeps a
  permanently green page.
- **Warn only in the collector:** a snapshot proves nothing about the viewer's
  clock or the age of delivery.
- **Edit or supersede ADR-0003/0004:** would erase still-valid non-product
  `unproven` semantics and the one-way evidence pipeline.

## References

- Shapes: [README.md — Reporting](../../README.md#reporting),
  [quality.md — Triaging product QA evidence](../quality.md#triaging-product-qa-evidence)
- Implemented by: [scripts/collect_runs.py](../../scripts/collect_runs.py),
  [site/src/dashboard.mjs](../../site/src/dashboard.mjs),
  [site/src/pages/index.astro](../../site/src/pages/index.astro)
- Enforced by: [tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py),
  [tests/test_collect_runs.py](../../tests/test_collect_runs.py),
  [site/test/dashboard.test.mjs](../../site/test/dashboard.test.mjs)
- Builds on: [ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md),
  [ADR-0003](0003-unproven-is-distinct-from-failed.md),
  [ADR-0004](0004-one-way-evidence-pipeline.md)
