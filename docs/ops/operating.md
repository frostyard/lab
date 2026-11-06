# Operating the lab

Run the recipes from the repository root. They use the current kubeconfig
context; there is no separate Argo server endpoint because this lab disables
the Argo Workflows server. Before operating the lab, verify that
`kubectl config current-context` names the intended cluster and that
`kubectl get namespaces argo argocd` succeeds with your current credentials.

Install these workstation clients:

| Client | Version expectation | Used by |
|---|---|---|
| [`just`](https://just.systems/) | No project-specific version is pinned; use a maintained release that can parse this `Justfile`. | Every `just ...` command. |
| [`kubectl`](https://kubernetes.io/docs/tasks/tools/) | Stay within the supported one-minor version skew of the v1.36.2+k3s1 server (v1.35–v1.37). | `setup-argocd`, `status`, `refresh`, `smoke`, `runs`, and `validate`. |
| [Argo Workflows CLI](https://argo-workflows.readthedocs.io/en/latest/walk-through/argo-cli/) (`argo`, not the Argo CD `argocd` CLI) | Use v4.0.8 to match the installed Workflows CRDs/controller. | `qa`, `watch`, and `logs`. |

Both `kubectl` and `argo` use the current kubeconfig context and need access to
the `argo` namespace; the `status`, `refresh`, and bootstrap recipes also need
access to `argocd`. The recipes do not select a context for you. Confirm the
clients before relying on a wrapper:

```bash
just --version
kubectl version
argo version --client
```

Common operator commands and the client each wrapper invokes:

```bash
just status     # kubectl: Application sync/health + enabled lanes
just smoke      # kubectl: one-off smoke run against snow:latest
just qa IMAGE TAG SUITES VARIANT  # argo: submit an arbitrary QA run
just watch      # argo: watch the most recently submitted workflow
just runs       # kubectl: recent run history
just logs       # argo: follow the most recent workflow
just validate   # kubectl: server-side dry-run every YAML before pushing
just refresh    # kubectl: force Argo CD to re-read git now
```

Setting up a cluster from scratch: [`docs/ops/bootstrap.md`](../ops/bootstrap.md).

Repository quality signals and their current limits:
[`docs/quality.md`](../quality.md).

The deny-by-default automated-contributor policy and its local validator:
[`policies/`](../../policies/).

Public aggregate dashboards and pull request metrics:
[`docs/metrics/`](../metrics/).

## The `qa-controlled` tag of a lifecycle run

A [bootc lifecycle run](../specs/bootc-lifecycle-lane.md) stages N+1 from the
product's `qa-controlled` tag. After a run, leave the tag in place; a later run
moves it to its own N+1. Do **not** delete it through the GitHub package UI:
GHCR deletes a package version by digest, which deletes the N+1 image itself
with its version and `latest` tags.
