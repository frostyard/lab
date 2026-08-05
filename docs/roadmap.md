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

### 2. The bootc install lane used an unsupported invocation

The lane ran:

```
bootc install to-disk --wipe --filesystem ext4 <disk>
```

snosi's own reference invocation, in `test/lib/vm.sh` (`install_to_disk`), is:

```
bootc install to-disk --generic-image --via-loopback \
  --composefs-backend --filesystem btrfs --karg console=ttyS0 <disk>
```

The missing flag is `--composefs-backend`. snosi's bootc images are composefs
deployments; without it, bootc lays down an ostree-style deployment that the
image's own UKI cannot mount — which is exactly the emergency-mode boot with no
`/dev/gpt-auto-root` this lane was reporting.

**Consequence:** [frostyard/snosi#504][504] and [frostyard/snosi#505][505] were
filed on that premise and are very likely invalid. #505 in particular argued
that the images should ship `/usr/lib/bootc/install/*.toml`; snosi instead
passes `--filesystem` explicitly at the call site, so the absence of that config
is a deliberate choice rather than a gap. Both need correcting on the issue
tracker once the corrected lane reports.

[504]: https://github.com/frostyard/snosi/issues/504
[505]: https://github.com/frostyard/snosi/issues/505

**The transferable lesson:** a lane that has never once been green is evidence
about the lane, not about the thing under test. Both of these produced
plausible narratives that survived because the red was assumed to be a finding.

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
| Installer | `bootc install to-disk` completes | 🟢 lab |
| OS | installed system boots | 🟡 **re-testing** with corrected flags |
| OS | secure install path (external Dakota / bootc-installer / Fisherman) | 🔴 not covered anywhere — snosi's own live harness is `BLOCKED` for lack of a prepared runner |
| OS | update / rollback | 🔴 not covered |

The single largest genuine gap is the last bootc row. Per snosi's own notes, the
supported secure bootc install path is an **external installer driven by a
recipe**, not `bootc install to-disk` — which is explicitly the "legacy
mechanics tier that provides no security evidence". This lab currently tests
only the mechanics tier and should stop implying otherwise.

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

### Phase A — make the lab's own results trustworthy

Nothing downstream is worth building on a harness that produced two false
findings in one week.

- [x] **A1.** Fix the disk lane's SIGPIPE/pipefail inversion.
- [ ] **A2.** Re-run the bootc lane with snosi's reference invocation. *(in flight)*
- [ ] **A3.** Correct or close #504 and #505 with what the corrected run shows.
- [ ] **A4.** Add a *first-green requirement*: a lane that has never once
      reported success is marked `unproven` on the dashboard rather than
      `Failed`. A lane's first green is what licenses reading its red as a
      finding. This is the specific control that would have caught both bugs.
- [ ] **A5.** Audit every lane for the same pipefail shape and for assertions
      that can only ever fail (the bootc lane asserted `/sysroot/ostree`, a path
      a composefs-backend install does not have).

### Phase B — close the Secure Boot gap

Adopt snosi's existing pattern; no new design.

- [ ] **B1.** Add `virt-firmware` to the VM-lane runner image and pre-seed the
      MOK into each guest's `qemu.nvram` before first boot.
- [ ] **B2.** Flip `run-incus-install-tests` to `secureboot=true`, drop
      `--skip-mok`, and assert the installed system boots enforced and
      unattended — the lab equivalent of e2e step 7.
- [ ] **B3.** Add a *negative* proof, mirroring `native-installer-iso-test.sh`:
      a deliberately wrongly-signed binary must be rejected by shim. Without
      this, a permissive OVMF config would make every positive result
      meaningless.
- [ ] **B4.** Re-run the published-disk lane with the MOK pre-seeded and assert
      it boots enforced.

### Phase C — prove install-and-boot across the matrix

Today one product (`cayo`) is proven for one format. The claim needs to be
`{cayo, snow, snowfield} × {native A/B, bootc}`.

- [ ] **C1.** Parameterize both install lanes over product; add snow and
      snowfield. Expect capacity differences — snow/snowfield use 8 GiB root
      slots against cayo's 5 GiB.
- [ ] **C2.** Run the **behave suite against the installed VM**, not just
      console assertions. Today the container lanes run 20/14 tests and the VM
      lanes assert a handful of console markers; a VM that boots is not the same
      claim as a VM that is correct. This unifies the two halves of the lab and
      is where `snowfield`'s Surface kernel finally gets covered, since no
      container lane can assert on a kernel.
- [ ] **C3.** Assert the two paths converge: a natively-installed system and a
      bootc-installed system of the same product should pass the same suite,
      modulo documented differences (LUKS `/var`, `bootc status`).

### Phase D — the update path

- [ ] **D1.** A/B update lane: install N, publish N+1, stage, reboot, assert the
      slot flipped and `/var` + `/etc` persisted.
- [ ] **D2.** Rollback and boot-count fallback.
- [ ] **D3.** bootc update lane once C is green.

The machinery for this already exists — SMBIOS credential injection plus
console assertions is exactly what D needs, and it is what the install lanes
already use.

### Phase E — CI gating

Only worth starting once B and C are green. A gate that fires on a harness bug
is worse than no gate: it trains people to override it.

**Do not reimplement the lab in GitHub Actions.** snosi's `test-bootc-secure.yml`
already declares its live jobs as:

```yaml
runs-on: [self-hosted, linux, x64, bootc-secure]
```

and they are `BLOCKED` for want of a prepared runner. `selfie` has KVM, swtpm,
incus, and now these harnesses. The shortest path from here to CI gating is to
**register selfie as that runner** rather than to port anything.

Proposed tiering:

| Tier | Trigger | Runs | Gate? |
|---|---|---|---|
| 1 | PR | existing static/fixture gates (`validate.yml`, bootc-secure contracts) — unchanged | blocking, already is |
| 2 | post-build, pre-promotion | install-and-boot for the built product, both formats, SB enforced | **blocking** — this is the new gate |
| 3 | nightly | full matrix + update/rollback | non-blocking, reported |
| 4 | continuous | published artifacts, on digest change | non-blocking — catches drift between what CI built and what users download |

Tier 4 is what this lab does today, and it stays valuable after gating exists:
CI tests a *candidate*, the lab tests what is actually being served.

Native A/B already has a version of tier 2 — `build-native-images.yml` runs
`native-boot-smoke-test.sh` before promotion — but with Secure Boot **not**
enforced. Phase B is what would let that tier assert the real posture.

---

## Open questions for the maintainer

1. **Should the lab test the bootc mechanics tier at all?** `bootc install
   to-disk` is explicitly labelled as providing no security evidence, and the
   supported secure path is the external installer. Testing mechanics is
   defensible, but the dashboard should say "mechanics" rather than "bootc
   installer".
2. **Is the external bootc installer (Dakota / bootc-installer / Fisherman)
   available for the lab to drive?** It is the single biggest coverage gap, and
   snosi's own harness is blocked on the same thing.
3. **Should the lab become the `bootc-secure` self-hosted runner?** That is a
   trust-boundary decision — it means a GitHub workflow can execute on selfie.
