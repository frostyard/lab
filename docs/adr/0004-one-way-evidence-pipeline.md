# 0004 — Evidence flows one way: checks.txt → collector → runs.json → Pages

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

Run results must reach a public dashboard
(<https://frostyard.github.io/lab/>) from a cluster that has no public
surface and should need none. Wiring reporting into each lane couples every
lane to the reporting mechanism; giving GitHub access into the cluster (or
the cluster broad access to GitHub) creates an attack surface the lab does
not want.

## Decision

Results flow **one way**, cluster → git → Pages, through fixed contracts at
each hop, and lanes never know reporting exists:

1. **Lanes emit two output parameters, nothing else.** Every lane writes
   `/tmp/results/result-summary.txt` (a one-line human verdict) and
   `/tmp/results/checks.txt` — newline-separated `key=value` pairs
   (e.g. `verity=ok`, `rootfs=erofs`) — surfaced as the Argo output
   parameters `result` and `checks` (see
   [argo/workflow-templates/run-incus-install-tests.yaml](../../argo/workflow-templates/run-incus-install-tests.yaml)).
2. **The collector reads only the Kubernetes API.**
   [scripts/collect_runs.py](../../scripts/collect_runs.py) lists Workflow
   objects, extracts `result`/`checks` from node outputs, and identifies
   each run's lane from `workflowTemplateRef`/`templateRef`. Its
   `LANE_KINDS` map is the single registration point for how a lane is
   described; an unregistered lane still appears, labelled by its raw
   template name — a lane added tomorrow shows up with no reporting change.
3. **The site reads only `site/src/data/runs.json`** — the collector's
   output contract; the Astro page has no other data source.
4. **Publication is a git push.** The in-cluster `publish-results`
   CronWorkflow ([manifests/publish-results.yaml](../../manifests/publish-results.yaml))
   regenerates `runs.json` every 30 minutes and pushes to `main`; the push
   triggers the Pages build. The cluster never reaches GitHub Pages and
   Pages never reaches the cluster; the only credential is a
   `contents:write` token held in the `argo` namespace.

## Consequences

- Adding a lane requires no reporting work; adding a *nicely labelled* lane
  requires one line in `LANE_KINDS`.
- Anything not expressed as a Workflow is invisible to the dashboard — the
  accepted cost of reading only the Kubernetes API.
- The `checks` parsing is tolerant by contract: the collector keeps only
  lines containing `=`, and lanes must build `checks.txt` so that an empty
  result cannot kill the lane script (see the load-bearing `|| true` in
  run-incus-install-tests and docs/roadmap.md item A5).
- The window is bounded by Argo retention (failed 30 days, successful 7),
  not by the collector; `MAX_RUNS = 200` caps the page.
- A stuck publisher cannot wedge the system: the CronWorkflow is
  `concurrencyPolicy: Forbid` with a 600 s deadline, and a missing token is
  a clean exit 0, not a red run every 30 minutes.
- Enforced by [tests/test_collect_runs.py](../../tests/test_collect_runs.py)
  (lane identification, checks parsing, rollup) and
  [e2e/dashboard.spec.ts](../../e2e/dashboard.spec.ts) (what `runs.json`
  contains is what the page renders).

## Alternatives considered

- **Each lane posts its own results** (workflow exit handlers, webhooks):
  couples every lane to reporting, multiplies credentials, and a lane
  author can forget it; centralizing in one collector makes reporting a
  property of the platform.
- **GitHub Actions polls the cluster:** requires exposing the Kubernetes
  API publicly; rejected outright.
- **Argo UI as the dashboard:** the Argo server is deliberately disabled
  (`server.enabled: false`,
  [ADR-0001](0001-two-argocd-applications-and-hand-applied-bootstrap.md));
  the audience for results should not need cluster credentials.

## References

- Shapes: [README.md — Reporting](../../README.md#reporting)
- Implemented by: [scripts/collect_runs.py](../../scripts/collect_runs.py),
  [manifests/publish-results.yaml](../../manifests/publish-results.yaml),
  [site/src/dashboard.mjs](../../site/src/dashboard.mjs)
- Enforced by: [tests/test_collect_runs.py](../../tests/test_collect_runs.py),
  [e2e/dashboard.spec.ts](../../e2e/dashboard.spec.ts)
- Builds on: [ADR-0001](0001-two-argocd-applications-and-hand-applied-bootstrap.md);
  related: [ADR-0003](0003-unproven-is-distinct-from-failed.md),
  [ADR-0009](0009-no-artifact-store-logs-are-the-surface.md)
