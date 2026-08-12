# 0006 — Lanes reach the host's incus by mounting /usr/incus and the API socket, never SSH

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The VM lanes need real virtualization — incus VMs with vTPM and OVMF — but
they run as Kubernetes pods on `selfie`, whose OS (cayo, an immutable snosi
server image) has a read-only `/usr`. Nothing can be installed on the host;
lanes cannot assume host tooling beyond what the image ships. An
incus client also has to match its daemon's version, and shipping a client
in a lane image invites skew with whatever the host runs.

## Decision

Pods that drive incus mount three things from the host, with hostPath
volumes, and talk to the daemon over its Unix socket
([argo/workflow-templates/run-incus-install-tests.yaml](../../argo/workflow-templates/run-incus-install-tests.yaml)):

- `/usr/incus` (read-only) — the host's own incus client binaries and
  libraries, put on `PATH`/`LD_LIBRARY_PATH`
  (`/usr/incus/bin`, `/usr/incus/lib`). Client and daemon are
  **version-matched by construction**: the lane runs the exact binary the
  host ships.
- `/var/lib/incus/unix.socket` — the daemon API. No TCP listener, no
  credentials, no SSH to the host anywhere in the repo.
- Lane-specific scratch/caches under `/var/lib/snosi-lab/` (core
  [ADR-0004](https://github.com/frostyard/core/blob/main/docs/adr/0004-product-namespaced-filesystem-tiers.md))
  and, for the install lane's MOK pre-seed,
  `/var/lib/incus/storage-pools` (writable — it edits the guest's own OVMF
  varstore).

Every lane that touches incus (or `/dev/kvm`) pins itself to the host with
`nodeSelector: node-role.kubernetes.io/control-plane: "true"`.

**What the host cannot supply, the lane carries.** The secure-install lane
runs qemu/swtpm/OVMF entirely inside its own container — "installing
qemu/swtpm/sshpass on [the host] is not merely undesirable, it is
impossible" — and touches the host only for `/dev/kvm` and a scratch
directory; its Microsoft-enrolled firmware comes from the container's own
`ovmf` package
([argo/workflow-templates/run-secure-install-tests.yaml](../../argo/workflow-templates/run-secure-install-tests.yaml)).

## Consequences

- No SSH keys, host credentials, or remote-execution path exist for the
  lab host; the pod security boundary is the hostPath mounts themselves,
  which are declared per-lane and reviewable in git.
- Incus upgrades on the host cannot break lanes through client/daemon skew;
  the client travels with the daemon.
- The lanes are pinned to one node; the incus lanes do not scale out, which
  is acceptable — they are serialized anyway by the `snosi-vm-qa`
  semaphore ([ADR-0007](0007-cross-workflow-concurrency-via-template-semaphores.md)).
- Lanes that bring their own toolchain pay an apt-install on every run
  (bounded by lane deadlines) in exchange for zero host footprint.
- The mounts require `runAsUser: 0` in those lanes; resource bounds on
  every container are still enforced by
  `tests/test_kubernetes_manifests.py::test_workload_containers_declare_cpu_and_memory_bounds`.

## Alternatives considered

- **SSH from the pod to the host:** a credential to manage, an audit
  surface, and an invitation to run arbitrary host commands; the socket
  mount grants exactly the incus API and nothing else.
- **Shipping an incus client in the lane image:** version skew with the
  host daemon; rejected in favor of mounting the host's own binaries.
- **Installing QA tooling on the host:** impossible on an immutable image
  with read-only `/usr`, and undesirable even where possible — the host
  stays a stock cayo install.
- **incus over HTTPS:** requires enabling a network listener and minting
  client certificates on a cluster that deliberately has no such surface.

## References

- Shapes: [README.md — The incus VM lane](../../README.md#the-incus-vm-lane)
- Implemented by:
  [argo/workflow-templates/run-incus-install-tests.yaml](../../argo/workflow-templates/run-incus-install-tests.yaml),
  [argo/workflow-templates/run-incus-vm-tests.yaml](../../argo/workflow-templates/run-incus-vm-tests.yaml),
  [argo/workflow-templates/run-incus-disk-tests.yaml](../../argo/workflow-templates/run-incus-disk-tests.yaml),
  [argo/workflow-templates/run-incus-bootc-install-tests.yaml](../../argo/workflow-templates/run-incus-bootc-install-tests.yaml),
  [argo/workflow-templates/run-secure-install-tests.yaml](../../argo/workflow-templates/run-secure-install-tests.yaml)
- Builds on: core
  [ADR-0004 — Product-namespaced filesystem tiers](https://github.com/frostyard/core/blob/main/docs/adr/0004-product-namespaced-filesystem-tiers.md)
- Related: [ADR-0007](0007-cross-workflow-concurrency-via-template-semaphores.md)
