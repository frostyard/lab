# 0010 — Vacuous success is forbidden: green must mean work was done

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

Several mechanisms in this stack default to reporting success when they did
*nothing*: Argo's `withItems` + `when` runs zero tasks for an unmatched
item and the DAG succeeds; a nested lane's failure can become a handled
branch whose parent reports `Succeeded`; Node's test runner exits 0 when
its glob matches no files; a validation loop over zero manifests passes
trivially. In a QA lab, a green that tested nothing is strictly worse than
a red — it is the false evidence problem of
[ADR-0003](0003-unproven-is-distinct-from-failed.md) at the submission
layer.

## Decision

Every point where "ran nothing" could read as "passed" carries an explicit
guard; new fan-outs, watchers, and CI jobs must add one. The enforcement
sites:

- **Suite fan-out** — `snosi-qa-pipeline` runs a `validate-suites` step
  before the `withItems: [smoke, system, sysext]` fan-out and fails on an
  empty list or unknown suite name, because "`withItems` + `when` silently
  runs zero lanes for an unknown suite name, which would report success
  for a pipeline that tested nothing"
  ([argo/workflow-templates/snosi-qa-pipeline.yaml](../../argo/workflow-templates/snosi-qa-pipeline.yaml)).
- **Watcher submission** — `secure-install-watch` submits the lane as a
  **standalone** Workflow (`resource: create`) rather than a nested task:
  nested, "the lane's failure became a handled branch and this workflow
  reported Succeeded over a failed run — and collect_runs.py maps by node
  templateRef, so that green would have reached the dashboard as a passing
  secure install" ([argo/secure-install-watch.yaml](../../argo/secure-install-watch.yaml)).
  (It also must not hold the VM semaphore for the lane's ~25 minutes,
  [ADR-0007](0007-cross-workflow-concurrency-via-template-semaphores.md).)
- **CI test globs** — `.github/workflows/ci.yml` asserts
  `site/test/*.test.mjs` matches at least one file before `npm test`,
  because "Node.js exits successfully when its test glob matches nothing"
  ([.github/workflows/ci.yml](../../.github/workflows/ci.yml)).
- **Manifest validation** — the pytest suite fails if a manifest root has
  no YAML files, if zero built-in resources were schema-validated
  (`assert validated > 0`), or if zero containers were resource-checked
  (`assert checked > 0`)
  ([tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py)).

## Consequences

- A typo in a suite name, a deleted test file, or an emptied manifests
  directory turns red instead of silently green.
- Every new `withItems`/`when` fan-out needs a validation step for its
  selector; every new counting loop needs a `> 0` assertion; every parent
  that launches a lane must either propagate its failure or hand it off as
  a standalone workflow whose own status reaches the dashboard.
- Standalone submission decouples the watcher from the lane's outcome, so
  the watcher records its digest at submission time — the stronger
  persist-after-QA guarantee remains the poller's
  ([ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md));
  the trade was accepted for honest per-lane reporting.
- Guards are per-site, not systemic: nothing scans for new unguarded
  fan-outs, so review must apply this rule to new templates.

## Alternatives considered

- **Trusting Argo/Node defaults:** each cited site is a case where the
  default happily reports success for zero work; rejected on direct
  experience.
- **Nesting the watched lane with failure propagation:** honest, but the
  watcher would hold the VM semaphore for the lane's whole runtime and its
  cron `concurrencyPolicy: Forbid` would stall subsequent polls.
- **A generic "minimum tasks ran" post-hoc check:** would centralize the
  guard but cannot know the intended count; per-site validation states the
  intent where it lives.

## References

- Shapes: [README.md — Suites](../../README.md#suites)
- Implemented by:
  [argo/workflow-templates/snosi-qa-pipeline.yaml](../../argo/workflow-templates/snosi-qa-pipeline.yaml),
  [argo/secure-install-watch.yaml](../../argo/secure-install-watch.yaml),
  [.github/workflows/ci.yml](../../.github/workflows/ci.yml),
  [tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py)
- Builds on: [ADR-0003](0003-unproven-is-distinct-from-failed.md); related:
  [ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md),
  [ADR-0007](0007-cross-workflow-concurrency-via-template-semaphores.md)
