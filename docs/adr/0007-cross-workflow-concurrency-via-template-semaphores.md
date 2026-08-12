# 0007 — Cross-workflow concurrency is bounded only by template-level semaphores

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The same lane templates are reached from many directions — CronWorkflow
pollers, the QA pipeline's fan-out via `templateRef`, one-off `argo submit`
runs, and watcher-submitted standalone Workflows. Two real resources need
capacity limits regardless of who the caller is: registry egress plus
kubelet-root disk during multi-GB image pulls (the container lanes), and
the single-node VM fixture (disk/TPM state on `selfie`) that the incus
lanes own exclusively. Per-caller limits (CronWorkflow
`concurrencyPolicy: Forbid`, workflow-level `parallelism`) only bind within
one caller and cannot see the others.

## Decision

Cross-workflow concurrency limits live in **exactly one mechanism**:
template-level semaphores backed by the
[manifests/workflow-semaphores.yaml](../../manifests/workflow-semaphores.yaml)
ConfigMap — "the only lock type that binds across ALL callers, including
workflow-of-workflows invocations via templateRef." Two keys exist:

| Key | Value | Held by | Bounds |
| --- | --- | --- | --- |
| `selfie-container-qa` | 4 | `run-container-tests` | concurrent nested-systemd container lanes; the contended resource is registry egress during pulls, not node memory |
| `snosi-vm-qa` | 1 | `run-incus-install-tests`, `run-incus-vm-tests`, `run-incus-disk-tests`, `run-incus-bootc-install-tests`, `run-firn-install-tests`, `run-secure-install-tests` | the VM lanes, which own fixed disk/TPM fixtures on the host and must run one at a time |

The semaphore is declared on the *template* that does the work, so every
route to that work queues on the same counter. Workflow-level knobs remain
only as local politeness (e.g. the QA pipeline's `parallelism: 2`), never
as the correctness mechanism.

## Consequences

- Any new caller of an existing lane — another cron, a manual submit, a
  future watcher — is automatically capacity-bounded with zero
  coordination.
- Changing a limit is a one-line git change to the ConfigMap, reconciled by
  Argo CD ([ADR-0001](0001-two-argocd-applications-and-hand-applied-bootstrap.md)).
- Queue time counts against workflow deadlines, so lane
  `activeDeadlineSeconds` must budget for semaphore wait (the QA pipeline
  uses 14400 s for 1-hour suites for exactly this reason).
- A watcher or parent that merely *submits* work must not hold the
  semaphore while waiting — which is one of the two reasons
  `secure-install-watch` submits standalone Workflows
  ([ADR-0010](0010-vacuous-success-is-forbidden.md)).
- Argo Workflows v4 removed the singular `semaphore`/`mutex` fields; this
  repo uses the plural forms everywhere
  ([docs/ops/bootstrap.md](../ops/bootstrap.md)).

## Alternatives considered

- **CronWorkflow `concurrencyPolicy: Forbid` alone:** still set on every
  cron, but it only prevents one cron overlapping itself; it cannot stop a
  poller and a manual run colliding on the VM fixture.
- **Workflow-level semaphores:** do not bind across separate Workflow
  objects reaching a template via `templateRef`; precisely the gap this
  decision closes.
- **Kubernetes ResourceQuota / pod anti-affinity:** bounds pods, not
  logical fixtures; cannot express "one VM lane at a time" when the lanes
  differ in shape.

## References

- Shapes: [README.md — Repository layout](../../README.md#repository-layout)
  (workflow-semaphores annotation)
- Implemented by:
  [manifests/workflow-semaphores.yaml](../../manifests/workflow-semaphores.yaml),
  [argo/workflow-templates/run-container-tests.yaml](../../argo/workflow-templates/run-container-tests.yaml),
  the `synchronization.semaphores` blocks in every `run-*` VM lane
- Builds on: [ADR-0001](0001-two-argocd-applications-and-hand-applied-bootstrap.md)
- Related: [ADR-0006](0006-host-daemon-access-by-mount-never-ssh.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md)
