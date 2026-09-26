# frostyard lab

> A GitOps-driven QA pipeline for [snosi](https://github.com/frostyard/snosi)
> bootc images, running on the lab k3s host (`minideb`, 10.0.1.175 since 2026-09-26).
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
| Kubernetes | [k3s](https://k3s.io) | Single-node cluster on the lab k3s host (`minideb`, 10.0.1.175 since 2026-09-26) |
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
pipeline. The Application split and its hand-applied bootstrap boundary are
recorded in
[ADR-0001](docs/adr/0001-two-argocd-applications-and-hand-applied-bootstrap.md);
the digest-gated trigger flow in
[ADR-0002](docs/adr/0002-digest-gated-qa-with-compare-and-swap-state.md).

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

### The VM lanes

| Template | Submit file | What it proves |
|---|---|---|
| `run-incus-vm-tests` | `snosi-vm-boot-test.yaml` | A published ISO boots under Secure Boot. The ISO is signed by a trusted chain, so this lane runs `secureboot=true`. |
| `run-incus-disk-tests` | `snosi-disk-boot-test.yaml` | Fetches, signature-verifies and boots the published `*-ab.disk.raw.xz`. Green with Secure Boot off. |
| `run-incus-install-tests` | `snosi-install-test.yaml` | **The native A/B installer** — partitioning, EROFS + dm-verity root, LUKS `/var`, TPM enrollment. |
| `run-incus-bootc-install-tests` | `snosi-bootc-install-test.yaml` | **The bootc mechanics tier** — direct `bootc install to-disk` of a `secureboot-capable=false` mechanics image, then a real bootc host. |
| `run-firn-install-tests` | `firn-install-test.yaml` | **The firn install matrix** — `firn`, the single installer (core ADR-0027/0028), driven from its ISO across a fan-out of (family × image × encryption × secure-boot) cells, each from nothing to installed-and-booted. |

#### The firn install matrix

`firn` replaces both fisherman (bootc) and snosi-install (native A/B) as the one
snosi installer. This lane is its analogue of the native-install lane, but a
**matrix**: one `run-firn-install-tests` invocation is a single cell (`family`,
`image`, `encryption`, `secureboot`), and `firn-install-test.yaml` fans out a
representative 12-cell set with `withItems`. It generates a recipe TOML per cell
in the guest, drives `firn install <recipe> --confirm <disk> --json-progress`,
then boots the result — for encrypted cells, **booting is the unlock proof**.
Cells serialize on the `snosi-vm-qa` semaphore (one VM at a time), so the full
matrix runs back-to-back; trim the `withItems` list for a smoke run.

The matrix covers every bootc encryption mode (`none`, `luks-passphrase`,
`tpm2-luks`, `tpm2-luks-passphrase`) and every ab mode (`none`, `luks`,
`tpm2-luks`), Secure Boot on and off in both families (ab pre-seeds the snosi
MOK into the guest varstore, exactly as the native lane does), floe + snow
throughout, snowfield once. The `bootc × tpm2-luks*` cells are the point: they
exercise the encrypted-boot unlock firn ADR-0012 installed but left unproven.

The firn ISO is published by snosi's `build-native-images.yml` (build-iso →
promote-iso), installing `firn` from the `frostyard-firn` apt package (snosi
PR #699 switched the published installer from `native-installer` to
`firn-installer`). `iso-url` points at the `snosi-installer-latest` alias; the
A/B `pubring-path` is `/usr/lib/snosi/os-update-pubring.gpg` (shipped by the
firn-installer mkosi).

The three installer lanes are the ones that matter most. Booting an image tests
an artifact; only running an installer tests the thing that *creates* the
on-disk layout — none of verity, LUKS, the A/B slots, or a bootc deployment
exists in a shipped image at all.

snosi ships native A/B and bootc install paths, and bootc deliberately splits
again into mechanics and secure tiers. Native A/B uses a signed sysupdate image
with dm-verity; bootc owns its deployment layout; secure bootc assembly must go
through the external recipe-driven installer. None of these lanes substitutes
for another.

In the August 2026 run, the native A/B install lane passed against `floe-ab`:

```
installed and verified: floe-ab (verity+luks+erofs, secureboot=false, skip-mok=true)
  verity=ok            dm-verity backing the root device
  luks=ok              /var is LUKS
  varsource=/dev/mapper/var
  rootfs=erofs
  osrelease=floe-20260805002345
  bootc=absent         expected — native A/B does not use bootc
```

`verity`, `luks`, and `rootfs=erofs` are the gating checks: each is created by
`snosi-install` at install time and exists in no published image, so a lane
that passes them has genuinely exercised the installer.

### The disk-artifact lane was red because of a bug in this repo

Corrected 2026-08-05. This section previously argued that a `*-ab.disk.raw` is
a *pre-install* artifact which cannot be expected to boot standalone. That was
wrong on both counts, and it was wrong in the direction that let a broken
harness look like an open question about snosi.

The image is a complete, self-contained bootable system. Inspecting the
published `floe-ab` disk directly:

```
1  esp                    1.0 GiB  vfat    shim + MokManager + systemd-boot + UKI
2  floe_<ver>_v         256.0 MiB  verity  slot A hash
3  floe_<ver>_r           5.0 GiB  erofs   slot A root — populated
4  _empty               256.0 MiB  verity  slot B — empty, awaiting first update
5  _empty                 5.0 GiB  root    slot B — empty
6  var                    4.0 GiB  ext4    plain, NOT LUKS
```

The UKI's embedded cmdline is
`roothash=5c356dcd…9ab76e69 lockdown=integrity console=ttyS0 rd.luks=1 rd.etc.overlay=1`,
and that roothash is exactly the slot-A root partition UUID concatenated with
the verity partition UUID — the systemd convention, correctly formed. Booted
with Secure Boot off it reaches `multi-user.target` **and** `graphical.target`
in about eleven seconds, on a dm-verity `/dev/mapper/root`, with sshd up.

The real cause of the red was this line, in this repo:

```bash
if incus console "${VM}" --show-log 2>/dev/null | grep -qaF "${EXPECT_CONSOLE}"; then
```

`grep -q` exits on the first match and closes the pipe; `incus console` then
dies of SIGPIPE (141); `set -o pipefail` promotes that to a failed pipeline.
The lane therefore reported failure **precisely when it found the marker**. The
other lanes escape this only because they route through a `console_log()`
helper that ends in `|| true`. Fixed by capturing to a file and grepping the
file, which is what the ISO lane always did.

Two lessons worth keeping: a lane that has never once been green is not
evidence about the thing under test, it is evidence about the lane; and one
plausible-sounding narrative ("pre-install artifact") is exactly how a harness
bug acquires the appearance of a product question.

Note that `/var` ships as plain ext4 here, while `snosi-install --encrypt-var`
produces a LUKS `/var`. The two paths genuinely produce different systems, so
this lane and the install lane are not redundant.

### The mechanics install passed in August; the secure lane has moved on

Corrected 2026-08-10 and re-run as `snosi-bootc-install-fkplf`. The mechanics
lane installed the current `snow:mechanics` image and booted it successfully:

```
bootc installed and verified: ghcr.io/frostyard/snow:mechanics (secureboot=false)
  backend=composefs
  booted=ok
  failedunits=0
  osrelease=snow-20260810050914
  rootfs=overlay
```

The old red result was a tier mismatch, not evidence that current bootc images
could not install. The lane had aimed direct `bootc install to-disk` at
`ghcr.io/frostyard/snow:latest`, a `secureboot-capable=true` assembly that must
be installed by the external secure installer. The mechanics template now
checks that label and rejects the combination as not applicable instead of
building a misleading broken deployment. Snosi issues
[#504](https://github.com/frostyard/snosi/issues/504) and
[#505](https://github.com/frostyard/snosi/issues/505) were both closed as not
planned after that diagnosis; the explicit `--filesystem` argument in the
mechanics harness is deliberate.

There was also a real `/dev/gpt-auto-root-luks` failure later in the **secure**
path, but it had a different root cause. Forky systemd 261 moved the GPT-auto
udev links from `99-systemd.rules` into `90-image-dissect.rules`, which dracut
did not include in the UKI initrd. [snosi#520](https://github.com/frostyard/snosi/pull/520)
ships that rule explicitly, and Snosi's secure artifact validation now rejects
an initrd that cannot create the GPT-auto root link.

The secure lane proved that repair repeatedly before it was retired. Its last
successful committed run, `snosi-secure-install-auto-fwzbj`, verified 18/18
assertions on 2026-08-10 with Secure Boot enforced, MOK enrolled, recovery
available, and TPM unlock working. The lane — `run-secure-install-tests`, the
external Dakota/bootc-installer/Fisherman path — was removed on 2026-08-12:
`firn` is the single installer (core ADR-0027/0028), and the firn install
matrix now owns secure-boot + encrypted bootc coverage. See the
[secure installer status and blocker history](docs/roadmap.md#status-at-a-glance)
for the complete evidence and limits of the retired lane.

### Manual Snow bootc lifecycle

[`run-snow-bootc-lifecycle`](argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
is an advisory WorkflowTemplate for Snow bootc only; it is not scheduled,
not a release gate and has no live pass on record here. It instantiates the
existing console, concurrency, logging and non-vacuous-evidence decisions in
[ADR-0005](docs/adr/0005-console-marker-protocol-for-agentless-guests.md),
[ADR-0007](docs/adr/0007-cross-workflow-concurrency-via-template-semaphores.md),
[ADR-0009](docs/adr/0009-no-artifact-store-logs-are-the-surface.md), and
[ADR-0010](docs/adr/0010-vacuous-success-is-forbidden.md).
Offline mocked tests are not published-media or hardware qualification.

Prepare a local UTF-8 JSON object with **exactly** these keys (no extras).
Every value is public: the submission passes JSON in the `argo` process argv,
and Argo workflow/pod metadata and the pod environment expose `manifest-json`
to readers. After tool provisioning, before `qa.py` runs, the runner writes
the env value to a private 0600 working file and unsets it; this does **not**
make the workflow parameter secret. Never put a password, token, recovery key or other
secret in the manifest, workflow parameters, or submission command.

| JSON key | Required value |
|---|---|
| `family` | `snow` (bootc, not snow-ab). |
| `product` | `snow`. |
| `iso_url` | Published immutable versioned `snosi-installer_<iso_version>_x86-64.iso` URL under `https://repository.frostyard.org/isos/native/v1/`; never `latest`. |
| `iso_sha256` | Lowercase 64-hex SHA-256 of that ISO, independently checked against the signed ISO index. |
| `iso_version` | 14-digit ISO version matching the URL and extracted release metadata. |
| `image_n` | `ghcr.io/frostyard/snow@sha256:<64 lowercase hex>` for N. |
| `version_n` | 14-digit OCI image version for N. |
| `version_tag_n` | Immutable `ghcr.io/frostyard/snow:<version_n>` mapping to `image_n`. |
| `image_n_plus_1` | Distinct signed `ghcr.io/frostyard/snow@sha256:<64 lowercase hex>` for N+1. |
| `version_n_plus_1` | 14-digit OCI version newer than N. |
| `version_tag_n_plus_1` | Immutable `ghcr.io/frostyard/snow:<version_n_plus_1>` mapping to `image_n_plus_1`. |
| `target_ref` | Operator-owned, non-`latest` mutable `ghcr.io/frostyard/snow:<controlled-tag>` mapping to N+1; not either version tag. |
| `snosi_commit` | Exact pinned source commit `39cf19887547f83200866e94e5753888706b49fa`. |
| `index_key_sha256` | SHA-256 of public `shared/native-ab/keys/import-pubring.gpg` at that commit. |
| `cosign_key_sha256` | SHA-256 of public `cosign.pub` at that commit. |
| `mok_cert_sha256` | SHA-256 of public `shared/native-ab/keys/mok-2026.crt` at that commit. |
| `trust_fingerprint` | Signed ISO index primary-key fingerprint `F37282A35CB6BDFEBFC8FE775A2EAC5C8216FD68`. |
| `secureboot` | JSON boolean `true`. |
| `encryption` | `tpm2-luks-passphrase`. |
| `timeouts` | Object of integer seconds, each at least 300, with maxima: `iso_boot` 1800, `install` 7200, `installed_boot` 1800, `stage` 3600, `reboot` 1800. |

Before submitting, independently compare the Snosi commit and all three
public key/cert hashes against committed source (not a mutable branch); verify
the ISO checksum with `SHA256SUMS` authenticated by `SHA256SUMS.gpg` and the
pinned fingerprint. That signature covers the **ISO index only**, not the
native A/B update index. Confirm both signed OCI digest references, their
immutable version-tag mappings, and **your ownership** of the nonlatest N+1
target tag; do not race another publisher. There is deliberately no runnable
placeholder digest or tag. After reviewing kube context, Argo permissions,
manifest content and controlled tag ownership, submit explicitly from a local
file (do not paste the JSON into shell history):

```bash
if [[ -n ${MANIFEST_FILE:-} && -s "$MANIFEST_FILE" ]]; then
  argo submit --namespace argo --from workflowtemplate/run-snow-bootc-lifecycle \
    -p "manifest-json=$(<"$MANIFEST_FILE")"
else
  printf '%s\n' 'Set MANIFEST_FILE to a reviewed nonempty public JSON file' >&2
  false
fi
```

This is a manual submission interface, **not** a command run in this repo.
Preflight authenticates the ISO and embedded public keys, records Firn provenance
and its hash, verifies signed OCI images with the pinned cosign key and checks
version/tag mapping. The disposable installer VM then checks the Firn hash and
narrow Firn v1/v2 recipe validation before install (validator only, not full
installed compatibility). Installed guest checks enforce exact signature policy and signed
pulls; read-only `skopeo inspect` tag resolution alone is **not** signature
verification. Both immutable version tags are checked once in preflight; the
controlled mutable target tag is rechecked before install and each phase, and
after stage, N+1 boot and rollback. The lane attempts a secure UEFI
vTPM Firn v1 encrypted-btrfs install, then five distinct fresh boots:
`installed-n`, `stage`, `boot-n-plus-1`, `rollback`, `boot-n`. See
[private evidence and verdict limits](docs/quality.md#manual-snow-bootc-lifecycle-evidence)
before treating any result as evidence.

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
assertions. The protocol is recorded in
[ADR-0005](docs/adr/0005-console-marker-protocol-for-agentless-guests.md);
how the lanes reach the host's incus daemon at all is
[ADR-0006](docs/adr/0006-host-daemon-access-by-mount-never-ssh.md).

### Native A/B Secure Boot remains a separate gap

Booting a published `*-ab` disk image with `secureboot=true` fails:

```
Verification failed: (0x1A) Security Violation
```

OVMF is right to refuse it. snosi signs its UKI with its own MOK, and nothing
has enrolled that MOK into a fresh firmware's db. Enrollment is a step the
**installer** performs — and `snosi-install` stages it as a one-time
MokManager prompt at first boot, which no unattended run can answer.

So the native A/B install lane currently runs `--skip-mok` with
`secureboot=false`. That covers everything the native installer builds, but
leaves its signed boot chain uncovered end to end. Successful external bootc
secure runs have enforced Secure Boot, but they do not prove the native A/B
path. Two ways to close that remaining native gap:

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

| Image | Tag | Schedule (UTC) | Suites |
|---|---|---|---|
| `ghcr.io/frostyard/snow` | `latest` | digest poll, `0 */3 * * *` | smoke |
| `ghcr.io/frostyard/floe` | `latest` | digest poll, `20 */3 * * *` | smoke |
| `ghcr.io/frostyard/snowfield` | `latest` | digest poll, `40 */3 * * *` | smoke |

All three CronWorkflows are declared `suspend: false` in git. For retained
polls and QA evidence, see the [dashboard](https://frostyard.github.io/lab/)
and its source, the committed [`runs.json`](site/src/data/runs.json).
`generated` marks when that snapshot was collected; the publisher replaces it
as runs arrive and old records leave its retention window. Neither the table
nor a missing record establishes current cluster or image status. See
[quality triage](docs/quality.md#triaging-product-qa-evidence) before attributing
any failure to an image.

floe skips the desktop scenarios by design — it is the headless server image,
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
├── docs/                         # see docs/README.md for the full index
│   ├── adr/                      # repo-local decision records
│   ├── ops/bootstrap.md          # from-zero cluster setup
│   └── quality.md                # quality signals, evidence, and known gaps
├── policies/                     # executable agent-governance policy and checker
└── Justfile                      # operator wrappers
```

---

## Operating it

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

Setting up a cluster from scratch: [`docs/ops/bootstrap.md`](docs/ops/bootstrap.md).

Repository quality signals and their current limits:
[`docs/quality.md`](docs/quality.md).

The deny-by-default automated-contributor policy and its local validator:
[`policies/`](policies/).

Public aggregate dashboards and pull request metrics:
[`docs/metrics/`](docs/metrics/).

---

## Reporting

`site/` is an [Astro](https://astro.build) page published to GitHub Pages,
showing per-lane status and recent run history:
**<https://frostyard.github.io/lab/>**

The pipeline-results data flow is one-way: the cluster publishes a git snapshot;
GitHub Pages does not query the cluster:

```
publish-results CronWorkflow (in cluster)
    │  reads the Argo API, regenerates site/src/data/runs.json
    ▼
git push main
    │
    ▼
.github/workflows/pages.yml → builds site/ → GitHub Pages
```

The collector reads the Kubernetes API rather than being wired into each lane,
so workflows appear without per-lane reporting hooks
([ADR-0004](docs/adr/0004-one-way-evidence-pipeline.md); the `unproven`
lane state it carries is
[ADR-0003](docs/adr/0003-unproven-is-distinct-from-failed.md); the product-only
QA evidence refinement is
[ADR-0011](docs/adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md)).
It skips the commit when only the generation timestamp moved, so an idle cluster
does not push a commit every 30 minutes.

Each published run records `name`, `phase`, `started`, `finished`,
`durationSeconds`, `template`, `laneKey`, `qaOutcome`, `kind`, `label`,
`trigger`, `result`, and `checks`. `lanes` groups runs by `laneKey` with
`latest`, retained `runs` count and `everGreen`; `generated` timestamps the
snapshot. Scheduled product polls have distinct keys
`image-poll-{snow,floe,snowfield}-latest` (from the workflow name), despite all
using the `image-poller` template. For those polls, `qaOutcome` is `passed` or
`failed` only with observed Behave scenario counts and a matching terminal
workflow phase; `not-run` means a successful poll with an absent or null Behave
result, and `unknown` means insufficient or ambiguous evidence (including an
empty result string or zero-count failures). For non-product runs,
`qaOutcome: unknown` means image QA is not applicable; the workflow phase is
the relevant status.
`phase: Succeeded` alone can mean unchanged-digest polling, **not**
passing QA; a Failed phase may still carry a Behave result. `result` is a
summary, not a failing step, and empty container `checks` does not mean all
steps passed. `trigger` defaults to `scheduled` unless the workflow supplies a
label; VM/installer runs are manual evidence, not continuous product QA. Other
lanes' phases describe workflow execution only.

Older snapshots may predate `laneKey`/`qaOutcome` publication and group all
product polls under one `image-poller` lane. The dashboard separates those
legacy runs by workflow name but leaves their QA outcome **unknown**, even
when their `result` text looks conclusive. Freshness is measured against `generated`:
poll evidence older than six hours is stale, not current image status; an old
confirmed failure stays a failure with a stale qualifier. Old unknown or not-run
polls remain unverified and count as both unknown and stale; an old pass is not
green. New fields take effect through collector publication, not by
hand-editing `runs.json`.

`e2e/` holds the [Playwright](https://playwright.dev) end-to-end suite. It
builds the site and drives the same static output GitHub Pages serves, asserting
that what `runs.json` contains is what the dashboard renders. Run it with `just
site-e2e`; CI runs it via `.github/workflows/e2e.yml`.

The page is styled with the [frostyard design
system](https://github.com/frostyard/core/tree/main/.agents/skills/frostyard-design), following its Pilothouse
dashboard language: ink surfaces, hairline separation, square corners, cold
ice/sky accents, mono kickers. `site/src/styles/tokens/` is copied **verbatim**
from that repo — change tokens there and re-copy rather than patching them here,
or the next copy silently reverts the edit. Because those tokens define no light
palette, the page is dark-only by design.

`publish-results` is declared **unsuspended** in
`manifests/publish-results.yaml`, scheduled every 30 minutes. Publication
requires a cluster-side `github-token` secret; if absent the job exits without
publishing.
For secret creation instructions, see the header of
[`manifests/publish-results.yaml`](manifests/publish-results.yaml); do not commit
the credential to git.
Neither the manifest nor the committed snapshot proves the credential or
publisher is healthy now. The page always renders the last committed snapshot,
which may be stale.

Locally: `just collect` regenerates the data from the cluster, `just site-dev`
serves the page.

---

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
  lane runs `secureboot=false` because of the MOK gap above. Successful bootc
  secure runs enforce Secure Boot, but that is a separate path; closing native
  A/B coverage is still a decision — see "Native A/B Secure Boot remains a
  separate gap".
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

---

## License

[MIT](LICENSE)
