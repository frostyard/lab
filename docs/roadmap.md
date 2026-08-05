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
| OS | secure install path (external Dakota / bootc-installer / Fisherman) | 🔴 not covered anywhere — snosi's own live harness is `BLOCKED` for lack of a prepared runner |
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
| **snosi's missing Task 9 runners** | `dakota-iso` `test/bootc-secure-{installer,negative,recovery,update-*}-runner.sh` + `test/lib/bootc-secure-runner-lib.sh` | **exists, pushed** |

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
- [ ] **A4.** *First-green requirement*: a lane that has never once succeeded
      reports `unproven`, not `Failed`. This is the control that would have
      caught both bugs, and it is cheap.
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

### Phase D — stand up the secure runner environment on selfie

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
- [ ] **D5.** Then the negative and recovery runners, then the update runner.

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

- [ ] **F1.** `virt-fw-vars --add-mok` into each guest's `qemu.nvram` before
      first boot, as `native-ab-secure-boot-test.sh` does.
- [ ] **F2.** Install lane to `secureboot=true`, drop `--skip-mok`, assert the
      installed system boots enforced and unattended.
- [ ] **F3.** Negative proof — a wrongly-signed binary must be rejected by shim,
      or the positive result proves nothing.
- [ ] **F4.** Published-disk lane under enforced SB with the MOK pre-seeded.

Note the two formats need *different* MOK handling: native pre-seeds host-side,
while the secure bootc installer generates a one-time MokManager password and
stages enrollment itself. Do not unify them.

### Phase G — matrix, updates, gating

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
