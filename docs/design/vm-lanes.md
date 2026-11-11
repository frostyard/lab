# Design: VM lanes

Living document. Rationale: [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md), [ADR-0006](../adr/0006-host-daemon-access-by-mount-never-ssh.md).

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
| `run-firn-install-tests` | `firn-install-test.yaml`, `bootc-only-iso-install-test.yaml` | **The bootc firn install lanes** — a pinned installer ISO installs to a blank virtual disk; the guest then boots under the selected Secure Boot and encryption settings. |

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
`7.2.15+deb14-amd64`, `7.3.3+deb14-amd64`, and `7.2.15-1-surface`, Snowfield's linux-surface
kernel; see [quality.md](../quality.md#fs-verity-regression-checks-snosi1130)).
`7.2.15+deb14-amd64` is Debian testing linux 7.2.15-1, signed by
`linux-signed-amd64` 7.2.15+1. Its Debian changelog has no btrfs entry;
upstream `ChangeLog-7.2.15`, `-7.3.2` and `-7.3.3` do not contain the fix.
`7.3.3+deb14-amd64` is Debian linux 7.3.3-1, signed by `linux-signed-amd64`
7.3.3+1. Its Debian changelog has no btrfs fs-verity fix; the upstream
`ChangeLog-7.3.1` to `-7.3.3` do not contain the fix, and v7.3.3
`fs/btrfs/extent_io.c` still calls `fsverity_verify_folio()` in
`btrfs_verify_folio()`.
On a listed kernel a positive count is recorded with
`fsverity_known_defect=<kernel>` and does not fail the cell. The informational
`kernel_btrfs_verity_canary=defect|clean|skipped` never fails a cell. The bootc
install lane and the Snow lifecycle lane judge `fsverity_errors` and `kernel`
the same way, and keep the matching kernel lines (never the hashes) in
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
[secure installer status and blocker history](../roadmap.md#status-at-a-glance)
for the complete evidence and limits of the retired lane.

## Driving a guest with no agent and no SSH

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

The SMBIOS table is bounded at 64 KiB **in total**, so the lane bounds the sum of
a boot's credential values, not only each one (`fail smbios_size`; dev run
`dev-snow-bootc-lifecycle-6swrx` lost every credential of a desktop report boot
at about 68500 characters). Results come back on the serial console, which is the
only channel that exists before a system is installed. The same mechanism carries the post-install
assertions. The protocol is recorded in
[ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md);
how the lanes reach the host's incus daemon at all is
[ADR-0006](../adr/0006-host-daemon-access-by-mount-never-ssh.md).

## Native A/B Secure Boot remains a separate gap

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

## References

- Rationale: [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0006](../adr/0006-host-daemon-access-by-mount-never-ssh.md),
  [ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md),
  [ADR-0015](../adr/0015-firn-lane-answers-the-luks-passphrase-prompt-on-serial.md)
- Architecture: [design/architecture.md](architecture.md)
- Contract: [specs/bootc-lifecycle-lane.md](../specs/bootc-lifecycle-lane.md)
- Evidence limits: [quality.md](../quality.md)
