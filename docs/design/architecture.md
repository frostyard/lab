# Design: Lab architecture

Living document. Rationale: [ADR-0001](../adr/0001-two-argocd-applications-and-hand-applied-bootstrap.md), [ADR-0002](../adr/0002-digest-gated-qa-with-compare-and-swap-state.md), [ADR-0004](../adr/0004-one-way-evidence-pipeline.md).

> A GitOps-driven QA pipeline for [snosi](https://github.com/frostyard/snosi)
> bootc images, running on the lab k3s host (`minideb`, 10.0.1.175 since 2026-09-26).
> Everything is declared in git, reconciled by Argo CD, and orchestrated by
> Argo Workflows.

Modelled on [`projectbluefin/lab`](https://github.com/projectbluefin/lab), with
the parts that assume Fedora replaced by ones that suit a Debian/mkosi image
family.

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

## Stack

| Layer | Project | Role |
|---|---|---|
| Kubernetes | [k3s](https://k3s.io) | Single-node cluster on the lab k3s host (`minideb`, 10.0.1.175 since 2026-09-26) |
| CI/CD | [Argo Workflows](https://argoproj.github.io/argo-workflows/) | DAG pipeline orchestration |
| GitOps | [Argo CD](https://argo-cd.readthedocs.io) | Declarative cluster state from git |
| Tests | [behave](https://behave.readthedocs.io) | BDD suites, from [`frostyard/testsuite`](https://github.com/frostyard/testsuite) |
| Images | [bootc](https://bootc-dev.github.io/bootc/) + OCI | Atomic OS image format |

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
pipeline. The Application split and its hand-applied bootstrap boundary are
recorded in
[ADR-0001](../adr/0001-two-argocd-applications-and-hand-applied-bootstrap.md);
the digest-gated trigger flow in
[ADR-0002](../adr/0002-digest-gated-qa-with-compare-and-swap-state.md).
For the poller, three registry fetch failures emit `none`; a ConfigMap read
failure is different and makes the poll fail rather than masquerading as empty state.

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

Those belong to the incus VM lane below. The container lanes cover the large
majority of what breaks, cheaply and fast.

## Image lanes

| Image | Tag | Schedule (UTC) | Suites |
|---|---|---|---|
| `ghcr.io/frostyard/snow` | `latest` | digest poll, `0 */3 * * *` | smoke |
| `ghcr.io/frostyard/floe` | `latest` | digest poll, `20 */3 * * *` | smoke |
| `ghcr.io/frostyard/snowfield` | `latest` | digest poll, `40 */3 * * *` | smoke |

All three CronWorkflows are declared `suspend: false` in git. For retained
polls and QA evidence, see the [dashboard](https://frostyard.github.io/lab/)
and its source, the committed [`runs.json`](../../site/src/data/runs.json).
`generated` marks when that snapshot was collected; the publisher replaces it
as runs arrive and old records leave its retention window. Neither the table
nor a missing record establishes current cluster or image status. See
[quality triage](../quality.md#triaging-product-qa-evidence) before attributing
any failure to an image.

floe skips the desktop scenarios by design — it is the headless server image,
and the suite gates them on variant so one set of features runs unmodified
across the whole family.

Enable or disable a lane by setting `spec.suspend` in its CronWorkflow and
pushing. Doing it through git keeps the set of active lanes reviewable in
history, and `selfHeal` would revert a `kubectl patch` anyway.

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
├── docs/                         # see docs/README.md for the full index
│   ├── adr/                      # repo-local decision records
│   ├── ops/bootstrap.md          # from-zero cluster setup
│   └── quality.md                # quality signals, evidence, and known gaps
├── policies/                     # executable agent-governance policy and checker
└── Justfile                      # operator wrappers
```

## Known gaps

- **`display-manager.service` is not linked in the snow image.** `gdm.service`
  ships with no `[Install]` section, so the `systemctl enable gdm.service` in
  snosi's postinst is a no-op and `/etc/systemd/system/display-manager.service`
  is absent from the built image. Whether GDM still starts on a real boot is
  unresolved — a container cannot answer it. Tracked for the VM lane.
- **Published summaries are not full diagnostics.** The collector commits
  bounded, retained workflow summaries to `runs.json`, not per-step Behave or
  full VM console artifacts. Diagnose a failure using the original workflow
  and logs while retained; a missing/expired workflow is not a passed run.
- **No artifact storage.** Argo needs a configured artifact repository to save
  output artifacts, and the lab has no object store. The VM lane's full serial
  console therefore goes to the workflow log rather than an artifact — 400
  lines on failure, 40 on success. Standing up an object store would let the
  whole console and the behave `results.json` be retained per run.
- **The native A/B signed boot chain is not covered end to end.** Its install
  lane runs `secureboot=false` because of [the MOK gap above](vm-lanes.md#native-ab-secure-boot-remains-a-separate-gap). Successful bootc
  secure runs enforce Secure Boot, but that is a separate path; closing native
  A/B coverage is still a decision — see ["Native A/B Secure Boot remains a
  separate gap"](vm-lanes.md#native-ab-secure-boot-remains-a-separate-gap).
- **No A/B update or rollback coverage.** The install lane proves a system gets
  built correctly; it does not yet stage a `systemd-sysupdate` run, switch
  slots, and boot the other side. That is the natural next lane and the
  machinery (SMBIOS credentials, console assertions) already exists.
- **The install lane needs 8 GiB of guest RAM.** `snosi-install` stages a UKI
  copy in `/var/tmp`, a tmpfs sized from guest memory; at 4 GiB the install
  fails partway with `objcopy: ...[.initrd]: No space left on device`. Worth
  knowing outside the lab — a real user on a low-memory machine hits the same
  wall, with the same unhelpful error.
- **No registry pull-through cache.** Every lane pulls from ghcr.io directly.
  Fine at three lanes on a 3-hour poll; revisit if lane count grows.

## References

- Rationale: [ADR-0001](../adr/0001-two-argocd-applications-and-hand-applied-bootstrap.md),
  [ADR-0002](../adr/0002-digest-gated-qa-with-compare-and-swap-state.md),
  [ADR-0004](../adr/0004-one-way-evidence-pipeline.md)
- VM lanes: [design/vm-lanes.md](vm-lanes.md)
- Reporting: [design/reporting.md](reporting.md)
- Contract: [specs/bootc-lifecycle-lane.md](../specs/bootc-lifecycle-lane.md)
- Operating: [ops/operating.md](../ops/operating.md)
