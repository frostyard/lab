# 0020 — Retire the native A/B and live-ISO VM lanes

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

4 incus VM lanes test artifacts that snosi no longer builds or publishes:

| Template | Artifact under test | State of the artifact |
|---|---|---|
| `run-incus-install-tests` | `isos/native/v1/snosi-native-installer-latest-x86-64.iso`, product `floe-ab` | HTTP 404 |
| `run-incus-disk-tests` | `os/native/v1/<product>/x86-64/*-ab.disk.raw.xz` | `os/native/v1/floe/x86-64/SHA256SUMS`: HTTP 404 |
| `run-incus-vm-tests` | `isos/snow-live-latest.iso` | frozen: last modified 2026-08-21, and no snosi build makes it |
| `run-incus-bootc-install-tests` | boots `isos/snow-live-latest.iso` as the vehicle for `bootc install to-disk` | the same frozen ISO |

[snosi ADR-0018](https://github.com/frostyard/snosi/blob/main/docs/adr/0018-remove-native-ab-and-nbc-lanes.md)
removed the native A/B profiles, the native installer payload and the native
OS publication. snosi builds no live ISO: its only ISO is the Firn installer
at `isos/native/v1/`. So a manual run of the first 2 lanes fails on a 404,
and the other 2 test an ISO that no build produces
([lab#220](https://github.com/frostyard/lab/issues/220)).

The 3 ISO lanes also download a moving `-latest` name with no SHA-256 check.
Core [ADR-0023](https://github.com/frostyard/core/blob/main/docs/adr/0023-verified-pinned-downloads.md)
requires a pinned URL and an expected SHA-256 for every download, and names
installer media into QA lanes. [ADR-0008](0008-media-cache-with-fingerprint-sidecars.md)
accepted the origin fingerprint in place of a checksum only because those
`-latest` endpoints publish no stable checksum.

`run-firn-install-tests` already covers the installer that snosi ships. It
pins its ISO by URL and SHA-256 from the signed `SHA256SUMS.gpg` index, and
it covers bootc installs of Floe, Snow and Snowfield with and without Secure
Boot and encryption. The Snow bootc lifecycle lane covers the installed bootc
host through upgrade and rollback.

## Decision

The lab retires the 4 lanes. Their WorkflowTemplates, their submit files and
their dashboard lane names are removed. No lane moves to the Firn ISO: the
Firn lane already tests that ISO, and a second lane on it would test the same
bytes twice.

This decision supersedes these parts of earlier ADRs:

- [ADR-0007](0007-cross-workflow-concurrency-via-template-semaphores.md): the
  `snosi-vm-qa` semaphore stays at 1. Its holders are now
  `run-firn-install-tests` and `run-snow-bootc-lifecycle`. The 4 retired
  templates no longer hold it.
- [ADR-0008](0008-media-cache-with-fingerprint-sidecars.md): the
  origin-fingerprint check for mutable `-latest` names, and the versioned-name
  variants of the native installer ISO and the A/B disk artifact, covered only
  retired lanes. No lane now downloads a moving name. The media cache stays for
  the Firn lane, which keys it on a pinned, SHA-256-verified ISO, as core
  ADR-0023 requires.

A new VM lane that downloads media pins it by URL and SHA-256 (core ADR-0023);
it does not bring back the fingerprint-only check.

## Consequences

- The lab no longer boots a published disk image or a live ISO. A boot under
  enforced Secure Boot is still covered: by the Firn cells with
  `secureboot=true` and by the lifecycle lane.
- Direct `bootc install to-disk` from a mechanics image is no longer tested in
  the lab. snosi's own CI still runs the mechanics build; Firn is the install
  path that snosi ships (core ADR-0027, ADR-0028).
- The bootc install lane's copy of the fs-verity judge and the known-defect
  kernel list goes with it. The list now lives in 2 templates: the Firn judge
  and the lifecycle `run.sh`. A test keeps them identical.
- Earlier ADRs (0004, 0005, 0006, 0008, 0009) cite the retired templates as
  examples or implementations. ADRs are immutable, so those links stay; the
  templates remain in git history.
- Retained runs of the retired templates still show on the dashboard until
  they leave Argo's retention window, labelled by their raw template name.

## Alternatives considered

- **Move `run-incus-vm-tests` and `run-incus-bootc-install-tests` to the Firn
  ISO:** the Firn lane already boots that ISO under Secure Boot and installs
  from it, with a pinned SHA-256. A second lane would add no coverage and a
  second copy of the download and judge logic to keep in step.
- **Keep the lanes and pin the frozen live ISO by SHA-256:** this meets core
  ADR-0023, but it tests an artifact that no build makes and no user gets.
- **Keep the templates but stop running them:** a lane that cannot pass and
  is never run is dead code that still reads as coverage in the docs.

## References

- Shapes: [design/vm-lanes.md](../design/vm-lanes.md),
  [roadmap.md](../roadmap.md), [quality.md](../quality.md)
- Supersedes in part: [ADR-0007](0007-cross-workflow-concurrency-via-template-semaphores.md),
  [ADR-0008](0008-media-cache-with-fingerprint-sidecars.md)
- Builds on: snosi [ADR-0018](https://github.com/frostyard/snosi/blob/main/docs/adr/0018-remove-native-ab-and-nbc-lanes.md),
  core [ADR-0023](https://github.com/frostyard/core/blob/main/docs/adr/0023-verified-pinned-downloads.md)
- Issue: [lab#220](https://github.com/frostyard/lab/issues/220)
