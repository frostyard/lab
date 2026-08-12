# 0008 — Host media cache with origin-fingerprint sidecars

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The VM lanes consume multi-GB ISOs and disk images from
repository.frostyard.org (core
[ADR-0009](https://github.com/frostyard/core/blob/main/docs/adr/0009-single-artifact-origin-repository-frostyard-org.md)).
Re-downloading per run wastes the same egress the semaphores exist to
protect, so media is cached on the host at `/var/lib/snosi-lab/iso` and
`/var/lib/snosi-lab/disks` (core
[ADR-0004](https://github.com/frostyard/core/blob/main/docs/adr/0004-product-namespaced-filesystem-tiers.md)).
But some URLs are *moving names* (`…-latest.iso` behind a redirect): a cache
keyed only on filename silently tests a stale artifact, which is worse than
having no lane. And a download interrupted mid-transfer must never be
mistaken for a complete file on the next run.

## Decision

Cached media is validated against an **origin fingerprint stored in a
sidecar file** `<path>.fingerprint`, with two rules:

- **Fingerprint precedence:** `ETag`, else `Last-Modified`, else
  `Content-Length` (stored as `etag:…` / `lm:…` / `len:…`; an
  unobtainable fingerprint is `unknown` and always re-downloads). Headers
  are lowercased before matching — this image's awk silently ignores
  `IGNORECASE`, which once made every ETag miss and fall through to
  content-length, a far weaker staleness signal
  ([argo/workflow-templates/run-incus-vm-tests.yaml](../../argo/workflow-templates/run-incus-vm-tests.yaml)).
- **Atomic materialization:** downloads go to `<path>.part` and are
  `mv`-ed into place only on success, so a partial file can never be a
  cache hit; the sidecar is written after the move.

**Immutable versioned names skip the check.** Where the resolved filename
embeds the build stamp (core
[ADR-0006](https://github.com/frostyard/core/blob/main/docs/adr/0006-os-artifact-versions-are-utc-timestamps.md)),
the name itself is the cache key: the native-installer ISO ("the installer
ISO redirects to an immutable versioned object, so the filename itself is
the cache key — no ETag check needed",
[run-incus-install-tests.yaml](../../argo/workflow-templates/run-incus-install-tests.yaml))
and the disk artifact, which is additionally digest-verified against the
GPG-signed index
([run-incus-disk-tests.yaml](../../argo/workflow-templates/run-incus-disk-tests.yaml)).

The logic is **inlined in each lane rather than shared**: Argo script
templates have no include mechanism, and each lane stays a self-contained
script readable top to bottom.

## Consequences

- A republished `-latest` ISO is picked up on the next run; a stable one
  costs one HEAD request instead of a multi-GB download.
- A `.fingerprint` sidecar sits next to every mutable cached file; deleting
  a cached file without its sidecar is safe (size mismatch/absence forces
  re-download), as is deleting the whole cache directory.
- Inlining means the copies can drift — and already have: the bootc lane's
  copy checks only `etag` → `len`, skipping `Last-Modified`
  ([run-incus-bootc-install-tests.yaml](../../argo/workflow-templates/run-incus-bootc-install-tests.yaml)).
  Fixes to the pattern must be applied to every lane that carries it
  (currently `run-incus-vm-tests`, `run-secure-install-tests`,
  `run-incus-bootc-install-tests`).
- The cache is host-local state outside GitOps; it is bounded only by
  `/var` capacity and has no eviction beyond manual deletion.

## Alternatives considered

- **No cache:** multi-GB downloads per run, multiplied by the poll cadence;
  the egress cost is the cluster's scarcest resource.
- **Checksum verification instead of fingerprints:** strictly stronger, but
  the mutable `-latest` endpoints publish no stable checksum to compare
  against; where a signed index exists (the disk lane) the digest *is*
  checked.
- **A shared script library (ConfigMap-mounted or image-baked):** would
  deduplicate the block at the cost of an indirection every lane reader
  must chase and a second artifact to version; rejected while the copy
  count stays small.
- **Kubernetes-native caching (PVC-backed image cache, registry mirror):**
  heavier machinery for the same result; hostPath is already the model for
  lane scratch space ([ADR-0006](0006-host-daemon-access-by-mount-never-ssh.md)).

## References

- Implemented by:
  [argo/workflow-templates/run-incus-vm-tests.yaml](../../argo/workflow-templates/run-incus-vm-tests.yaml),
  [argo/workflow-templates/run-secure-install-tests.yaml](../../argo/workflow-templates/run-secure-install-tests.yaml),
  [argo/workflow-templates/run-incus-bootc-install-tests.yaml](../../argo/workflow-templates/run-incus-bootc-install-tests.yaml);
  versioned-name variants in
  [run-incus-install-tests.yaml](../../argo/workflow-templates/run-incus-install-tests.yaml),
  [run-incus-disk-tests.yaml](../../argo/workflow-templates/run-incus-disk-tests.yaml),
  [run-firn-install-tests.yaml](../../argo/workflow-templates/run-firn-install-tests.yaml)
- Builds on: core
  [ADR-0004](https://github.com/frostyard/core/blob/main/docs/adr/0004-product-namespaced-filesystem-tiers.md),
  core [ADR-0006](https://github.com/frostyard/core/blob/main/docs/adr/0006-os-artifact-versions-are-utc-timestamps.md),
  core [ADR-0009](https://github.com/frostyard/core/blob/main/docs/adr/0009-single-artifact-origin-repository-frostyard-org.md),
  core [ADR-0014](https://github.com/frostyard/core/blob/main/docs/adr/0014-single-gpg-trust-root.md)
- Related: [ADR-0006](0006-host-daemon-access-by-mount-never-ssh.md)
