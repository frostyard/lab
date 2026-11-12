# Design: VM lanes

Living document. Rationale: [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md), [ADR-0006](../adr/0006-host-daemon-access-by-mount-never-ssh.md).

## The incus VM lane

The VM lanes boot a guest in an incus VM with **UEFI Secure Boot and a vTPM**,
then assert on the serial console. They cover the one class of failure no
container lane can see: if the shim/UKI signing chain is broken, OVMF refuses
to boot the image and only a VM lane notices.

**How a pod drives incus without SSH.** The host's `/usr/bin/incus` is a shell
wrapper that sets `PATH` and `LD_LIBRARY_PATH` over `/usr/incus`. The workflow
pod mounts that directory plus `/var/lib/incus/unix.socket` and runs the
host's own client — version-matched by construction, with nothing to keep in
sync and no shell on the host. `swtpm` comes from the same directory, so the
vTPM needs no host package.

```bash
kubectl create -f argo/firn-install-test.yaml
```

Guests are named after the workflow and deleted from an `EXIT` trap, so a
failure mid-run cannot leak a VM holding host memory and vTPM state. Runs
serialize on the `snosi-vm-qa` semaphore.

Installer ISOs are cached on the host at `/var/lib/snosi-lab/iso` and checked
against their pinned SHA-256 on every run. The Firn lane pins its ISO by URL and
SHA-256 from snosi's signed `SHA256SUMS.gpg` index; a cached copy whose hash no
longer matches is deleted and the run fails (`iso sha256_mismatch`,
`argo/workflow-templates/run-firn-install-tests.yaml`). Core
[ADR-0023](https://github.com/frostyard/core/blob/main/docs/adr/0023-verified-pinned-downloads.md)
requires this for every downloaded artifact.

### The VM lanes

| Template | Submit file | What it proves |
|---|---|---|
| `run-firn-install-tests` | `firn-install-test.yaml`, `bootc-only-iso-install-test.yaml` | **The bootc firn install lanes** — a pinned installer ISO installs to a blank virtual disk; the guest then boots under the selected Secure Boot and encryption settings. |
| `run-snow-bootc-lifecycle` | `scripts/snow-lifecycle-dev-submit.sh`, or the [lane spec](../specs/bootc-lifecycle-lane.md) | **The installed host through an update** — a secure UEFI vTPM Firn install of N, then five fresh boots: `installed-n`, `stage`, `boot-n-plus-1`, `rollback`, `boot-n`. It proves install N, the upgrade to N+1 and the rollback to N. |

**Retired lanes.** 4 VM templates were removed on 2026-11-11
([ADR-0020](../adr/0020-retire-the-native-ab-and-live-iso-vm-lanes.md)):
`run-incus-install-tests` (native A/B installer), `run-incus-disk-tests`
(published A/B disk artifact), `run-incus-vm-tests` (ISO boot) and
`run-incus-bootc-install-tests` (bootc install mechanics). snosi
[ADR-0018](https://github.com/frostyard/snosi/blob/main/docs/adr/0018-remove-native-ab-and-nbc-lanes.md)
removed native A/B, so their artifacts return HTTP 404; no snosi build makes
the live ISO they booted; and their `-latest` downloads had no SHA-256, which
core ADR-0023 forbids. Git history keeps them.

#### The bootc firn install lanes

`firn` is the single snosi installer (core ADR-0027/0028). The runner accepts
only bootc recipes; the A/B family was removed from this firn lane. One
`run-firn-install-tests` invocation is a cell (`family=bootc`, `image`,
`encryption`, `secureboot`). `firn-install-test.yaml` fans out seven bootc
cells with `withItems`: floe, snow and snowfield across selected
auto-unlockable encryption and Secure Boot settings, plus one interactive
`luks-passphrase` cell (floe, Secure Boot off). It generates a recipe TOML per
cell, drives `firn install <recipe> --confirm <disk> --json-progress` against a
fresh blank virtual disk, then boots the result. For tpm2 encrypted cells,
**booting is the unlock proof**. The `luks-passphrase` cell prompts for the
passphrase at every boot, so the lane types the public test passphrase into the
serial console with `incus console` (at most 2 prompts, ADR-0015) and then runs
the normal probe and judge. Cells serialize on the `snosi-vm-qa` semaphore (one
VM at a time), so the matrix runs back-to-back.

`bootc-only-iso-install-test.yaml` is a narrower manual run: snow, floe and
sundog, all unencrypted with Secure Boot on. Snowfield is untested in this
submit file by decision; its presence in the broader matrix does not make it
evidence for this run. On minideb (10.0.1.175), never selfie, a passing cell
proves a fresh ISO-to-blank-virtual-disk install, the Secure Boot MOK chain,
first boot with a login prompt ready (display manager, tty1 getty or serial
getty), a btrfs root (directly, or a composefs overlay backed by a btrfs
`/sysroot`), and the installed bootc image ref and digest. This is VM evidence,
**not hardware qualification**; it does not prove graphical login or upgrades,
and the `luks-passphrase` cell proves the serial passphrase unlock in a VM, not
the graphical prompt or real hardware. See
[quality evidence limits](../quality.md#bootc-only-iso-install-evidence).

Each `run-firn-install-tests` result line ends in `firn=<version>`, read from
`firn --version` inside the installer VM. `firn=unknown` means the installer
booted but printed no version; `firn=not_reached` means the run failed before
the installer booted. The version is recorded, not judged.

The post-install probe also guards the btrfs fs-verity kernel defect
([snosi#1130](https://github.com/frostyard/snosi/issues/1130); Firn's
`compress=zstd:1` mount, [firn#119](https://github.com/frostyard/firn/issues/119);
upstream fix `254e15591ee4` in kdave/linux). It emits
`cfs_compressed_objects=<N>`, `fsverity_errors=<N>` and `kernel=<release>`.
The judge fails the cell when `cfs_compressed_objects` is above zero, and when
`fsverity_errors` is missing, not a number, or above zero on a kernel that is
not on the known-defect list (`7.2.11+deb14-amd64` through
`7.2.15+deb14-amd64`, `7.3.3+deb14-amd64`, `7.3.4+deb14-amd64`, and
`7.2.15-1-surface`, Snowfield's linux-surface kernel; see
[quality.md](../quality.md#fs-verity-regression-checks-snosi1130)).
`7.2.15+deb14-amd64` is Debian testing linux 7.2.15-1, signed by
`linux-signed-amd64` 7.2.15+1. Its Debian changelog has no btrfs entry;
upstream `ChangeLog-7.2.15`, `-7.3.2` and `-7.3.3` do not contain the fix.
`7.3.3+deb14-amd64` is Debian linux 7.3.3-1, signed by `linux-signed-amd64`
7.3.3+1. Its Debian changelog has no btrfs fs-verity fix; the upstream
`ChangeLog-7.3.1` to `-7.3.3` do not contain the fix, and v7.3.3
`fs/btrfs/extent_io.c` still calls `fsverity_verify_folio()` in
`btrfs_verify_folio()`.
`7.3.4+deb14-amd64` is Debian linux 7.3.4-1, signed by `linux-signed-amd64`
7.3.4+1. Its Debian changelog has no btrfs fs-verity fix, upstream
`ChangeLog-7.3.4` does not contain the fix, and v7.3.4
`fs/btrfs/extent_io.c` is byte-identical to v7.3.3. The lifecycle scripts
replace these per-entry comment lines with 1 pointer to
[quality.md](../quality.md#fs-verity-regression-checks-snosi1130), which holds
the evidence for every entry, so that the dev-submit template stays under its
size bound (`tests/test_snow_lifecycle_dev_submit.py`).
On a listed kernel a positive count is recorded with
`fsverity_known_defect=<kernel>` and does not fail the cell. The informational
`kernel_btrfs_verity_canary=defect|clean|skipped` never fails a cell. The Snow
lifecycle lane judges `fsverity_errors` and `kernel` the same way, and keeps the matching kernel lines (never the hashes) in
`fsverity.txt` next to `checks.txt`. The lifecycle lane also records its
lockdown mode and command-line evidence in `checks.txt` and `cmdline.txt`.
These fields are evidence only and do not add a lifecycle gate (see
[kept kernel lines](../quality.md#kept-kernel-lines-fsveritytxt-lab174)).
Details:
[quality evidence](../quality.md#fs-verity-regression-checks-snosi1130).

The firn ISO is published by snosi's `build-native-images.yml` (build-iso →
promote-iso), installing `firn` from the `frostyard-firn` apt package (snosi
PR #699 switched the published installer from `native-installer` to
`firn-installer`). Both submit files require an immutable ISO URL and lowercase
SHA-256 from the signed `SHA256SUMS.gpg` index, plus product-specific bootc
digests from `skopeo inspect`; replace every `REPLACE_*` placeholder before
submitting with `kubectl create -f`, not `apply`. A moving `-latest-` ISO is
not a pin and cannot establish which installer bytes were exercised.

The install lanes are the ones that matter most. Booting an image tests an
artifact; only running an installer tests the thing that *creates* the
on-disk layout — the partition table, LUKS, TPM enrollment and the bootc
deployment exist in no shipped image at all, so a lane that passes them has genuinely exercised the
installer.

### What the retired lanes left behind

The secure installer lane, `run-secure-install-tests` (the external
Dakota/bootc-installer/Fisherman path), was removed on 2026-08-12: `firn` is
the single installer (core ADR-0027/0028), and the firn install matrix now owns
secure-boot and encrypted bootc coverage. The mechanics lane,
`run-incus-bootc-install-tests`, was removed on 2026-11-11 (ADR-0020). See the
[status at a glance](../roadmap.md#status-at-a-glance) for the evidence and
limits of both retired lanes.

2 lessons stay with the lab. A lane that has never once been green is evidence
about the lane, not about the product under test. And a `grep -q` at the end of
a pipe inverts a result under `set -o pipefail`: `grep` exits on its first
match, the writer dies of SIGPIPE (141), and the pipeline fails exactly when it
found the marker. Capture to a file, then grep the file.

## Driving a guest with no agent and no SSH

snosi images ship no incus guest agent, and an installer ISO has no provisioned
SSH key — so there is no obvious way to run a command inside a guest. systemd
solves it: it reads credentials from **SMBIOS type 11**, and the well-known
`systemd.extra-unit.<name>` credential defines an entire unit from thin air.

The lane passes qemu two credentials via `raw.qemu` — the unit to run, and a
`multi-user.target` drop-in that pulls it in — and the guest executes it at
boot with no cooperation from the image:

```
systemd[1]: Received regular credentials: systemd.extra-unit.snosi-qa-install.service, ...
systemd[1]: Acquired 2 regular credentials, 0 untrusted credentials.
```

The SMBIOS table is bounded at 64 KiB **in total**, so the lane bounds the sum of
a boot's credential values, not only each one (`fail smbios_size`; dev run
`dev-snow-bootc-lifecycle-6swrx` lost every credential of a desktop report boot
at about 68500 characters). Results come back on the serial console, which is the
only channel that exists before a system is installed. The same mechanism carries the post-install
assertions. The protocol is recorded in
[ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md);
how the lanes reach the host's incus daemon at all is
[ADR-0006](../adr/0006-host-daemon-access-by-mount-never-ssh.md).

## References

- Rationale: [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0006](../adr/0006-host-daemon-access-by-mount-never-ssh.md),
  [ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md),
  [ADR-0015](../adr/0015-firn-lane-answers-the-luks-passphrase-prompt-on-serial.md),
  [ADR-0020](../adr/0020-retire-the-native-ab-and-live-iso-vm-lanes.md)
- Architecture: [design/architecture.md](architecture.md)
- Contract: [specs/bootc-lifecycle-lane.md](../specs/bootc-lifecycle-lane.md)
- Evidence limits: [quality.md](../quality.md)
