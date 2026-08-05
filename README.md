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

Those belong to the incus VM lane below. The container lanes cover the large
majority of what breaks, cheaply and fast.

---

## The incus VM lane

`run-incus-vm-tests` boots a published ISO in an incus VM with **UEFI Secure
Boot and a vTPM**, then asserts on the serial console. It covers the one class
of failure no container lane can see: if the shim/UKI signing chain is broken,
OVMF refuses to boot the image and only this lane notices.

**How a pod drives incus without SSH.** The host's `/usr/bin/incus` is a shell
wrapper that sets `PATH` and `LD_LIBRARY_PATH` over `/usr/incus`. The workflow
pod mounts that directory plus `/var/lib/incus/unix.socket` and runs the
host's own client — version-matched by construction, with nothing to keep in
sync and no shell on the host. `swtpm` comes from the same directory, so the
vTPM needs no host package.

```bash
kubectl create -f argo/snosi-vm-boot-test.yaml
```

Guests are named after the workflow and deleted from an `EXIT` trap, so a
failure mid-run cannot leak a VM holding host memory and vTPM state. Runs
serialize on the `snosi-vm-qa` semaphore.

ISOs are cached on the host at `/var/lib/snosi-lab/iso` and validated against
the origin's ETag on every run — `snow-live-latest.iso` is a stable name whose
bytes change, so caching on filename alone would pin the lane to a stale
artifact.

### The three VM lanes

| Template | Submit file | What it proves |
|---|---|---|
| `run-incus-vm-tests` | `snosi-vm-boot-test.yaml` | A published ISO boots under Secure Boot. The ISO is signed by a trusted chain, so this lane runs `secureboot=true`. |
| `run-incus-disk-tests` | `snosi-disk-boot-test.yaml` | The published `*-ab.disk.raw.xz` artifact boots and runs, with its signed `SHA256SUMS` verified before use. |
| `run-incus-install-tests` | `snosi-install-test.yaml` | **The native A/B installer** — partitioning, EROFS + dm-verity root, LUKS `/var`, TPM enrollment. |
| `run-incus-bootc-install-tests` | `snosi-bootc-install-test.yaml` | **The bootc install path** — `bootc install to-disk` from the live ISO, then a real bootc host. |

The two install lanes are the ones that matter most. Booting an image tests an
artifact; only running an installer tests the thing that *creates* the on-disk
layout — none of verity, LUKS, the A/B slots, or a bootc deployment exists in a
shipped image at all.

snosi ships both install paths and they fail differently: native A/B is a
signed sysupdate image with dm-verity, while bootc installs the OCI image
itself and owns its own deployment layout. Neither lane substitutes for the
other.

The native A/B install lane is verified green against `cayo-ab`:

```
installed and verified: cayo-ab (verity+luks+erofs, secureboot=false, skip-mok=true)
  verity=ok            dm-verity backing the root device
  luks=ok              /var is LUKS
  varsource=/dev/mapper/var
  rootfs=erofs
  osrelease=cayo-20260805002345
  bootc=absent         expected — native A/B does not use bootc
```

`verity`, `luks`, and `rootfs=erofs` are the gating checks: each is created by
`snosi-install` at install time and exists in no published image, so a lane
that passes them has genuinely exercised the installer.

### The bootc install lane is red, and the failure is real

`run-incus-bootc-install-tests` works — and it is reporting a genuine defect,
not a harness problem:

```
bootc install to-disk --wipe --filesystem ext4 /dev/sda   → succeeds
first boot of the installed system:
  DEPEND  Dependency failed for sysroot.mount - Root Partition.
  DEPEND  Dependency failed for initrd-root-fs.target
  DEPEND  Dependency failed for bootc-root-setup.service
  Reached target emergency.target - Emergency Mode.
```

`bootc install` reports success against `ghcr.io/frostyard/snow:latest`, but the
resulting system's initrd cannot mount its root partition. The composefs karg
is present on the kernel command line; the failure is in the initrd, before
`bootc-root-setup.service` runs.

