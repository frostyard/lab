---
mode: agent
description: Change cluster infrastructure under manifests/ or argocd/.
---

# Change a cluster manifest

Read first:

- `manifests/` — namespaces, RBAC, semaphores, the `image-polling-digests`
  ConfigMap, the image-poll CronWorkflows, orphan pod GC.
- `argocd/` — the Applications themselves. Header comments in each file record
  *why* a setting is the way it is; read them before changing one.
- `docs/ops/bootstrap.md` — what is a bootstrap prerequisite (CRDs, the `argo`
  namespace) versus what is synced.

## Rules

- `manifests/` is reconciled by the `frostyard-lab-infra` Application. Change
  git, never the cluster.
- Ownership is deliberate and must not be duplicated: the `argo` ServiceAccount
  and RBAC belong to `manifests/argo-rbac.yaml`, controller config to
  `manifests/workflow-controller-configmap.yaml`. Do not let the Helm chart
  take over an object another Application owns.
- CRDs stay out of Helm's hands — `prune: true` would delete them, and every
  Workflow object with them.
- Do not hand-edit `image-polling-digests` content in git to "fix" a run; the
  pipeline owns that data at runtime.
- Preserve the explanatory header comments, and extend them when you change a
  choice they document.

## Check your work

- `just validate` — server-side dry run against the live cluster.
- `just status` — Applications and CronWorkflows still reconciled and healthy.
- Note in the PR which Application (`frostyard-lab-infra`, `argo-workflows`)
  is affected and whether a bootstrap step changed.
