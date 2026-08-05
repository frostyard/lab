# frostyard lab

> A GitOps-driven QA pipeline for [snosi](https://github.com/frostyard/snosi)
> bootc images, running on the single-node `selfie` k3s cluster.
> Everything is declared in git, reconciled by Argo CD, and orchestrated by
> Argo Workflows.

Modelled on [`projectbluefin/lab`](https://github.com/projectbluefin/lab), with
the parts that assume Fedora replaced by ones that suit a Debian/mkosi image
family.

---

## What this is

snosi publishes bootc OCI images continuously. This repo is the machinery that
answers "is the image that just got published actually good?" without anyone
watching:

1. A CronWorkflow polls the registry digest for an image tag.
2. If the digest moved, the QA pipeline runs against **that exact digest**.
3. Suites run inside the image itself — booted as a nested systemd container.
4. The new digest is recorded **only after QA passes**, so a failure retries on
   the next poll instead of being silently marked as seen.

No hypervisor, no SSH, no persistent test machines. The container lanes are
Kubernetes-native end to end.

---

## Stack

| Layer | Project | Role |
|---|---|---|
| Kubernetes | [k3s](https://k3s.io) | Single-node cluster (`selfie`) |
| CI/CD | [Argo Workflows](https://argoproj.github.io/argo-workflows/) | DAG pipeline orchestration |
| GitOps | [Argo CD](https://argo-cd.readthedocs.io) | Declarative cluster state from git |
| Tests | [behave](https://behave.readthedocs.io) | BDD suites, from [`frostyard/testsuite`](https://github.com/frostyard/testsuite) |
| Images | [bootc](https://bootc-dev.github.io/bootc/) + OCI | Atomic OS image format |

---

## Architecture

```
image-poll CronWorkflow
        │
        ▼
  skopeo inspect → digest
        │
        ▼
  compare with `image-polling-digests` ConfigMap
        │
        ├─ unchanged ──────────────► exit cleanly
        │
        └─ changed ────────────────► snosi-qa-pipeline (pinned to that digest)
                                     │
                                     └─ run-container-tests, one lane per suite
                                        │
                                        ├─ podman pull <image>@<digest>
                                        ├─ podman run --systemd=always /sbin/init
                                        ├─ apt-get install python3-behave
                                        └─ behave tests/<suite>/features
                                     │
                                     ▼
                          persist digest ONLY on success
```

**GitOps loop:**

```
git push main
    │
    ▼
Argo CD reconciles
    │
    ├─ argo/workflow-templates/ ──► WorkflowTemplates  (App: frostyard-lab)
    └─ manifests/               ──► CronWorkflows, RBAC, config
                                                       (App: frostyard-lab-infra)
```

WorkflowTemplates are never applied by hand. `selfHeal: true` reverts a manual
`kubectl apply` on the next reconcile, so git is the only way to change a
pipeline.

---

## Why the tests run in a container, not a VM

The suites boot the bootc image as a nested systemd container
(`podman run --systemd=always … /sbin/init`) and run `behave` against that live
system. Tests then read exactly like assertions a user would make on a running
machine, and a lane costs a pull plus ~20 seconds of boot rather than a VM.

The trade is real and bounded — a container cannot assert on:

- **the kernel** (it runs the host's), so `snowfield`'s linux-surface kernel is
  not covered by its container lane
- **a graphical seat**, so "GDM actually starts a session" is out of reach;
  the desktop suite asserts installation and configuration only
- **the disk layout** — EROFS, dm-verity, Secure Boot, TPM/LUKS `/var`, and the
  A/B update path all need a real boot

Those belong to the incus VM lane (phase 2, not yet built). The container lanes
cover the large majority of what breaks, cheaply and fast.

---

## Image lanes

| Image | Tag | Schedule | Suites | Status |
|---|---|---|---|---|
| `ghcr.io/frostyard/snow` | `latest` | digest poll, `0 */3 * * *` | smoke | suspended |
| `ghcr.io/frostyard/cayo` | `latest` | digest poll, `20 */3 * * *` | smoke | suspended |
| `ghcr.io/frostyard/snowfield` | `latest` | digest poll, `40 */3 * * *` | smoke | suspended |

Lanes ship suspended. Flip `spec.suspend` to `false` in the CronWorkflow and
push — enabling a lane through git keeps the set of active lanes reviewable in
history, and `selfHeal` would revert a `kubectl patch` anyway.

---

## Suites

Defined in [`frostyard/testsuite`](https://github.com/frostyard/testsuite).

| Suite | Covers |
|---|---|
| `smoke` | Boots to usable systemd, no unexpected failed units, os-release provenance, shipped toolchain runs |
| `system` | bootc/composefs contracts, filesystem layout, image metadata |
| `sysext` | `systemd-sysext` and `updex` behaviour against shipped extensions |

`smoke` is implemented. `system` and `sysext` are declared in the pipeline's
validation list but not yet populated — adding features to those directories in
the testsuite repo is all that is needed to light them up.

---

## Repository layout

```
lab/
├── argocd/
│   ├── application.yaml          # App: frostyard-lab       → argo/workflow-templates
│   ├── infra-application.yaml    # App: frostyard-lab-infra → manifests
│   └── argo-workflows-app.yaml   # App: argo-workflows      → upstream Helm chart
│
├── argo/
│   ├── workflow-templates/       # ← Argo CD auto-syncs these
│   │   ├── image-poller.yaml         digest compare → QA → persist
│   │   ├── snosi-qa-pipeline.yaml    validate suites → fan out lanes
│   │   └── run-container-tests.yaml  nested systemd boot + behave
│   └── snosi-smoke-test.yaml     # submit file: one-off manual run
│
├── manifests/                    # ← Argo CD auto-syncs these
│   ├── argo-rbac.yaml                argo ServiceAccount + Roles
│   ├── workflow-controller-configmap.yaml
│   ├── workflow-semaphores.yaml      cross-workflow concurrency caps
│   ├── image-polling-digests.yaml    digest state (values owned by cluster)
│   ├── image-poll-*.yaml             one CronWorkflow per lane
│   ├── namespaces.yaml
│   └── orphan-pod-gc.yaml
│
├── docs/ops/bootstrap.md         # from-zero cluster setup
└── Justfile                      # operator wrappers
```

---

## Operating it

```bash
just status     # Application sync/health + which lanes are enabled
just smoke      # one-off smoke run against snow:latest
just runs       # recent run history
just logs       # follow the most recent workflow
just validate   # server-side dry-run every YAML before pushing
just refresh    # force Argo CD to re-read git now
```

Setting up a cluster from scratch: [`docs/ops/bootstrap.md`](docs/ops/bootstrap.md).

---

## Known gaps

- **`display-manager.service` is not linked in the snow image.** `gdm.service`
  ships with no `[Install]` section, so the `systemctl enable gdm.service` in
  snosi's postinst is a no-op and `/etc/systemd/system/display-manager.service`
  is absent from the built image. Whether GDM still starts on a real boot is
  unresolved — a container cannot answer it. Tracked for the VM lane.
- **No result publication.** Bluefin's lab publishes per-suite results back into
  the repo and renders them with Astro. Results here live in the workflow's
  output parameters and the pod logs only.
- **No VM lane.** See "Why the tests run in a container".
- **No registry pull-through cache.** Every lane pulls from ghcr.io directly.
  Fine at three lanes on a 3-hour poll; revisit if lane count grows.