Reproduced twice, with and without `--generic-image`, so it is not an artifact
of how the lane invokes bootc. This is exactly the class of bug no container
lane and no image-boot lane can see, and it is why the install lanes exist.

Filed as [frostyard/snosi#504](https://github.com/frostyard/snosi/issues/504).
The failing unit names `/dev/gpt-auto-root`, so root discovery is going through
systemd's `gpt-auto-generator` and that device never appears.

**Related:** the snow image ships no `/usr/lib/bootc/install/` configuration, so
a plain `bootc install to-disk` fails with `No root filesystem specified` and
the lane must pass `--filesystem ext4`. Fedora and Bluefin bootc images ship an
install-configuration TOML that supplies this. Adding one to snosi would make
the documented invocation work unmodified for users. Filed as
[frostyard/snosi#505](https://github.com/frostyard/snosi/issues/505).

### Driving a guest with no agent and no SSH

snosi images ship no incus guest agent, and a live ISO has no provisioned SSH
key — so there is no obvious way to run a command inside a guest. systemd
solves it: it reads credentials from **SMBIOS type 11**, and the well-known
`systemd.extra-unit.<name>` credential defines an entire unit from thin air.

The lane passes qemu two credentials via `raw.qemu` — the unit to run, and a
`multi-user.target` drop-in that pulls it in — and the guest executes it at
boot with no cooperation from the image:

```
systemd[1]: Received regular credentials: systemd.extra-unit.snosi-qa-install.service, ...
systemd[1]: Acquired 2 regular credentials, 0 untrusted credentials.
```

Results come back on the serial console, which is the only channel that exists
before a system is installed. The same mechanism carries the post-install
assertions.

### Secure Boot: a real gap, and it needs a decision

Booting a published `*-ab` disk image with `secureboot=true` fails:

```
Verification failed: (0x1A) Security Violation
```

OVMF is right to refuse it. snosi signs its UKI with its own MOK, and nothing
has enrolled that MOK into a fresh firmware's db. Enrollment is a step the
**installer** performs — and `snosi-install` stages it as a one-time
MokManager prompt at first boot, which no unattended run can answer.

So the install lane currently runs `--skip-mok` with `secureboot=false`. That
covers everything the installer builds, but leaves the signed boot chain
uncovered end to end. Two ways to close it:

1. **Lab-side.** Pre-seed the VM's OVMF variable store with the snosi MOK
   before first boot (e.g. `virt-fw-vars` against the per-instance
   `qemu.nvram`). No installer change; the lab simulates an operator who
   already enrolled the key.
2. **Installer-side.** An unattended enrollment path in `snosi-install` — for
   example a flag that enrolls directly into db when the firmware permits it,
   rather than staging a MokManager prompt.

(1) is less invasive and testable today; (2) is closer to what a real user
does. This is a decision for the snosi maintainer, not the lab.

---

## Image lanes

| Image | Tag | Schedule | Suites | Last verified |
|---|---|---|---|---|
| `ghcr.io/frostyard/snow` | `latest` | digest poll, `0 */3 * * *` | smoke | 20 passed |
| `ghcr.io/frostyard/cayo` | `latest` | digest poll, `20 */3 * * *` | smoke | 14 passed, 6 skipped |
| `ghcr.io/frostyard/snowfield` | `latest` | digest poll, `40 */3 * * *` | smoke | 20 passed |

cayo skips the desktop scenarios by design — it is the headless server image,
and the suite gates them on variant so one set of features runs unmodified
across the whole family.

Enable or disable a lane by setting `spec.suspend` in its CronWorkflow and
pushing. Doing it through git keeps the set of active lanes reviewable in
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
- **No artifact storage.** Argo needs a configured artifact repository to save
  output artifacts, and the lab has no object store. The VM lane's full serial
  console therefore goes to the workflow log rather than an artifact — 400
  lines on failure, 40 on success. Standing up an object store would let the
  whole console and the behave `results.json` be retained per run.
- **The signed boot chain is not covered end to end.** Both install lanes run
  `secureboot=false` because of the MOK gap above. Closing it is a decision,
  not a task — see "Secure Boot: a real gap".
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
