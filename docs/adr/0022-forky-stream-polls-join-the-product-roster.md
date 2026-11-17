# 0022 — The `:forky` stream polls join the product roster

- **Status:** Accepted
- **Date:** 2026-11-16

## Context

snosi publishes an opt-in forky stream. Its moving `:forky` tag moves only by a
promote dispatch, to a digest that the lab already proved
([snosi ADR-0040](https://github.com/frostyard/snosi/blob/main/docs/adr/0040-move-the-forky-tag-to-a-verified-forky-image-by-dispatch.md)).
Machines on that stream run the `:forky` digest every day, but no lab lane
watches it: the only scheduled product polls are
`image-poll-{snow,floe,snowfield,sundog}-latest`.

[ADR-0011](0011-product-poll-qa-evidence-is-rostered-and-fresh.md) fixes the
product-poll roster as one row per product, identified by the workflow name
`image-poll-<product>-latest`, and checks that roster against the lab's
CronWorkflows and `image-polling-digests` keys. A `:forky` poll is not in that
roster, so the collector would file it as a generic `image-poller` lane and the
dashboard would show no forky evidence. Accepted ADRs are immutable, so the
roster change needs its own ADR.

## Decision

This ADR amends the roster of ADR-0011. Every other rule of ADR-0011 holds
for the new rows.

1. The roster is **products × streams**. The streams are `latest` and
   `forky`, in that order; the products keep ADR-0011's order (snow, floe,
   snowfield, sundog). The lane key is `image-poll-<product>-<stream>`, the
   CronWorkflow name. The 4 `-latest` rows stay first and stay unchanged; the
   4 `-forky` rows follow.
2. Each `-forky` row has its own CronWorkflow
   (`manifests/image-poll-<product>-forky.yaml`) on the `image-poller`
   template, with `image-tag: forky`, the state key `digest-<product>-forky`
   in `image-polling-digests`, and the `smoke` suite. The digest gate and the
   compare-and-swap state of
   [ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md) apply as
   they are.
3. The `-forky` polls run every 3 hours, like the `-latest` polls, offset by
   1 hour (hours 1, 4, …, 22 UTC) so that the two streams do not queue on the
   same container-test semaphore at the same time.
4. The `-forky` rows use the same freshness rules as the `-latest` rows: poll
   evidence older than six hours at snapshot generation is stale. A carried
   pass (unchanged digest) refreshes each 3-hour poll, so the rare moves of
   `:forky` do not make the row stale.
5. The dashboard names a `-latest` row by the product (`snow`), as before, and
   a `-forky` row by product and tag (`snow:forky`).
6. Tests check the products × streams roster against the CronWorkflows and the
   ConfigMap keys, in the collector and in the dashboard. The check against
   snosi's product matrix and its forky publication stays manual.

## Consequences

- Forky users get continuous smoke evidence on the same dashboard as
  `:latest`, with the same pass, fail, carried and stale meanings.
- The page has 8 product rows instead of 4. An empty snapshot shows 8 unknown
  rows.
- A new stream or a new product now changes the roster in 2 dimensions: the
  collector and dashboard rosters, one CronWorkflow and one ConfigMap key for
  each product and stream. The parity tests catch drift in the lab.
- The smoke suite must hold on forky as well as on trixie. A suite assumption
  that names one Debian release turns a `-forky` row red; the fix belongs in
  frostyard/testsuite.
- 4 more polls run every 3 hours. When the digest is unchanged, a poll is only
  a registry lookup and runs no QA.

## Alternatives considered

- **Poll `:forky` every 6 hours with a 12-hour stale window:** fewer runs, but
  2 freshness rules on one page. The unchanged-digest poll is cheap, so one
  rule is simpler.
- **One forky row for all 4 products:** mixes products, the problem that
  ADR-0011 removed.
- **Poll the immutable `forky-<version>` tags:** those are test builds before
  promotion. `:forky` is what forky users run, so that is the tag to watch.
- **Supersede ADR-0011:** its evidence and freshness rules stay valid; only
  the roster grows.

## References

- Shapes: [design/reporting.md — Reporting](../design/reporting.md),
  [design/architecture.md](../design/architecture.md),
  [quality.md — Triaging product QA evidence](../quality.md#triaging-product-qa-evidence)
- Implemented by: `manifests/image-poll-*-forky.yaml`,
  [manifests/image-polling-digests.yaml](../../manifests/image-polling-digests.yaml),
  [scripts/collect_runs.py](../../scripts/collect_runs.py),
  [site/src/dashboard.mjs](../../site/src/dashboard.mjs)
- Enforced by: [tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py),
  [tests/test_collect_runs.py](../../tests/test_collect_runs.py),
  [site/test/dashboard.test.mjs](../../site/test/dashboard.test.mjs)
- Amends: [ADR-0011](0011-product-poll-qa-evidence-is-rostered-and-fresh.md)
- Builds on: [ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md)
