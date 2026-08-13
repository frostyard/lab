# Roadmap: from a snosi PR to proof that images install and boot

Written 2026-08-05, after a regroup that corrected two wrong conclusions this
lab had been reporting. Read [What each lane actually tests](#what-each-lane-actually-tests)
first — several lane names are misleading, and one of them misled me.

The goal this document works back from:

> A change lands in a PR on snosi. Before it ships, something proves that the
> images that change produces **install** and then **boot** — in both formats,
> under Secure Boot, for every product.

Nothing in this lab proves that yet. This is the path.

---

## Status at a glance

Updated 2026-08-12 after the secure lane's retirement. If you read one
section, read this one.

| Lane | State |
|---|---|
| Container smoke suites (×3 products) | 🟢 |
| Native A/B installer | 🟢 (Secure Boot off — Phase F) |
| Published A/B disk artifact | 🟢 |
| ISO boot, Secure Boot enforced | 🟢 |
| bootc installer (mechanics) | 🟢 — latest committed run `fkplf` |
| **bootc secure installer** | ⚫ retired 2026-08-12 — superseded by the firn install matrix (core ADR-0027/0028); was red on assembly-compatibility validation, first 18/18 green 2026-08-07 |
| firn installer matrix | 🟢 — latest run `firn-install-matrix-sz9rv`, all 10 cells green 2026-08-12 |
| Registry digest poll, orphan GC | 🟢 |

**Every live lane is currently green; the retired secure lane ended red with
prior green evidence.** The secure install path first went green on 2026-08-07
(`snosi-secure-install-5dpkq`, 18 assertions, 0 failed, 0 blocked) against
**published media and a published image** — `snow-live-latest.iso` and
`cayo@sha256:b3375f6c`. It passed repeatedly through
`snosi-secure-install-auto-fwzbj` on 2026-08-10. The next two committed runs,
`4pzln` and `s6mqq`, completed the bootc installation and then failed with
`secure contract has unsupported assembly compatibility`. Their failure is not
the resolved GPT-auto root-discovery defect.

Those successful runs proved end to end: install completes; boots under enforced
Secure Boot with a measured, MOK-signed UKI; lockdown active; LUKS2/Btrfs root unlocked by a single
signed-PCR-11 TPM token; Type #2-only BLS; composefs binding with no root or
LUKS identifier on the kernel command line; complete non-secret provenance;
bootc-managed deployment; the runtime ESP reconciler restoring only what was
deliberately changed; an unattended TPM-unlock reboot; and both recovery paths
— TPM replacement and recovery re-enrolment.

**What those green runs do NOT mean here.** The harness proved a good image
installs and boots. It no longer proves a bad one is refused: the nine-case
negative-fixture requirement was removed by decision on 2026-08-07
([snosi#548](https://github.com/frostyard/snosi/pull/548),
[dakota#31](https://github.com/frostyard/dakota-iso/pull/31)) rather than
implemented. Six of those cases needed published, deliberately-broken, signed
OCI artifacts to be *causal* — valid in every respect except the property under
test — and that cost was judged not worth paying. Signature enforcement itself
is still covered by the shipped `policy.json` and
`snosi test/bootc-container-policy-test.sh RUN_LIVE=1`, and Dakota's shim
`Security Violation` proof is untouched. The specific uncovered gap is a
deliberately-broken image reaching the installer.

### How the secure lane got green

`run-secure-install-tests` drives snosi's Task 9 harness against the real
external installer. Each attempt cleared one genuine defect and exposed the
next — none of them findable without running against real media:

| # | Blocker | Resolution |
|---|---|---|
| 1 | Harness reported `BLOCKED` forever | environment stood up; nothing to build — the runners already existed |
| 2 | Live SSH could not connect | SMBIOS-injected unit ([dakota#16](https://github.com/frostyard/dakota-iso/pull/16)) |
| 3 | `mokPasswordFile is required` | caller-owned file generated on the guest |
| 4 | systemd `261.1-3` vs media `261.2-1` | version floors ([fisherman#14](https://github.com/frostyard/fisherman/pull/14), [snosi#509](https://github.com/frostyard/snosi/pull/509)) |
| 5 | `podman pull` exit 125 | `--signature-policy` is a *pull* flag ([fisherman#15](https://github.com/frostyard/fisherman/pull/15)) |
| 6 | OCI layout `rejected by policy` | scoped local-transport policy ([fisherman#17](https://github.com/frostyard/fisherman/pull/17)) |
| 7 | composefs digest probe `exit status 1` | `--privileged` + store bind-mount ([fisherman#18](https://github.com/frostyard/fisherman/pull/18)) |
| 8 | post-install validation reads `<target>/usr/...`, which composefs does not provide | split target/image roots ([fisherman#19](https://github.com/frostyard/fisherman/pull/19)) |
| 9 | contract rejected: ESP floor `2 GiB` vs the normative `1 GiB` | corrected to the normative figure ([fisherman#20](https://github.com/frostyard/fisherman/pull/20)) |
| 10 | `mmx64.efi` missing — nothing stages the Secure Boot chain onto the ESP | ESP chain staging built ([fisherman#21](https://github.com/frostyard/fisherman/pull/21)) |
| 11 | BLS validator rejects `uki`, the directive bootc actually writes | accept `uki` as well as `efi` ([fisherman#22](https://github.com/frostyard/fisherman/pull/22)) |
| 12 | composefs identity read from BLS `options`, which a UKI entry does not have | read it from the UKI's signed `.cmdline` ([fisherman#23](https://github.com/frostyard/fisherman/pull/23)) |
| 13 | the QA SSH unit killed the `ssh.socket` that was already serving | start SSH only if nothing already is ([dakota#17](https://github.com/frostyard/dakota-iso/pull/17)) |
| 14 | MOK certificate still read from the target root | move both reads to the image root ([fisherman#24](https://github.com/frostyard/fisherman/pull/24)) |
| 15 | post-install LUKS assertion: `$2` expanded by the remote shell | escape it ([dakota#18](https://github.com/frostyard/dakota-iso/pull/18)) |
| 16 | pre-MOK boot found no TPM socket — swtpm dies with its QEMU client | re-arm before the boot ([snosi#510](https://github.com/frostyard/snosi/pull/510)) |
| 17 | ~~pre-MOK boot classified as neither rejection nor boot-through~~ — a symptom of 18, not a blocker | the `ssh.socket` guard was insufficient; prefer the socket outright ([dakota#19](https://github.com/frostyard/dakota-iso/pull/19)) |
| 18 | ESP staged the **unsigned** `shimx64.efi`; firmware refuses it with `Access Denied` before shim runs | stage the `.signed` variants, and check for a signature table ([fisherman#26](https://github.com/frostyard/fisherman/pull/26)) |
| 19 | reading the UKI's sections with `objcopy` (no outfile) **rewrote it in place and dropped its signature** — the installer unsigned its own kernel | read sections with `debug/pe` ([fisherman#28](https://github.com/frostyard/fisherman/pull/28)) |
| 20 | live SSH intermittently unreachable with `ssh.socket` listening and `ssh.service` failed — **open, cause unknown** | diagnostics now unconditional ([dakota#20](https://github.com/frostyard/dakota-iso/pull/20)) |
| 21 | the installed target boots the signed kernel, then drops to **emergency mode**: nothing unlocks the LUKS root | systemd 261 moved the `gpt-auto-root-luks` udev rule into `90-image-dissect.rules`, which dracut does not install — force it in ([snosi#520](https://github.com/frostyard/snosi/pull/520)). **Proven: the target now boots.** |
| 22 | harness read BLS entries from `/boot`, which bootc leaves unmounted unless it is using it | read them off the ESP, located by GPT type GUID ([snosi#524](https://github.com/frostyard/snosi/pull/524)) |
| 23 | recovery runner read `ssh_private_key`; the manifest has always been `ssh_key`. Also matched only `efi` BLS keys when bootc writes `uki`, and ran `objcopy --dump-section` with **no output file against the installed system's UKI** — which would have unsigned the kernel of the machine it was recovering | all three fixed ([dakota#25](https://github.com/frostyard/dakota-iso/pull/25)). The unit test had asserted the *wrong* key name, so it defended the bug; replaced with a check coupling every key a runner reads to what `validate_state` requires |
| 24 | every subsequent failure reported only "runner failed or omitted its completion marker" plus four stray ssh lines | **no fix — diagnostics.** ERR trap naming line/command/status, phase markers, serial tail ([dakota#26](https://github.com/frostyard/dakota-iso/pull/26)). Found the next three defects in three consecutive runs, first try each |
| 25 | `stop_vm` used a bare `return` after a failed test, so it reported failure **exactly when it succeeded** — and succeeded only in the had-to-SIGKILL branch | `return 0` on both graceful paths ([dakota#27](https://github.com/frostyard/dakota-iso/pull/27)). Inert in two EXIT-trap callers; fatal in the one that calls it as a function's last command under `set -e` |
| 26 | swtpm control socket gone between the replacement-TPM VM and the live guest; QEMU does not retry a missing `-chardev` socket | tear down and re-arm against the **same** `--tpmstate` dir ([dakota#28](https://github.com/frostyard/dakota-iso/pull/28)) — reinitializing would have discarded the very TPM the case rotates |
| 27 | `$2` unescaped inside `sudo bash -ceu`, eaten as a positional parameter; `-u` made it fatal inside a process substitution, so the visible symptom was a bogus "expected exactly one LUKS device" | escape it ([dakota#29](https://github.com/frostyard/dakota-iso/pull/29)); test now scans every runner's awk programs |
| 28 | `stop_vm` again: `-f` test then a separate read, racing the `quit` it had just sent — QEMU removes its own pidfile on exit. Killed a **successful** install from its EXIT trap | one tolerant read ([dakota#30](https://github.com/frostyard/dakota-iso/pull/30)) |
| 29 | `BLOCKED: signed causal fixture for unsigned was not supplied` — the last non-green line | requirement removed by decision, not implemented ([snosi#548](https://github.com/frostyard/snosi/pull/548), [dakota#31](https://github.com/frostyard/dakota-iso/pull/31), [lab#14](https://github.com/frostyard/lab/pull/14)) |

**Every one of blockers 23–28 was a first-execution defect in a code path that
had never run end to end — not a regression.** Each was invisible until the one
before it was fixed. The turning point was 24, which fixed nothing and only made
the runner name its own failure; before it, each failure cost a full run to
re-observe and was diagnosed by guessing at shape.

### The secure install now completes

**2026-08-06.** After fourteen blockers, fisherman runs the whole secure install
end to end on real media:

```
{"elapsed_ms":214076,"message":"Installation complete!","type":"complete"}
```

Everything from partitioning through MOK enrollment staging works: LUKS,
policy-checked pull, composefs digest computed and verified, deployment written,
Secure Boot chain staged, contract read and validated, BLS validated, composefs
identity checked against the deployment.

**The remaining blockers are no longer in the installer.** 15 was in dakota's
post-install assertion, 16 and 17 are in snosi's own harness — code that had
never been reached because nothing had ever got this far. That is a different
phase of the work, and the failures look different: less "this call is wrong",
more "this step has never run".

Blocker 17 turned out to be a **symptom, not a cause** — and the cause was back
in the installer after all.

The harness boots the target *before* MOK enrollment and requires one of two
outcomes: shim printing `Security Violation` (the correct rejection), or a
diagnosable boot-through. It saw neither. With the serial console finally
preserved, what it actually saw was:

```
BdsDxe: loading Boot0001 "UEFI Misc Device" from PciRoot(0x0)/Pci(0x2,0x0)
BdsDxe: failed to load Boot0001 "UEFI Misc Device": Access Denied
>>Start PXE over IPv4.
```

`Access Denied` is `EFI_ACCESS_DENIED` — **firmware** rejecting the signature at
the very first hop. shim never ran, so it could never print `Security
Violation`; the harness was watching for a message from a program that had not
started. Its "neither outcome" verdict was correct and precise.

The cause (**blocker 18**) is that Debian installs an unsigned and a signed copy
of the boot chain into the *same directory*, from different packages:

| path | signature |
|---|---|
| `usr/lib/shim/shimx64.efi` | **none** |
| `usr/lib/shim/shimx64.efi.signed` | Microsoft Corporation UEFI CA 2011 |
| `usr/lib/shim/mmx64.efi` | **none** |
| `usr/lib/shim/mmx64.efi.signed` | Debian Secure Boot CA |

The ESP staging added in fisherman#21 took the bare names — the unsigned pair —
and carried a comment asserting they were Microsoft-signed. An assertion in a
comment cannot fail. [fisherman#26](https://github.com/frostyard/fisherman/pull/26)
stages the `.signed` variants and adds a check for a signature table, matching
what snosi's own native-installer has always done. One trap worth recording:
`sbverify --list` exits 0 for signed and unsigned binaries alike, so its
*output* has to be read, not its status.

Diagnosing this was blocked by a familiar problem: the evidence is the guest's
serial console, which lives in the harness's work directory and is deleted on
exit. The lane now runs with `KEEP_VM=1` and copies that console to
`/var/lib/snosi-lab/secure/logs/<workflow>-serial.log`. **Every check from here
on — shim rejection, the enrolled boot, TPM unlock — is judged from that
console**, so it is worth having permanently rather than one run at a time.

Even so, placing blocker 18 still meant pulling the 2 GiB image by hand and
running `sbverify` over its layers, because the target disk that would have
answered it in one line had already been deleted. The lane now dumps the
installed ESP — a directory listing plus a signature summary of every `.efi` on
it — to `<workflow>-esp.txt` on any non-zero exit, before the cleanup trap runs.

### Blocker 19 — the installer was unsigning its own kernel

That ESP dump paid for itself on its first run. With the chain fixed, the target
got **past the pre-MOK rejection and past MOK enrollment** for the first time,
and then failed one hop later:

```
Error loading EFI binary \EFI\Linux\bootc\bootc_composefs-a0c1f9a9….efi: Invalid parameter
BdsDxe: No bootable option or device was found.
```

The image's UKI is MOK-signed; the one on the ESP was not.

**I got the cause wrong, twice over, and it is worth recording why.** I
concluded `bootc install` was rewriting the UKI and dropping its signature, and
built [fisherman#27](https://github.com/frostyard/fisherman/pull/27) to stage
the image's signed UKI over it — at a cost of ~100 MiB added to the image-root
extraction. I also filed frostyard/snosi#516 claiming the update path had the
same defect. Both were wrong.

The real cause is ours. `objcopy`'s synopsis is `objcopy [options] infile
[outfile]`, and **with `outfile` omitted it rewrites `infile` in place** — even
when the only requested operation is to dump a section *out*. The rewrite keeps
every section but does not carry the Authenticode certificate table across:

```
shimx64.efi.signed   1036152 bytes
objcopy --dump-section .sbat=out shimx64.efi.signed
                     1016789 bytes   ← exactly Debian's UNSIGNED shim
```

fisherman read the UKI's `.cmdline` and `.pcrpkey` that way, against the UKI on
the installed ESP, inside `VerifyInstalled`. **The secure install unsigned its
own kernel between completing and rebooting.** On the real UKI it reproduces the
observed artifact exactly: 101157224 → 101134848.

bootc is not involved: `write_pe_to_esp` is a `std::io::copy`, unchanged between
v1.16.3 and main, and bootc links no PE-writing crate.

The disproof was in evidence I had already collected and reported as good news:
**`.pcrsig` survived**. Regenerating that section needs the PCR *private* key,
which is never present during an install — so nothing could have rebuilt the PE,
and only a tool that preserves sections while dropping the certificate table
fits. When section contents survive but a signature does not, suspect a rewrite,
not a rebuild.

Fixed at the cause in
[fisherman#28](https://github.com/frostyard/fisherman/pull/28): both reads use
Go's `debug/pe` in process, which cannot mutate and avoids a ~100 MiB temporary
copy per call. `VerifyInstalled` now also refuses an unsigned installed UKI
outright, so this class fails at install time rather than at the next boot.
#27 is closed and snosi#516 withdrawn.

The finding came from a subagent audit of bootc's write path, dispatched after
the maintainer asked whether the next bootc *update* would strip the signature
again — a question about coverage that turned up the actual cause.

### Blocker 21 — the kernel boots; nothing unlocks the root

**This is the current front, and it is much further along than anything before it.**

With the UKI signature preserved, the target now boots. The kernel starts, the
initramfs runs, the TPM is found — and then:

```
[1.872] Expecting device dev-gpt-auto-root.device - /dev/gpt-auto-root...
[  OK ] Reached target cryptsetup.target - Local Encrypted Volumes.
[  OK ] Found device dev-tpm0.device - /dev/tpm0.
[  OK ] Reached target tpm2.target - Trusted Platform Module.
[TIME ] Timed out waiting for device dev-gpt-auto-root.device.
[DEPEND] Dependency failed for sysroot.mount - Root Partition.
Entering emergency mode.
```

`cryptsetup.target` is reached **with no units beneath it**. Nothing ever
attempted an unlock.

**What is ruled out.** The lane now dumps the installed disk before cleanup, and
the disk is correct:

| partition | name | GPT type | fs |
|---|---|---|---|
| p1 | `EFI-SYSTEM` | `c12a7328-…` | vfat |
| p2 | `root` | `4f68bce3-e8cd-4db1-96e7-fbcaf984b709` | **crypto_LUKS** |

LUKS2 carrying both a `systemd-tpm2` token and a pbkdf2 recovery slot. So
partitioning, LUKS setup and TPM enrollment — all fisherman's work — are right.

Unpacking the initramfs from the image's own signed UKI rules out more:
`libcryptsetup.so.12` and the tpm2 token plugin are present, the generator has
`add_root_cryptsetup` compiled in, and `libsystemd-shared-261.so` matches PID 1's
`systemd 261.2-1`, so there is no trixie/forky skew.

**A wrong turn worth recording.** I first blamed a missing
`systemd-cryptsetup@.service` template, filed that as the root cause, and was
corrected: systemd v261's generator *writes* complete units rather than
instantiating a packaged template, so forky omitting the template is correct
packaging. Verified against systemd's source before accepting it. Copying a
template in would have been a fix for a non-problem.

**Then proven capable.** Running the real initramfs's own generator against the
real signed UKI on selfie, it emits everything needed:

```
/run/gen/late/systemd-cryptsetup@root.service
/run/gen/late/dev-gpt\x2dauto\x2droot\x2dluks.device.wants/systemd-cryptsetup@root.service
/run/gen/late/systemd-veritysetup@root.service
/run/gen/late/sysroot.mount
```

So the generator can set up the encrypted root. The failure is in the
**production discovery path** — under the real cmdline (`rw composefs=?<digest>`,
no `root=`) it never takes the LUKS branch. That is the open question in
[snosi#517](https://github.com/frostyard/snosi/issues/517).

[snosi#518](https://github.com/frostyard/snosi/pull/518) and
[#519](https://github.com/frostyard/snosi/pull/519) add a build-time regression
test that runs the embedded generator and requires the unit — scoped, explicitly,
to *not* claim coverage of the production path.

### The secure install boots

**2026-08-07.** On the first published image carrying snosi#520, the lane
installed a target and **booted it** — automatically, via the digest watcher,
with nobody watching:

```
ok - Microsoft-only varstore rejects the unenrolled MOK stage
ok - firmware Secure Boot is enforced
ok - booted chain is a measured UKI
ok - lockdown is integrity or confidentiality
ok - kernel command line has a composefs binding without accepting raw root data
ok - kernel command line contains no root or LUKS identifier
```

SSH was available 14 seconds after the enrolled boot. Every one of those
assertions runs *inside the guest*, so each requires a live system whose
encrypted root was unlocked by the TPM.

That is the goal this lab was built to reach: **a snosi image that installs
through the real external installer and then boots, under enforced Secure Boot,
with a TPM-unlocked LUKS root.** Twenty-two blockers, across four repositories,
none of them findable without running against real media.

What remains are assertions about a working system — BLS entry shape, TPM token
identity, provenance completeness, the runtime reconciler, update and rollback.
Different work, and a much better class of problem.

#### Blocker 21 — resolved: a udev rule that moved house

**Root cause, found by a second agent and confirmed here against the real
artifacts:** systemd 257 shipped the udev rules that create
`/dev/gpt-auto-root[-luks]` in `99-systemd.rules`, which dracut installs by
name. systemd 261 moved them into `90-image-dissect.rules`, which dracut's
hardcoded list does **not** install. The bootc-secure fragment pins
`udev/forky`, so the initramfs got 261's rules file minus the rules.

The generator was never the problem. v261 writes
`systemd-cryptsetup@root.service` **unconditionally**, keyed on the device
appearing — from systemd's own source:

> *If a device /dev/gpt-auto-root-luks … appears, then make it pull in
> systemd-cryptsetup@root.service, which sets it up, and causes
> /dev/gpt-auto-root … to appear which is all we are looking for.*

So the unit was generated correctly every time; nothing ever created the device
it binds to. `cryptsetup.target` completed empty, `sysroot.mount` timed out,
emergency mode. My own framing — "the generator never took the LUKS branch" —
was wrong, and that error is why I kept looking at the generator instead of at
udev.

**Verified before merge, without CI or signing keys.** Running `dracut` inside
the published image twice, identical but for the drop-in:

| | `90-image-dissect.rules` in the initramfs |
|---|---|
| baseline | **0** |
| with `35-gpt-auto-udev-rules.conf` | **1** |

with the installed file carrying
`ENV{ID_FS_TYPE}=="crypto_LUKS", … SYMLINK+="gpt-auto-root-luks"`. The baseline
`0` also reproduces the defect in a *fresh* dracut run, making it a build
configuration bug rather than an accident of how one initramfs was assembled.

**Also confirmed on published media.** The lane ran against the ISO GitHub built
after runners briefly recovered — new ETag, freshly downloaded, fisherman
v0.2.14 — and reached emergency mode identically. So this was a defect in what
users would install, not an artefact of the ISO built by hand on selfie.

**Still unproven:** the runtime chain. A rebuilt secure image is required.

### Blocker 20 — live SSH is intermittent, and the cause is still unknown

Two runs in a row differed only in this: one reached the installer fine, the
other sat for 600 s with `ssh.socket` **listening**, `ssh.service` **failed**,
and nothing on the console. It is flaky, not deterministic.

Worth stating plainly because it has cost three rounds: **two fixes here were
built on unverified theories** about why `ssh.service` fails — first "our unit
stops the working socket" (#17), then "connecting is what starts the service"
(#19). Both were wrong, and each took a run to disprove.
[dakota#20](https://github.com/frostyard/dakota-iso/pull/20) stops theorising:
it reports `is-active`, `ss -ltnp` on `:22` — whether anything is actually
*serving*, which is not the same claim as a unit being active — `sshd -t`, and
the journal, **unconditionally**. The previous guard only spoke when nothing was
listening, which is precisely the case that did not occur.

**Where it sits now:** fisherman **v0.2.6** is released and carries 4 through 7.
The dakota pin is repointed at it and the secure ISO rebuilt against it.

The pattern has been stable enough to state plainly: **each release clears one
blocker and the next attempt finds the next one.** Seven so far, none of them
reachable without running against real media, and two of them actively masked by
tests that asserted the broken behaviour. Expect an eighth rather than a green
run — that is not pessimism, it is what six iterations of evidence say, and the
lane is worth running precisely because it keeps finding real defects.

A note on how this gets diagnosed now: fisherman's `DefaultOutput` used to drop
`ExitError.Stderr`, so every failed command surfaced as a command line plus
`exit status N`. Blocker 7 needed a hand-built reproduction to diagnose for that
reason alone. #18 propagates stderr, so the next one should explain itself.

### Blocker 8 — open, and it is structural rather than a typo

On v0.2.6 media the install itself now completes. It fails afterwards:

```
fisherman: fatal: validating deployed secure contract: reading installed
  secure contract: open /mnt/fisherman-target/usr/lib/snosi/bootc-secure.json:
  no such file or directory
```

The image is not at fault — `ghcr.io/frostyard/cayo:latest` ships
`/usr/lib/snosi/bootc-secure.json` (verified directly, alongside `cosign.pub`,
`mok.crt` and `pcr-signing.pub`). The problem is *where fisherman looks*.

A composefs deployment does not present a merged root under the target mount.
Its writable `/etc` lives at `state/deploy/<hash>/etc` — fisherman's own
`post.DefaultComposeFsDeployEtcDir` documents exactly this — and `/usr` comes
from the composefs image, which is not a directory tree on the target at all.

**This is not one bad path.** Every `usr/...` read in the secure post-install
validation makes the same assumption:

| Reads from `<target>/usr/...` | Purpose |
|---|---|
| `usr/lib/snosi/bootc-secure.json` | the contract itself |
| `contract.PCRPublicKey` → `usr/lib/snosi/pcr-signing.pub` | PCR identity |
| `contract.MOKCertificate` → `usr/lib/snosi/mok.crt` | MOK identity |
| `usr/lib/snosi/bootc/systemd-bootx64.efi` | ESP second-stage repair source |

The `boot/efi/...` and `var/...` reads are fine — those really are on the
target. So the secure post-install validation was written against a merged-root
layout, while the secure install is composefs **by contract** (schema-1 mandates
the composefs backend). The two have never met before now because nothing had
run a secure install to completion.

**Resolved: option 1**, in
[fisherman#19](https://github.com/frostyard/fisherman/pull/19). The roots are
now split by where each artifact actually lives — `boot/efi` and `var` on the
installed target, everything under `usr/` from the image's `usr/lib/snosi`
subtree, extracted during install. Released as **v0.2.7**.

The soundness argument *and its expiry condition* are recorded at the extractor:
source and deployment are identical by construction because the composefs digest
is verified immediately beforehand and bootc pins the deployment to it — if that
digest check is ever removed, this becomes an assumption and needs revisiting.

`secure-restage-mok` and `secure-repair-esp` still read from the target root and
carry the same limitation, annotated in place. They run against an
already-installed system where no source image exists, so they need a different
approach and a real installed system to test against.

The three options as they stood:

1. **Read those artifacts from the source image.** The composefs digest is
   computed and verified immediately beforehand, and bootc pins the deployment
   to it, so source and deployment are identical *by construction* rather than
   by assumption. Cheapest, and arguably validates the same fact.
2. **Mount the composefs deployment read-only and validate through the mount.**
   Validates the deployed bytes literally, at the cost of mounting during
   install.
3. **Copy the artifacts to a known target path at install time**, and validate
   those. Simple, but it validates a copy the installer made — the weakest of
   the three.

### Blocker 10 — open, and it needs building rather than fixing

On v0.2.8 the install completes, the contract is read and validated, and it
fails here:

```
fatal: installing verified secure ESP second stage: validating installed
  mmx64.efi: stat <target>/boot/efi/EFI/BOOT/mmx64.efi: no such file or directory
```

**Nothing stages the Secure Boot chain onto the ESP.** `bootc install` with
`--bootloader systemd` writes plain systemd-boot:

```
EFI/BOOT/BOOTX64.EFI            systemd-boot (158 KB)
EFI/systemd/systemd-bootx64.efi
EFI/Linux/bootc/bootc_composefs-<id>.efi
```

The secure design needs the shape the native A/B image already ships:

```
EFI/BOOT/BOOTX64.EFI            shim, Microsoft-signed (1.0 MB)
EFI/BOOT/grubx64.EFI            MOK-signed systemd-boot, chainloaded by shim
EFI/BOOT/mmx64.efi              MokManager
EFI/Linux/<uki>.efi
```

The parts are all present in the image — `/usr/lib/shim/shimx64.efi`,
`/usr/lib/shim/mmx64.efi`, and the MOK-signed second stage at
`/usr/lib/snosi/bootc/systemd-bootx64.efi`. Nothing assembles them.

The naming is the tell: `RepairESP` *repairs* a chain — it replaces the second
stage on an ESP that already has shim, which is exactly what snosi's Task 7
reconciler does at runtime ("Shim `BOOTX64.EFI` and MokManager `mmx64.efi` are
never modified"). Both assume an install step that puts shim and MokManager
there in the first place. On the native A/B path mkosi/repart does it. On the
bootc path nobody does.

**Built in [fisherman#21](https://github.com/frostyard/fisherman/pull/21)**,
released as v0.2.9. Three decisions in it are worth knowing, because each is a
place a later change could go wrong quietly:

- **Only the second stage is verified.** It is what snosi signs with its own
  MOK. shim and MokManager are Debian's and Microsoft-signed — checking them
  against snosi's MOK would *fail*, and firmware establishes their trust at
  boot. What this does rely on is all three coming from an image whose
  signature was verified at pull.
- **shim is written last.** Until it lands the entry point is still bootc's
  plain systemd-boot, which at least boots unsecured. Writing shim before its
  chainload target exists would leave an ESP that cannot boot at all if the
  install is interrupted. The ordering is a failure-mode choice.
- **Idempotent.** A complete chain is left untouched, so this never gratuitously
  rewrites a firmware entry point on an existing install.

This was the first blocker where the fix was new behaviour rather than a
correction, so there is more room for it to be subtly wrong than in 1–9.

**It worked on real media.** The next run got past the missing `mmx64.efi`
entirely, which means shim, MokManager and the MOK-signed second stage all
landed where they belong. Blocker 11 was found immediately after, further along
the same code path.

Blocker 11 itself was small — `ValidateType2BLS` accepted only the `efi`
directive while every entry bootc writes uses `uki`. Both designate a Type #2
UKI under `/EFI/Linux`; snosi's note that "bootc writes a BLS `efi=` entry" was
an observation of one build that has gone stale. Fixed in
[fisherman#22](https://github.com/frostyard/fisherman/pull/22), released as
v0.2.10, with an entry carrying *both* directives now refused as ambiguous
rather than silently preferring one.

**The failures are arriving later in the install each time.** That is the shape
worth watching: it means each fix is real and the remaining surface is
shrinking, rather than the same wall being hit from different angles.

### The tests are half the story

Four of the nine blockers were **actively masked by tests that asserted the
broken behaviour**, and each passed while the thing it covered could not work:

| Blocker | What the test locked in |
|---|---|
| 5 | `--signature-policy` before the subcommand — podman rejects it outright |
| 5 | the same, in a second test |
| 7 | the composefs digest probe without `--privileged` or the store mounted |
| 9 | a 2 GiB ESP floor no image declares |

Blocker 12 added a fifth, and a sharper variant: `installedFixture` writes a BLS
entry with an `options composefs=` line, a shape **no real install produces**.
Every test in that file was exercising a fallback path rather than the live one,
which is why the suite stayed green through a bug that broke every install.

The common shape: each test restated the argument vector, constant, or file
layout the implementation happened to produce, so it could only ever fail if
someone changed the implementation *and forgot to change the test*. It could
never fail because the behaviour was wrong.

**This is now the most reliable predictor of where the next blocker is:**
wherever a fixture encodes a shape the product does not emit.

### Two of my own mistakes, worth recording

**Blocker 13 was self-inflicted.** The QA SSH unit added in
[dakota#16](https://github.com/frostyard/dakota-iso/pull/16) ran
`systemctl start ssh.socket || systemctl start ssh.service`. The live image
socket-activates sshd, so `ssh.socket` was usually already listening; starting
`ssh.service` *conflicts* with it, so systemd stopped the working socket and
then failed to start the service, leaving nothing listening. The unit was
breaking the very SSH it exists to provide — and only when it lost the race
against socket activation, which is exactly the intermittent
"Dakota live SSH did not become ready" seen earlier. Raising the timeout to
600s could never have helped; that was treating a symptom. What actually caught
it was the serial-console dump added in the same PR.

**Blocker 14 was an incomplete fix of blocker 8.** When the contract, PCR key
and ESP second stage moved to the image root, two MOK certificate reads were
left on the target root. The failure pointed at one path and I converted the
paths it pointed at, rather than auditing the class. Fixed properly this time by
sweeping every caller, with a note at the `secureImageRoot` declaration listing
everything that must come from the image root — because the next `/usr` read is
the obvious place for a third.

The replacements assert **properties** instead — "the subcommand precedes its
flags", "the store is mounted", "the constant equals the normative figure". That
is the only form that fails when reality drifts, and it is worth adopting as a
review habit in that repo: it accounts for roughly half of everything found
here.

### On blocker 8's framing

Blocker 8 was qualitatively different from 1–7. Those were wrong flags, wrong
paths, wrong namespaces — mistakes. This one is an architectural mismatch:
post-install validation written for one deployment layout, against an install
path that mandates another. That it surfaced only after seven other fixes
cleared the way to it is the whole argument for the lane existing.

Blocker 9 was back to the ordinary kind, so that framing described one blocker
rather than a new phase. The honest summary remains: each run gets further and
finds the next real thing, and there is no way to know how many remain.

### After that

- **Phase F** — Secure Boot for the native A/B lanes. Independent of the above,
  adopts snosi's existing `virt-fw-vars` pattern, could start any time.
- **Phase E** — the lab as `bootc-secure` self-hosted runner. Approved; gated on
  the secure lane being green so we are not automating something unproven.
- **ext4 in the fisherman matrix** — done, open as
  [fisherman#25](https://github.com/frostyard/fisherman/pull/25). See G0.
- **A live dashboard.** The current design — a 30-minute CronWorkflow that
  commits `runs.json` and triggers a Pages rebuild — is the *least* live option
  available, and the maintainer wants more live rather than less. So it is a
  working default, not the end state. Reference to study first:
  <https://factory.projectbluefin.io/>, projectbluefin's own build/QA surface —
  worth understanding before designing ours, given this lab is modelled on
  `projectbluefin/lab`. `collect_runs.py` stays the producer whichever way it
  goes; only the sink and the cadence change.

---

## What got touched, and why

Four repos, because the blockers were genuinely distributed. Nothing here was a
detour for its own sake — each one sat directly on the path to a green secure
install.

| PR | Repo | Why it was on the path |
|---|---|---|
| [#508](https://github.com/frostyard/snosi/pull/508) | snosi | publish mechanics images — the bootc lane had no legal target |
| [#509](https://github.com/frostyard/snosi/pull/509) | snosi | version floors in the normative contract |
| [#21](https://github.com/frostyard/bootc-installer/pull/21), [#22](https://github.com/frostyard/bootc-installer/pull/22) | bootc-installer | secure install flow; fisherman pin |
| [#16](https://github.com/frostyard/dakota-iso/pull/16) | dakota-iso | SMBIOS QA-SSH transport |
| [#14](https://github.com/frostyard/fisherman/pull/14) | fisherman | version floors, detected-version provenance |
| [#15](https://github.com/frostyard/fisherman/pull/15) | fisherman | `--signature-policy` position |
| [#16](https://github.com/frostyard/fisherman/pull/16) | fisherman | `workflow_dispatch` on release-publish |
| [#17](https://github.com/frostyard/fisherman/pull/17) | fisherman | frostyard-only matrix; OCI-layout policy |

Plus, in this repo: the `unproven` lane state, the pipefail audit, the secure
lane itself, ETag cache validation, and host-persisted harness logs.

**Two findings worth remembering even if everything else fades:**

1. **A secure image is not derivable.** Its MOK-signed UKI pins `composefs=` to
   the digest of the exact image it was assembled from, so any derived variant
   is refused. That is the security property working. It is why the fisherman CI
   matrix must use `:mechanics` images, and why the secure path can only be
   tested by installing the unmodified signed image through real media.
2. **Tests asserted two of these bugs.** The `--signature-policy` position had
   two unit tests locking in a command line podman rejects outright. A test that
   restates whatever the implementation emits proves only that it still emits
   it.

---

## What each lane actually tests

The names on the dashboard are ambiguous. Concretely:

| Dashboard name | Template | Artifact under test | Install step? | Secure Boot |
|---|---|---|---|---|
| Container smoke suites | `run-container-tests` | `ghcr.io/frostyard/{snow,snowfield,cayo}:latest`, booted as nested systemd containers | no | n/a |
| ISO boot (Secure Boot) | `run-incus-vm-tests` | `isos/snow-live-latest.iso` — the **bootc live desktop ISO**, booted to its live session | no | **on** |
| Published A/B disk artifact | `run-incus-disk-tests` | `os/native/v1/cayo/x86-64/cayo-ab_<ver>.disk.raw.xz` — the **complete shipped GPT disk**, booted directly | no | off |
| Native A/B installer | `run-incus-install-tests` | `isos/native/v1/snosi-native-installer-latest-x86-64.iso` → runs `/usr/libexec/snosi-install --product cayo-ab` → reboots | **yes** | off, `--skip-mok` |
| bootc installer | `run-incus-bootc-install-tests` | boots the snow-live ISO, then `bootc install to-disk` from `ghcr.io/frostyard/snow:latest` → reboots | **yes** | off |

Two clarifications that matter:

- **"ISO boot (Secure Boot)" is the bootc lane, not the installer.** It boots
  `snow-live-latest.iso` — bootc-format live media — and never installs
  anything. It is the only thing in this lab proven under real enforced Secure
  Boot, and what it proves is that the *live ISO's* signing chain is trusted by
  stock OVMF. It says nothing about the native installer ISO, which is a
  different artifact at a different URL.
- **"Published A/B disk artifact" is not a DDI install.** There is no install
  step at all. It downloads the full 16.6 GB `*-ab.disk.raw`, verifies it
  against the gpg-signed `SHA256SUMS`, and boots it as-is. The image is
  self-contained: ESP with shim + MokManager + systemd-boot + UKI, a populated
  slot A (erofs root + verity hash), an empty slot B awaiting the first update,
  and a plain-ext4 `/var`.

Note that last detail: the published image ships **plain ext4 `/var`**, while
`snosi-install --encrypt-var` produces a **LUKS `/var`**. The two paths produce
materially different systems, so the disk lane and the install lane are not
redundant.

---

## Two corrections

Both of these were reported to the maintainer as findings about snosi. Both
were actually defects in this lab.

### 1. The disk-artifact lane was never broken code under test

It reported red on every run. The cause was one line in this repo:

```bash
if incus console "${VM}" --show-log 2>/dev/null | grep -qaF "${EXPECT_CONSOLE}"; then
```

`grep -q` exits on the first match and closes the pipe; `incus console` dies of
SIGPIPE (141); `set -o pipefail` promotes that to a failed pipeline. The lane
reported failure **precisely when it found its marker**. The install and bootc
lanes escape this only because they route through a `console_log()` helper that
ends in `|| true`.

With that fixed, the published `cayo-ab` image reaches `multi-user.target` and
`graphical.target` in about eleven seconds on a dm-verity `/dev/mapper/root`.

Before this was found, the README carried a confident theory that a
`*-ab.disk.raw` is a "pre-install artifact" that cannot be expected to boot
standalone, and that the lane's premise was a category error. That theory was
wrong, and it pointed at snosi instead of at this repo.

### 2. The bootc install lane was installing a secure image by the mechanics path

This one took three rounds to pin down, and each round produced a plausible
wrong answer.

**Round 1** — the lane ran `bootc install to-disk --wipe --filesystem ext4`,
missing `--composefs-backend`. snosi's images are composefs deployments, so this
looked like the whole story. It was not: with the corrected flags the install
still produced an emergency-mode boot, byte for byte the same failure.

**Round 2** — `--karg console=ttyS0`, which snosi's own `test/lib/vm.sh` passes,
is rejected outright against these images:

```
Setting up UKI boot: Cannot use externally specified kernel arguments with UKI
```

snosi's Task 2 notes record the same constraint. Dropping it let the install
complete — and the installed system *still* went to emergency mode.

**Round 3, the actual answer.** `ghcr.io/frostyard/snow:latest` is labelled
`io.snosi.bootc.secureboot-capable: "true"`. `bootc install to-disk` is snosi's
**legacy mechanics tier**, and snosi's own CI refuses to point it at a secure
image — `.github/workflows/test-install.yml` hard-fails unless the label reads
`false`:

```bash
[[ $capability == false ]] || {
  echo "::error::legacy mechanics workflow requires secureboot-capable=false" >&2
  exit 1; }
```

A `secureboot-capable=true` image is installed by the **external secure
installer** (Dakota ISO + bootc-installer, driven by a recipe), never by
`bootc install to-disk`. The lane was doing precisely what snosi's own CI exists
to prevent.

The failure is a coherent consequence of that. Inspecting the installed disk
directly: the root partition carries the correct DPS type GUID
(`4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709`) on the same disk as the ESP, the
bootloader is systemd-boot, and the deployment's UKI cmdline is exactly:

```
rw composefs=?7284737ba131387625d6c296…
```

No `root=`, so root discovery falls to `gpt-auto` — which never produces
`/dev/gpt-auto-root`, so `sysroot.mount` and `bootc-root-setup.service` both
fail and the guest lands in `emergency.target`. That is what the mechanics
installer produces from a secure image; it is not a defect in the image.

**Consequence:** [frostyard/snosi#504][504] and [frostyard/snosi#505][505] are
both invalid and should be closed. The lane now carries the same capability
guard as snosi's CI, so it fails in seconds with the real reason instead of
twenty minutes later in emergency mode.

[504]: https://github.com/frostyard/snosi/issues/504
[505]: https://github.com/frostyard/snosi/issues/505

**The transferable lesson:** a lane that has never once been green is evidence
about the lane, not about the thing under test. Every round above produced a
narrative that explained the symptom and was still wrong, because the red was
assumed to be a finding rather than a question about the harness.

---

## Where coverage actually stands

Green means proven in this lab. Items marked *snosi* are proven by snosi's own
test suite but not continuously, and not against published artifacts.

### Native A/B

| Category | What | Status |
|---|---|---|
| ISO | installer ISO boots | 🟢 lab (SB off) · 🟢 *snosi* `native-installer-iso-test.sh` under **enforced SB**, plus a negative proof |
| Installer | unattended install completes | 🟢 lab |
| Installer | MOK enrollment, unattended | ⚪ lab runs `--skip-mok` · 🟢 *snosi* `native-installer-e2e-test.sh` |
| OS | installed system boots; verity + LUKS + erofs | 🟢 lab (SB off) |
| OS | installed system boots under **enforced SB** | 🔴 not covered in lab · 🟢 *snosi* e2e |
| OS | published disk image boots standalone | 🟢 lab, as of today |
| OS | published disk image under enforced SB | 🟡 fails at shim by design — MOK not enrolled in a fresh OVMF |
| OS | A/B update → rollback → boot-count fallback | 🔴 not covered in lab · 🟢 *snosi* `native-ab-secure-boot-test.sh --full-window` |

### bootc

| Category | What | Status |
|---|---|---|
| ISO | snow-live ISO boots under **enforced SB** | 🟢 lab |
| Image | container smoke suites (20 desktop / 14 server) | 🟢 lab ×3 products |
| Installer | `bootc install to-disk` (mechanics tier) | ⚪ **not applicable** to `:latest`, which is `secureboot-capable=true`; lane now refuses it, as snosi's CI does |
| OS | installed system boots | ⚪ blocked behind the row above |
| OS | secure install path (external Dakota / bootc-installer / Fisherman) | 🟢 `run-secure-install-tests` — 18/18 on published media, first green 2026-08-07. Refusal of a deliberately-broken image is **not** covered (negative fixtures removed by decision) |
| OS | update / rollback | 🔴 not covered |

**There is currently no way for this lab to test bootc installation at all.**
The mechanics path is refused for `secureboot-capable=true` images, and no
mechanics image is published — snosi's PR `mechanics-build` job is
non-publishing by design, so `:latest` is always the secure build. The secure
path needs the external installer, which snosi's own harness is blocked on.

So the bootc install gap is **structural, not a bug to fix in a lane**. Closing
it requires one of: a published mechanics image to point the existing lane at,
or access to the external secure installer. That is question 2 below, and it is
the single most valuable thing a maintainer could unblock.

---

## The Secure Boot gap is already solved upstream

This has been carried as "needs a maintainer decision between a lab-side OVMF
pre-seed and unattended enrollment in the installer". That framing was wrong:
**snosi already made this decision and implements it.**

`test/native-ab-secure-boot-test.sh` and `test/native-installer-e2e-test.sh`
both pre-enroll the Snosi MOK host-side:

```
virt-fw-vars --add-mok <cert> --input OVMF_VARS_4M.ms.fd --output <per-VM varstore>
```

paired with `OVMF_CODE_4M.secboot.fd`. Microsoft keys are already enrolled, so
Secure Boot is genuinely enforced; the MOK addition simulates what MokManager
would do if a human clicked through it. The e2e test uses exactly this to prove
the installed system boots fully enforced and fully unattended.

The lab needs no decision — it needs to adopt the same pattern against incus's
per-instance `qemu.nvram`. The certificate is committed and public
(`shared/native-ab/keys/mok-2026.crt`).

---
## Roadmap

**Revised 2026-08-05** after the maintainer confirmed three things that change
the shape of this materially:

1. Mechanics builds may be published, provided they are never advertised as
   something useful to install.
2. `dakota-iso`, `bootc-installer`, and `fisherman` are all frostyard repos
   under our control, and changes to them that help prove install/image success
   are welcome.
3. The lab should become the self-hosted runner once it is cleaned up.

The single biggest consequence: **bootc install coverage is no longer
structurally blocked.** It was previously "needs an external installer we do not
have". That is wrong — the installer, the media, and the test runners all exist
already, on branches in repos we own:

| Piece | Where | State |
|---|---|---|
| Unattended install entrypoint | `fisherman install --recipe <json>`, and `/usr/lib/snosi/fisherman <recipe.json>` on the live ISO | exists |
| Secure install flow (LUKS, MOK staging, TPM, cosign) | `bootc-installer` `feat/live-image-selection`, `bootc_installer/utils/secure_install.py` + `docs/secure-install.md` | exists |
| Secure Snow installer media | `dakota-iso` `feat/secure-snow-media`, `images.json` with `secure_install: true` on all three products | exists, pushed |
| **snosi's missing Task 9 runners** | `dakota-iso` `test/bootc-secure-{installer,recovery}-runner.sh`, `test/bootc-secure-update-publish.sh` + `test/lib/bootc-secure-runner-lib.sh` | **exists, green** (the two negative runners were deleted 2026-08-07) |

That last row is the finding. snosi's `test/bootc-secure-install-test.sh` reports
`BLOCKED` because no prepared runner is supplied — and
`bootc-secure-installer-runner.sh` implements exactly the contract it asks for:

```
$0 --non-interactive --iso ISO --recipe RECIPE
```

schema-1 recipe, immutable `ghcr.io/frostyard/<profile>@sha256:…` plus a
tracking tag, QEMU + swtpm + enforced Secure Boot, then `sudo
/usr/lib/snosi/fisherman /run/snosi-task9/recipe.json`, then LUKS and
persistent-`/etc` assertions.

**What is actually missing is the prepared *environment*, not code:**
`SNOSI_SECURE_OVMF_CODE`, `SNOSI_SECURE_OVMF_VARS`, `SNOSI_SECURE_TPM_STATE`,
`SNOSI_SECURE_TPM_SOCKET`, a built secure Dakota ISO, and signed secure OCI
fixtures. selfie can supply all of it.

Note these runners drive **raw QEMU**, not incus. Do not port them into Argo
templates — that work would be thrown away the moment the runner lands. See
Phase D.

---

### Phase A — make the lab's own results trustworthy

Unchanged and still first. Nothing below is worth building on a harness that
produced two false findings in a week.

- [x] **A1.** Fix the disk lane's SIGPIPE/pipefail inversion.
- [x] **A2.** Establish that the bootc lane was aimed at the wrong image tier;
      add the capability guard snosi's own CI uses.
- [x] **A3.** Close #504 and #505 as invalid, with evidence.
- [x] **A4.** *First-green requirement*: a lane that has never once succeeded
      reports `unproven`, not `Failed`. This is the control that would have
      caught both bugs, and it is cheap. Implemented in the collector and
      dashboard; recorded as
      [ADR-0003](adr/0003-unproven-is-distinct-from-failed.md).
- [x] **A5.** Audited every lane for the `pipefail` shape and for assertions
      that can only ever fail. **Four instances total**, all fixed:
      the disk lane's SIGPIPE inversion, the bootc lane's union pipeline, the
      bootc lane's `/sysroot/ostree` assertion (a path a composefs-backend
      install does not have), and a fourth found by the sweep —
      `run-incus-install-tests` built `checks.txt` through
      `console_log | grep | sed | sort` with no `|| true`. That one is masked
      today because the lane is green, but it would have killed the script
      exactly when the per-check diagnostics beneath it were needed.
      No `| head -` SIGPIPE instances remain. All nine templates `bash -n`
      clean.

### Phase B — publish mechanics images (cheap, unblocks the existing lane)

The bootc mechanics lane is fully built and has never had a legal target,
because `:latest` is always `secureboot-capable=true` and `mechanics-build` is
non-publishing by design.

- [ ] **B1.** Publish mechanics images to a **distinct tag that `latest` never
      points at** — `ghcr.io/frostyard/<product>:mechanics-<version>`. Approved,
      including the registry write for a main-only job. No retention work:
      GHCR's own GC (~90 days) is the intended lifecycle, and these are not worth
      accumulating or protecting.
- [ ] **B2.** Guardrails so this is never mistaken for an install target: keep
      `io.snosi.bootc.secureboot-capable=false`, add an explicit
      "QA-only, not for installation" annotation, never reference it in install
      docs, and never move a user-facing tag to it.
- [ ] **B3.** Point `run-incus-bootc-install-tests` at the mechanics tag. Its
      guard then passes and the lane gets its first green — which also proves
      the lane's plumbing, still unverified end to end.

Worth being clear about what B buys: it validates *installation mechanics only*
and carries no security evidence. It is a real regression signal for the
install path, and nothing more.

### Phase C — land the in-flight branches ✅ done 2026-08-05

- [x] **C1.** `dakota-iso` `feat/secure-snow-media` — **was already merged**;
      `main` is ahead of it and carries all the Task 9 runners. Only the local
      checkout was stale.
- [x] **C2.** `bootc-installer` `feat/live-image-selection` — dangling fisherman
      bump committed and merged as
      [#21](https://github.com/frostyard/bootc-installer/pull/21), all checks
      green.
- [x] **C3.** Fisherman pin `5cdcb5f` confirmed reachable from `origin/dev`, so
      the detached local HEAD was never at risk of loss.

### Phase D — the secure install path

The active front. Full blow-by-blow in
[Appendix: how the secure path was unblocked](#appendix-how-the-secure-path-was-unblocked);
the short version is the table in [Status at a glance](#status-at-a-glance).

- [x] **D1.** Secure Dakota ISO — already published nightly and already cached.
- [x] **D2.** `STATE_ROOT` and toolchain — containerised, host untouched.
- [x] **D3.** Signed OCI fixtures — **not needed**; consecutive published
      version tags are a valid N/N+1/N+2.
- [x] **D4a.** `BLOCKED` cleared.
- [x] **D4b–d.** Six blockers cleared, fisherman v0.2.5 released with the last
      three.
- [x] **D4.** Lane green: 18 assertions, 0 failed, 0 blocked, on published
      media and a published image (`snosi-secure-install-5dpkq`, 2026-08-07).
      Twenty-nine blockers cleared across four repos.
- [x] **D5.** Recovery runners green (TPM replacement + recovery re-enrolment).
      Negative runners **removed by decision**, not implemented — see the
      status section above for exactly what that gives up.
- [ ] **D6.** The update runner (`bootc-secure-update-test.sh`) is still
      unproven: it needs published N+1/N+2 versions with distinct 14-digit
      image versions. Its negative half is gone for the same reason as the
      install lane's.


### Phase E — the lab as self-hosted runner

Approved in principle; the cleanup gate is Phase A.

snosi already declares the jobs. `test-bootc-secure.yml`:

```yaml
runs-on: [self-hosted, linux, x64, bootc-secure]
```

`live-full-window` and `snowfield-hardware` are waiting on a runner with exactly
what selfie has. Registering it is strictly less work than reimplementing any of
it in Argo — which is the argument for **not** building Phase D as Argo
templates.

- [ ] **E1.** Register selfie with the `bootc-secure` label.
- [ ] **E2.** Decide the trust boundary: a self-hosted runner executes workflow
      code from PRs. Restrict to protected branches / `workflow_dispatch`, never
      `pull_request` from forks.
- [ ] **E3.** Move Phase D invocations behind that runner.

### Phase F — close the native A/B Secure Boot gap

Independent of everything above; adopt snosi's existing pattern.

- [x] **F1.** `virt-fw-vars --add-mok` into each guest's `qemu.nvram` before
      first boot, using snosi's exact invocation (owner GUID + `--inplace`), and
      verifying the varstore contains a `MokList` afterwards rather than
      trusting the exit status. incus materialises the varstore at
      `storage-pools/<pool>/virtual-machines/<vm>/qemu.nvram` only once the
      instance has started, so the guest is started and immediately stopped to
      create it.
- [x] **F2.** Install lane now defaults to `secureboot=true`. Proven
      2026-08-06:

      ```
      installed and verified: cayo-ab (verity+luks+erofs, secureboot=true, skip-mok=true)
      ```

      `--skip-mok` is kept rather than dropped: the pre-seed replaces MokManager
      entirely, so asking the installer to stage a prompt nothing can answer
      would be worse, not better. Secure Boot is genuinely enforced — Microsoft's
      KEK/db are untouched and firmware still rejects everything except the one
      certificate granted.
- [ ] **F3.** Negative proof — a wrongly-signed binary must be rejected by shim,
      or the positive result proves nothing.
- [ ] **F4.** Published-disk lane under enforced SB with the MOK pre-seeded.

Note the two formats need *different* MOK handling: native pre-seeds host-side,
while the secure bootc installer generates a one-time MokManager password and
stages enrollment itself. Do not unify them.

### Phase G — matrix, updates, gating

- [x] **G0.** Restore ext4 to the fisherman test matrix — **open as
      [fisherman#25](https://github.com/frostyard/fisherman/pull/25)**. All three
      entries were btrfs; ext4 coverage disappeared with the removed `dakota`
      entry, which was the only ext4 filesystem in it. btrfs is correctly primary
      — it is what most installs use and what the secure path produces — but
      `FormatRoot`'s ext4 branch is not a trivial variation of it:

      ```go
      case "ext4":
          return runner.Run("mkfs.ext4", "-F", "-L", "root", "-O", "verity", dev)
      ```

      composefs enables fs-verity per file and ext4 only offers
      `FS_IOC_ENABLE_VERITY` when the feature is set at format time. Drop that
      one flag and the **install still reports success**; the deployment then
      fails to boot, and nothing in CI notices. The new leg is
      `frostyard-cayo-ext4`: advisory, on the small image, reusing cayo's
      published `:ssh-enabled` tag so no new SSH image is built.
- [ ] **G1.** Both install lanes across `{cayo, snow, snowfield}`.
- [ ] **G2.** Run the behave suite against installed VMs, not just console
      assertions. This is where `snowfield`'s Surface kernel finally gets
      covered — no container lane can assert on a kernel.
- [ ] **G3.** A/B update → rollback → boot-count fallback.
- [ ] **G4.** Pre-promotion gating, tiered:

| Tier | Trigger | Runs | Gate? |
|---|---|---|---|
| 1 | PR | existing static/fixture gates — unchanged | blocking, already is |
| 2 | post-build, pre-promotion | install-and-boot, both formats, SB enforced | **blocking — the new gate** |
| 3 | nightly | full matrix + update/rollback | non-blocking |
| 4 | continuous | published artifacts on digest change | non-blocking |

Tier 4 is what the lab does today and stays valuable after gating exists: CI
tests a candidate, the lab tests what is actually being served.

---

## Sequencing

B and C–D are independent. B is days and yields a green mechanics lane; C–D is
the real coverage. A gates both, and A4/A5 are small.

```
A (trust)  ──┬── B (mechanics publish) ──► mechanics lane green
             │
             └── C (merge branches) ── D (STATE_ROOT + runners) ──► Task 9 unblocked
                                             │
                                             └── E (self-hosted runner) ──► G4 gating
F (native SB) runs in parallel throughout.
```

## Open questions

1. **Mechanics tag naming and lifecycle.** `snow:mechanics-<version>` with no
   moving alias is the safest shape. Do they get retention/GC, or accumulate?
2. **Giving a mechanics job registry write.** Today `mechanics-build` is
   secretless, which is a genuine security property. Publishing means changing
   that. A separate main-only job is the narrower change.
3. **Trust boundary for the self-hosted runner** (E2). selfie holds the incus
   socket and the lab's cluster; a compromised workflow run there is not
   contained. Restricting to protected branches and dispatch is the minimum.
4. ~~Who owns the signed secure OCI fixtures~~ — **resolved**: none are needed.
   See D3.

---

## Appendix: how the secure path was unblocked

Preserved in full because each step was a real defect with a real diagnosis,
and the reasoning is worth more than the conclusion.

### The Phase D log

This is the prize: it unblocks snosi's own Task 9 harness, not just the lab.

- [x] **D1.** ~~Build a secure Dakota ISO~~ — **already done, and already on
      selfie.** `dakota-iso`'s `build-iso-snow.yml` runs nightly at 04:00 UTC
      with `SECURE_SNOSI: 1`, smoke-tests the media under Secure Boot, and
      publishes it to R2 as `snow-live-latest.iso` — which is the *same ISO the
      lab has been caching all along*. Verified inside the cached copy:

      - `/usr/lib/snosi/fisherman` — the exact path the Task 9 runner invokes
      - `/etc/bootc-installer/images.json` with `"secure_install": true` on all
        three products (Snow, Snowfield, Cayo)
      - `/etc/bootc-installer/cosign.pub` and `recipe.json`
      - `/var/home/liveuser` and `sshd` — the account the runner logs in as

      So the secure installer media is published, current, cached, and complete.
      Nothing to build.
- [ ] **D2.** Prepare `STATE_ROOT`. Readiness surveyed on selfie:

      | Need | State |
      |---|---|
      | `qemu-system-x86_64`, `jq`, `python3`, `ssh`, `scp` | present |
      | `/dev/kvm` | present |
      | `OVMF_CODE_4M.secboot.fd`, `OVMF_VARS_4M.ms.fd` | present in `/usr/share/OVMF` — exactly what the runner wants |
      | `swtpm` | not on `PATH`, but incus ships one at `/usr/incus/bin/swtpm` |
      | `sshpass`, `socat` | **missing** |
      | `virt-fw-vars` | missing (Phase F only, not needed here) |

      **Decision: do not install packages on selfie.** Build a `secure-runner`
      container image carrying qemu/swtpm/sshpass/socat, run it privileged with
      `/dev/kvm` and the ISO cache mounted, and keep `STATE_ROOT` on a host
      path. The host stays untouched, and the image is exactly what Phase E's
      runner needs anyway — so it is built once, not twice.
- [x] **D3.** ~~Produce signed secure OCI fixtures~~ — **not needed, decided
      2026-08-05.** The harness wants immutable N/N+1/N+2 digests with distinct
      14-digit versions sharing one tracking tag. `build-images.yml` already
      publishes exactly that on every main push: each build gets its own
      immutable `ghcr.io/frostyard/<product>:<14-digit>` tag alongside `latest`.
      Verified — `snow:20260805124239` is `secureboot-capable=true` and is the
      same digest `latest` points at, with `…002447`, `…005926`, `…133112` and
      more behind it. **Three consecutive published version tags are a valid
      N/N+1/N+2.** They are tagged, not dangling, so GHCR GC will not reap them.

      This is the answer to "build fixtures on GitHub, or install mkosi on
      selfie?" — **neither**. No new CI, no mkosi on the lab host, and no
      signing key ever leaves the `native-build` environment. Strictly the best
      outcome for home-lab safety.

      The one case that would still need a purpose-built artifact is **key
      rotation** (a dual-signed transition UKI). That needs the key ceremony and
      stays on GitHub. It is not on the critical path.
- [x] **D4a. `BLOCKED` is cleared.** The harness passed `require_live_inputs`
      for the first time on 2026-08-05: it validated the profile, the immutable
      `OCI_REF`, the MOK and PCR identities, the mode-0600 recovery credential,
      the 40 GiB blank target, all three dakota runners, the tracking ref, the
      full tool list, and the Microsoft-enrolled OVMF — then built a schema-1
      recipe, generated its SSH keypair, and handed off to dakota's installer
      runner. The environment gap is closed.

- [ ] **D4b. The runners have never been executed against real media, and it
      shows.** The run now fails inside dakota's runner with:

      ```
      ERROR: Dakota live SSH did not become ready
      ```

      `bootc-secure-runner-lib.sh` drives the live ISO over SSH as
      `liveuser@127.0.0.1` with password `live`
      (`SNOSI_TASK9_LIVE_USER`/`SNOSI_TASK9_LIVE_PASSWORD`). Neither half of
      that assumption holds against the published secure ISO. Inspected
      directly:

      - **sshd is not enabled.** The only unit wanted by `multi-user.target`
        is `live-ready.service`.
      - **`liveuser` has an empty password hash** (`/etc/shadow` field length
        0), and `sshd` refuses empty passwords by default.

      So this is not a timeout or a networking problem — the channel the runner
      depends on does not exist on the media. This is precisely what snosi's
      "no prepared runner/artifact set is currently supplied" was concealing:
      the runners were written, unit-tested, and never once run against a real
      ISO.

      **Three ways to close it, and the choice matters because this ISO is what
      users boot:**

      1. *Enable sshd and set a live password on the published media.* Simplest,
         and **the wrong answer** — it ships user-facing install media with an
         SSH daemon and a known password.
      2. *Drop SSH from the runner* and drive the guest over the serial console
         or systemd credentials, as this lab's other VM lanes already do via
         SMBIOS type 11. Safe, but a large rewrite of a runner that otherwise
         works.
      3. **Recommended — gate it.** Ship a QA-only unit on the media that
         enables sshd *and* installs an authorized key only when a kernel
         argument (say `snosi.qa-ssh=1`) is present, with the key delivered as
         a systemd credential over SMBIOS. Off for every real user by
         construction, no password anywhere, and the runner changes by a few
         lines: add the karg, pass the credential, swap `sshpass` for key auth.

      Option 3 also removes `sshpass` from the dependency list, which is a small
      win on its own.

      **Resolved 2026-08-05 — better than option 3.** No media change was needed
      at all: `sshd`, `ssh.service` and `ssh.socket` are *already installed* on
      the media, merely not enabled. So the harness injects a one-shot enabling
      unit as a systemd credential over SMBIOS type 11 — the mechanism this lab
      already uses for agentless guests. Nothing is enabled unless the caller
      controls the VM definition, which is a stronger gate than a kernel
      argument, no password exists anywhere, and the published media is
      untouched. Landed as [dakota-iso#16](https://github.com/frostyard/dakota-iso/pull/16).

      Four defects surfaced getting that first real run through, none of which
      unit tests could have caught:

      1. `-smbios` and its value were emitted as one argv element; QEMU rejected
         the whole string as `invalid option`.
      2. The keypair was generated inside `mapfile < <(...)` — a subshell — so
         the exported key path never reached the parent, and `live_ssh`
         dereferenced an unset variable under `set -u`.
      3. `fisherman` rejected the recipe: `secureInstall.mokPasswordFile is
         required`. bootc-installer's own docs say that file is *caller-owned*
         for an external autoinstall recipe; the runner never generated one.
      4. `wait_live_ssh` allowed 300s and dumped no guest evidence on timeout.
         The same media passed once and timed out twice. Now 600s, and it tails
         the serial console before dying.

- [ ] **D4c. Blocked on a real contract drift.** With all of the above fixed the
      chain runs end to end — live ISO boots under enforced Secure Boot, the
      harness SSHes in, delivers a schema-1 recipe, and invokes the real
      installer — and stops at:

      ```
      fisherman: fatal: secure installer prerequisites:
                 secure install requires dpkg-query version 261.1-3
      ```

      The published secure Dakota media ships **systemd 261.2-1**
      (`systemd`, `libsystemd0`, `systemd-boot`, `udev` all 261.2-1).
      fisherman pins **exactly 261.1-3** — the version snosi's Task 4 validated.
      The media moved forward; the pin did not.

      **This needs a maintainer decision and I deliberately did not just bump
      it.** snosi's own notes are explicit that the secure assembly is not
      upstream-stable and that the build/root check must be repeated "when
      either the Frostyard debs or the selected systemd family changes".
      Editing the pin to match whatever the media happens to ship would defeat
      exactly the check it exists to perform. The options:

      1. **Re-validate on 261.2-1 and bump the pin.** Correct if 261.2-1 is the
         intended family — but it means re-running Task 4's build/root check.
      2. **Make the check a minimum** (`>= 261.1-3`) rather than exact equality.
         Removes the drift class permanently, at the cost of the guarantee that
         only a validated family is used.
      3. **Pin the media** to 261.1-3 so it matches what was validated.

      Whichever is chosen, the interesting part is that this drift existed
      silently until something actually ran the installer against real media.

      **Decided 2026-08-05: option 2, applied per tool rather than uniformly.**
      Implemented across three PRs:

      | Repo | PR | Change |
      |---|---|---|
      | fisherman | [#14](https://github.com/frostyard/fisherman/pull/14) | per-tool policy, Debian version comparison, detected-version provenance |
      | snosi | [#509](https://github.com/frostyard/snosi/pull/509) | normative contract text |
      | dakota-iso | [#16](https://github.com/frostyard/dakota-iso/pull/16) | the SMBIOS QA-SSH transport that got us here |

      - **bootc stays exact.** Its integration depends on observed,
        non-upstream-stable behaviour of 1.16.3, so a newer release is the thing
        most likely to break it silently. This was not relaxed.
      - **systemd and cosign are floors**, with a validated set. Above the floor
        but unvalidated installs and *warns* — on stderr and as a
        `secure_install` progress event — rather than passing silently.
      - Provenance now records **detected** versions, not the contract's declared
        floors, which is what makes the policy auditable afterwards.

      The argument that settled it: an exact pin on a routinely rebuilt medium
      creates standing pressure to edit the pinned number instead of
      revalidating, and a check maintainers are trained to defeat protects
      nothing. The Task 9 harness proves directly — unattended TPM unlock across
      a reboot — what a version string only proxies.

- [ ] **D4d. The release chain, executed 2026-08-05.**

      1. [x] fisherman **v0.2.3** cut and published; verified the released
             binary carries the change and its digest matches `checksums.txt`.
      2. [x] dakota's `SNOW_SECURE_FISHERMAN_URL` / `_SHA256` repointed at it.
      3. [x] fisherman submodule bumped to `v0.2.3` in bootc-installer
             ([#22](https://github.com/frostyard/bootc-installer/pull/22)).
      4. [ ] secure Dakota ISO rebuilt against it — dispatched.
      5. [ ] re-run `run-secure-install-tests`.

      **A release-process defect worth fixing.** `release-cut.yml` pushes the
      version tag using `GITHUB_TOKEN`, and GitHub deliberately does not fire
      workflows from that token. `release-publish.yml` triggers only on
      `push: tags` and has no `workflow_dispatch`, so **cutting a release
      silently produces a tag and no release**. It needed a manual
      delete-and-re-push under a user credential to publish v0.2.3, and it will
      need that every time. Fix is one of: add `workflow_dispatch` to
      `release-publish.yml`, or have `release-cut` push the tag with a PAT.
- [x] **D5.** Recovery runners done; negative runners removed by decision.
      The update runner remains (now D6).
