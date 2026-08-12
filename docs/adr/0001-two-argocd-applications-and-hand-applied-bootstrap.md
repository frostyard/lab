# 0001 — Two locally-sourced Argo CD Applications with a hand-applied bootstrap boundary

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The lab is a single-node k3s cluster (`selfie`) reconciled from this
repository by Argo CD with `prune: true` and `selfHeal: true`. Aggressive
pruning is what makes git authoritative — a manual `kubectl apply` is
reverted on the next reconcile — but it also means anything an Application
owns is deleted when that Application is removed or its source shrinks.
Argo Workflows CRDs are the worst case: deleting the CRD cascades into
deleting every `Workflow` object in the cluster, i.e. the entire run
history the lab exists to produce.

Two kinds of git-managed resources have different blast radii: pipeline
definitions (WorkflowTemplates), which change often, and infrastructure
(RBAC, controller config, CronWorkflows, state ConfigMaps), which must
survive pipeline churn.

## Decision

Exactly **three** Argo CD Applications exist, of which exactly **two**
source from this repository, with non-overlapping paths:

| Application | Source | Owns |
| --- | --- | --- |
| `frostyard-lab` | this repo, `argo/workflow-templates/` | WorkflowTemplates ([argocd/application.yaml](../../argocd/application.yaml)) |
| `frostyard-lab-infra` | this repo, `manifests/` | RBAC, controller ConfigMap, semaphores, digest state, CronWorkflows, namespaces ([argocd/infra-application.yaml](../../argocd/infra-application.yaml)) |
| `argo-workflows` | upstream Helm chart `argo-workflows` 1.0.23 | the workflow controller only ([argocd/argo-workflows-app.yaml](../../argocd/argo-workflows-app.yaml)) |

All three run `prune: true`, `selfHeal: true`, and `CreateNamespace=false`.

**CRDs and the `argo`/`argocd` namespaces are deliberately outside GitOps.**
They are hand-applied bootstrap prerequisites
([docs/ops/bootstrap.md](../ops/bootstrap.md) steps 1–3): the Helm chart is
pinned with `crds.install: false` because a Helm-managed CRD would be pruned
along with its Application, taking every Workflow object with it. For the
same reason the chart sets `serviceAccount.create: false` and
`configMap.create: false` — the `argo` ServiceAccount/RBAC and the
controller ConfigMap are owned by `manifests/` so the two Applications never
fight over the same object.

## Consequences

- `git push main` is the only way to change a pipeline; a hand-applied
  WorkflowTemplate is reverted by `selfHeal` on the next reconcile.
- Removing an Application can never delete the Workflows CRDs or the run
  history.
- Bootstrap is a documented manual sequence
  ([docs/ops/bootstrap.md](../ops/bootstrap.md)) that must be kept current
  by hand; it is idempotent and re-runnable against a live cluster.
- The shape is enforced offline:
  `tests/test_kubernetes_manifests.py::test_argocd_applications_own_exact_nonoverlapping_sources`
  pins the exact Application set, their exact `spec.source` blocks, and the
  prune/selfHeal/CreateNamespace settings, so a fourth Application or a path
  change fails CI before it reaches the cluster.
- Runtime state that lives inside a git-managed object needs an explicit
  carve-out (`ignoreDifferences` on `image-polling-digests` `/data`) — see
  [ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md).

## Alternatives considered

- **One Application for the whole repo:** couples infrastructure lifetime to
  pipeline churn and gives one bad sync the maximum blast radius; the path
  split keeps the failure domains separate.
- **Helm-managed CRDs (`crds.install: true`):** the default, rejected
  because pruning the Application would cascade into deleting every
  Workflow object.
- **`CreateNamespace=true`:** would let Argo CD own namespaces it should
  treat as prerequisites; namespace lifecycle stays in the bootstrap
  sequence (and `manifests/namespaces.yaml` for the test namespaces).

## References

- Shapes: [docs/ops/bootstrap.md](../ops/bootstrap.md),
  [README.md — Architecture](../../README.md#architecture)
- Implemented by: [argocd/application.yaml](../../argocd/application.yaml),
  [argocd/infra-application.yaml](../../argocd/infra-application.yaml),
  [argocd/argo-workflows-app.yaml](../../argocd/argo-workflows-app.yaml)
- Enforced by: [tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py)
- Related: [ADR-0002](0002-digest-gated-qa-with-compare-and-swap-state.md)
  (the state carve-out inside `frostyard-lab-infra`)
