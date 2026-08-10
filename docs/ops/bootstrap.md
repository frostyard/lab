# Bootstrap — from a bare k3s node to a reconciling lab

This is the exact sequence used to build the `selfie` cluster, in order. Every
step is idempotent; re-running the whole guide against a live cluster is safe.

The split to keep in mind: **CRDs and the `argo` namespace are prerequisites,
not GitOps-managed resources.** Argo CD Applications sync with `prune: true`, so
a Helm-managed CRD would be deleted along with its Application — taking every
`Workflow` object in the cluster with it.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| x86_64 host | `selfie`: 32 cores, 125 GiB RAM, 3.4 TiB free on `/var` |
| k3s | v1.36.2+k3s1. Stock install; Traefik and local-path are fine as-is. |
| `kubectl` on your workstation | Talking to the node over the network is enough — no step needs a shell on the host. Use a release within one minor of the v1.36.2+k3s1 server (v1.35–v1.37). |
| `just` on your workstation | Required for the documented `just ...` wrappers; use a maintained release. The underlying bootstrap commands can instead be run directly. |
| Argo Workflows `argo` CLI on your workstation | Optional for bootstrap, but required by `just qa`, `just watch`, and `just logs` (including the first-run example below). Use v4.0.8 to match the installed Workflows CRDs/controller. This is not the Argo CD `argocd` CLI. |

The wrappers use the current kubeconfig context rather than selecting a cluster
or an Argo API endpoint. After copying the kubeconfig, confirm that it names the
intended cluster and that your credentials can access both operator namespaces:

```bash
kubectl config current-context
kubectl get namespaces argo argocd
```

See the [operator client and command mapping](../../README.md#operating-it) for
which recipes invoke `kubectl` versus `argo`.

On an image-based host (snosi, Bluefin) install k3s with
`INSTALL_K3S_BIN_DIR=/var/usrlocal/bin` so the binary survives an OS update. On
snosi specifically, the [`k3s` sysext](https://github.com/frostyard/snosi)
handles this — `updex` installs it and there is nothing to place by hand.

### Getting a kubeconfig off the node

k3s writes `/etc/rancher/k3s/k3s.yaml` root-only, pointed at `127.0.0.1`:

```bash
ssh -t <node> 'sudo install -Dm644 /etc/rancher/k3s/k3s.yaml /tmp/k3s.yaml'
mkdir -p ~/.kube
ssh <node> 'cat /tmp/k3s.yaml' | sed 's#127.0.0.1#<node-ip>#' > ~/.kube/config
chmod 600 ~/.kube/config
kubectl get nodes
```

k3s includes the node IP in the API server certificate's SAN list by default, so
no extra TLS configuration is needed.

---

## 1. Install Argo CD

```bash
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side --force-conflicts -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
```

`--server-side` is required, not stylistic. The `applicationsets.argoproj.io`
CRD exceeds the 262 kB limit on the `kubectl.kubernetes.io/last-applied-
configuration` annotation that client-side apply writes, and the apply fails
partway through with `metadata.annotations: Too long`.

The lab has no public surface and no Argo CD ingress. Reach the UI with
`kubectl port-forward svc/argocd-server -n argocd 8080:443` when needed; the
initial password is in the `argocd-initial-admin-secret` Secret.

---

## 2. Install Argo Workflows CRDs

The controller itself is installed by Argo CD in step 4. Only the CRDs are
placed by hand, for the pruning reason above.

```bash
kubectl create namespace argo --dry-run=client -o yaml | kubectl apply -f -

AWF=v4.0.8
BASE="https://raw.githubusercontent.com/argoproj/argo-workflows/${AWF}/manifests/base/crds/full"
for crd in workflows workflowtemplates cronworkflows workflowtaskresults \
           workflowartifactgctasks workflowtasksets workfloweventbindings \
           clusterworkflowtemplates; do
  kubectl apply --server-side --force-conflicts -f "${BASE}/argoproj.io_${crd}.yaml"
done
```

> **Argo Workflows v4 note.** v4.0 removed the singular `mutex`, `semaphore`,
> and CronWorkflow `schedule` fields deprecated in v3.6. Everything in this repo
> uses the plural `mutexes`, `semaphores`, and `schedules`. A template copied
> from an older lab will fail validation on those three fields.

---

## 3. Seed the controller's prerequisites

The Helm chart is configured with `serviceAccount.create: false` and
`configMap.create: false`, so these objects must exist before the controller
starts. Argo CD adopts them in step 5 — applying them now and letting the
`frostyard-lab-infra` Application take ownership later is expected.

```bash
kubectl apply --server-side --force-conflicts \
  -f manifests/argo-rbac.yaml \
  -f manifests/workflow-controller-configmap.yaml \
  -f manifests/workflow-semaphores.yaml \
  -f manifests/image-polling-digests.yaml \
  -f manifests/namespaces.yaml
```

---

## 4. Install the Argo Workflows controller

This Application sources from the upstream Helm repository, not from this git
repo, so it can be applied before the repo exists.

```bash
kubectl apply -f argocd/argo-workflows-app.yaml -n argocd
kubectl -n argo rollout status deploy/argo-workflows-workflow-controller --timeout=300s
```

---

## 5. Close the GitOps loop

```bash
kubectl apply -f argocd/application.yaml -n argocd        # WorkflowTemplates
kubectl apply -f argocd/infra-application.yaml -n argocd  # manifests/
```

Or `just setup-argocd`, which applies all three Applications.

From here, `git push main` is the only step needed to change the cluster.

Verify:

```bash
just status
```

All three Applications should read `Synced` / `Healthy`.

---

## 6. First run

```bash
just smoke  # kubectl
just logs   # argo CLI
```

`just logs` requires the v4.0.8 Argo Workflows CLI from the prerequisites. With
only `kubectl`, use `just runs` to inspect workflow status and
`kubectl logs -n argo <pod>` for a selected workflow pod instead.

The lane pulls the image (several GB on a cold cluster — allow ~5 minutes),
boots it as a nested systemd container, installs `python3-behave` inside it, and
runs the smoke suite.

Once green, enable the scheduled lanes by setting `spec.suspend: false` in
`manifests/image-poll-*.yaml` and pushing.

---

## Failure modes worth knowing

**Pods stuck `Pending` with insufficient ephemeral-storage.**
`run-container-tests` requests 16 GiB and limits at 40 GiB, because podman
unpacks a multi-GB bootc image into an `emptyDir`. `emptyDir` is backed by the
kubelet root, so `/var` must have room for concurrent lanes. The
`selfie-container-qa` semaphore caps that at 4.

**`podman pull` stalls forever.** Each attempt is bounded at 600 s with 4
retries. Blob storage persists across attempts inside a pod, so a retry resumes
rather than restarting; attempts get monotonically faster. If all four fail, the
registry is genuinely unreachable.

**The nested systemd host never becomes ready.** The readiness gate gives 90
probes at 2 s. It accepts `degraded`, which is the correct steady state in a
container — units needing real hardware or a seat cannot start. It waits on
`dbus.service` instead, since every meaningful probe depends on it. On failure
the step dumps `systemctl list-units --failed` and the last 200 journal lines.

**Argo CD reverts a change you made with `kubectl`.** Working as intended.
`selfHeal: true`. Change git.

**Every poll re-runs QA against an unchanged image.** The `data` of
`image-polling-digests` is under `ignoreDifferences` in the infra Application
precisely so Argo CD does not reset the stored digests to `""` on each
reconcile. If that stanza is removed, this is the symptom.
