# Plan: Lockdown proof for snosi#1214 on Snow and Snowfield

This plan records the lab proof of
[snosi#1214](https://github.com/frostyard/snosi/pull/1214), which seals
`lockdown=integrity` into the signed UKI command line of every secure product.
Each product gets 1 dev lifecycle run in which N predates #1214 and N+1 carries
it: fresh install of N with Secure Boot and TPM2, update to N+1, rollback to N.
The runs check, on every boot, the command line, the kernel lockdown mode and
an unattended TPM2 unlock across the change of the sealed command line (PCR 11).
For Snow, N to N+1 is also the first lab run of bootc 1.17.1 to 1.17.3
([snosi#1223](https://github.com/frostyard/snosi/pull/1223)).

The same pull request adds the lane changes that the runs need: the bootc 1.17.3
assembly label, and per-boot lockdown and command-line evidence. It does not
change a lane gate, the qualification rules or the known-defect list.

## Selected inputs

The manifests are [`manifest-snow.json`](lockdown-1214-lifecycle-runs-2026-11-05/manifest-snow.json)
and [`manifest-snowfield.json`](lockdown-1214-lifecycle-runs-2026-11-05/manifest-snowfield.json).

| Input | Snow | Snowfield |
| --- | --- | --- |
| N | `20261104151522`, `sha256:80b9785447b30c3ab80e9fc2ffcfbf1b35385de84bfb59c345129d4120adfd33`, bootc 1.17.1 | `20261104151218`, `sha256:80ee199c97a4fa4ae71919530e2d141866c1d2b84713a04caa3d71aad0f7192d`, bootc 1.17.1 |
| N source | snosi `42677d15`, before the #1214 merge `da2f6fc7` | snosi `42677d15`, before the #1214 merge `da2f6fc7` |
| N+1 | `20261105001236`, `sha256:82f06d9f3defffa0c74b072f11139c8596037431d6270aea883b83c23698a3ba`, bootc 1.17.3 | `20261104190408`, `sha256:e7b09eb5a75cae19feee363f6ea36607ebd276a03ef19e9d1c24eb693c1fb4e4`, bootc 1.17.1 |
| Target | `ghcr.io/frostyard/snow:qa-controlled` = N+1 (Brian, T797c) | `ghcr.io/frostyard/snowfield:qa-controlled` = N+1 (Brian, T828b) |
| Canonical `manifest_sha256` | `6b3eb8f365d953e141f2a9c2e05b7dedf95715e2c0f13024c7ea88b1b2846a9c` | `de244723f55821fc10efb970158c6f5d34f4d0716e817c718b040f376c3e6674` |
| ISO | `snosi-installer_20261104213621_x86-64.iso`, SHA-256 `2850a0ce…d79c5bf` (Firn 0.11.0), the only signed index entry | same |
| Trust pins | snosi `39cf1988`, index key `977fe2de…0fc97`, cosign key `3e797504…14fc5`, MOK cert `0446e478…545b4` | same |

Each N is the last build of its product before #1214 merged. The brief named
Snowfield `20261026191603` as an example; it ships bootc 1.16.8 and is 9 days
older, so the newer N isolates the #1214 change better.

## Known causes

- **Snowfield N boots at lockdown `none`
  ([snosi#1213](https://github.com/frostyard/snosi/issues/1213)).** Every
  Snowfield build before #1214 does. The lane's guest `lockdown` gate stops
  such a boot (run `pnljz`), and #1213 says not to relax it. The Snowfield run
  therefore uses a **dev-only lane copy that is never merged**:
  [`snowfield-dev-variant.patch`](lockdown-1214-lifecycle-runs-2026-11-05/snowfield-dev-variant.patch).
  It accepts exactly `[none] integrity confidentiality` only when the product
  is Snowfield and the booted digest is N, and records `lockdown=none`. Every
  N+1 boot, and every Snow boot, keeps the full gate.
- **btrfs fs-verity on linux-surface ([snosi#1130](https://github.com/frostyard/snosi/issues/1130)).**
  Snowfield's kernel is not on `known_defect_kernels`, so a positive
  `fsverity_errors` count ends `FAILED: fsverity_errors`
  ([first Snowfield run](2026-11-03-snowfield-bootc-lifecycle-run.md#known-causes-of-a-non-pass-result)).
  The list stays unchanged.
- **Snow and Debian kernels.** Debian kernels raise lockdown to `integrity`
  under Secure Boot by themselves, so Snow N boots locked down without a
  `lockdown=` token. Only the source of the lockdown changes on Snow.

## How TPM unlock is shown

The installed VM never receives the LUKS passphrase or the recovery key: the
installer VM keeps them in its own `/run`, and the lane answers no prompt on
the installed console. The `tpm_token` gate requires exactly 1 `systemd-tpm2`
token bound to the signed PCR 11 policy. So every phase that reaches its
attested `SNOW_QA_V1` record unlocked the root volume through TPM2 with no
prompt answered; a prompt would hang the boot until `phase_timeout`.

## Phase 1 — Lane changes (this pull request)

- Accept `bootc-1.17.3-storage-digest-v1` in the exact assembly allowlist
  (`qa.py` `ASSEMBLIES`, operator preflight `checks.sh`).
- Record per boot `lockdown_mode`, `cmdline_lockdown` and the command-line
  tokens (32+ hex runs elided), in `checks.txt` and the new `cmdline.txt`.
  Evidence only: the collectors never fail a boot.
- **Done when:** the full test suite passes and the cross-review is Ready.

## Phase 2 — Preflight (read-only)

[`preflight-snow.txt`](lockdown-1214-lifecycle-runs-2026-11-05/preflight-snow.txt)
(2026-11-05T02:35Z) and
[`preflight-snowfield.txt`](lockdown-1214-lifecycle-runs-2026-11-05/preflight-snowfield.txt)
(2026-11-05T02:09Z, before the 1.17.3 label commit; both Snowfield images are
1.17.1) show every check `PASS`, `lane_preflight PASS`, `failures=0` and
`target_ready=1`.

- **Done when:** both records show `failures=0` and `target_ready=1`. Met.

## Phase 3 — Dev runs

Submit 1 workflow per product with
[`scripts/snow-lifecycle-dev-submit.sh`](../../scripts/snow-lifecycle-dev-submit.sh)
(`SUBMIT` through `ssh minideb sudo -n k3s kubectl create`), under the
`lab-dev` lease (thread T797, taken 03:56:47Z after T855 released it). Snow ran
from this pull request's branch at `9b4a0698`; Snowfield ran from the dev-only
lane copy at `f2a662cd` (this branch plus
[the patch](lockdown-1214-lifecycle-runs-2026-11-05/snowfield-dev-variant.patch)).
Both have label `snosi.io/trigger=dev`, `keep-vm-on-failure=false`, no publish
and no tag move. The `snosi-vm-qa` semaphore (1) ran them one after the other.

- **Done when:** both workflows end with a result and per-phase evidence, the
  VMs are gone, the targets still point at N+1, and the lease is released. Met
  at 2026-11-05T04:30:40Z.

## Result 2026-11-05

| Product | Workflow | Ran (UTC) | Result |
| --- | --- | --- | --- |
| Snow | `dev-snow-bootc-lifecycle-rj697` | 03:59:41 to 04:14:58 | `PASS: verified_five_fresh_boots;fsverity_known_defect=7.2.12+deb14-amd64` |
| Snowfield | `dev-snow-bootc-lifecycle-dm5bp` | 03:59:44 (queued) to 04:30:40 | `FAILED: fsverity_errors` |

Every boot below reached its attested record over serial and kmsg
(`serial-channel.txt`: `serial_record=1 kmsg_record=1` in all 10 phases), so
every boot unlocked its LUKS root through TPM2 with no prompt answered (see
[How TPM unlock is shown](#how-tpm-unlock-is-shown)). The kept Snowfield serial
log (last boot, `boot-n`) shows `Finished systemd-cryptsetup@root.service`
and no passphrase prompt.

### Snow (`dev-snow-bootc-lifecycle-rj697`): PASS

| Phase | Booted | bootc | Command line (`cmdline.txt`) | `lockdown` | fs-verity errors |
| --- | --- | --- | --- | --- | --- |
| `installed-n` | N | 1.17.1 | `rw composefs=?<hex> console=uart,io,0x3f8 console=tty0` | `integrity` | 1 |
| `stage` | N | 1.17.1 | same as N | `integrity` | 1 |
| `boot-n-plus-1` | N+1 | 1.17.3 | `rw composefs=?<hex> lockdown=integrity console=uart,io,0x3f8 console=tty0` | `integrity` | 3 |
| `rollback` | N+1 | 1.17.3 | same as N+1 | `integrity` | 1 |
| `boot-n` | N | 1.17.1 | same as N | `integrity` | 1 |

Snow N has no `lockdown=` token and boots at `integrity` from the Debian
kernel's Secure Boot lockdown; N+1 carries the sealed token. The bootc 1.17.1
updater staged N+1, and bootc 1.17.3 in N+1 ran the rollback to N: the first
lab run across bootc 1.17.1 and 1.17.3. Kernel `7.2.12+deb14-amd64` is on
`known_defect_kernels`, so its fs-verity errors pass as a known defect
([snosi#1130](https://github.com/frostyard/snosi/issues/1130)).
Evidence: [`checks.txt`](lockdown-1214-lifecycle-runs-2026-11-05/dev-snow-bootc-lifecycle-rj697/checks.txt),
[`cmdline.txt`](lockdown-1214-lifecycle-runs-2026-11-05/dev-snow-bootc-lifecycle-rj697/cmdline.txt),
[`fsverity.txt`](lockdown-1214-lifecycle-runs-2026-11-05/dev-snow-bootc-lifecycle-rj697/fsverity.txt).

### Snowfield (`dev-snow-bootc-lifecycle-dm5bp`): FAILED, fs-verity only

| Phase | Booted | Command line (`cmdline.txt`) | `lockdown` | fs-verity errors |
| --- | --- | --- | --- | --- |
| `installed-n` | N | `rw composefs=?<hex> console=uart,io,0x3f8 console=tty0` | `none` (dev copy) | 2 |
| `stage` | N | same as N | `none` (dev copy) | 0 |
| `boot-n-plus-1` | N+1 | `rw composefs=?<hex> lockdown=integrity console=uart,io,0x3f8 console=tty0` | `integrity` | 0 |
| `rollback` | N+1 | same as N+1 | `integrity` | 0 |
| `boot-n` | N | same as N | `none` (dev copy) | 0 |

N boots at lockdown `none` (snosi#1213, accepted only by the dev copy); both
N+1 boots run at `integrity` with the sealed token, under the unchanged gate.
All 5 boots ran on kernel `7.2.15-1-surface`. The only fs-verity errors are 2
in `installed-n`, on inode 145627, the same inode as `installed-n` of run
`v4cgb`. linux-surface is not on `known_defect_kernels`, so the lane fails it,
as expected; the list stays unchanged.
Evidence: [`checks.txt`](lockdown-1214-lifecycle-runs-2026-11-05/dev-snow-bootc-lifecycle-dm5bp/checks.txt),
[`cmdline.txt`](lockdown-1214-lifecycle-runs-2026-11-05/dev-snow-bootc-lifecycle-dm5bp/cmdline.txt),
[`fsverity.txt`](lockdown-1214-lifecycle-runs-2026-11-05/dev-snow-bootc-lifecycle-dm5bp/fsverity.txt).

### What it shows

- snosi#1214 works on both products: every N+1 boot has the sealed
  `lockdown=integrity` token and runs at lockdown `integrity`. On Snowfield it
  turns lockdown from `none` (N) to `integrity` (N+1).
- TPM2 unlock survives the change of the sealed command line, and so of PCR 11,
  in both directions: N to N+1 and the rollback to N, on both kernels.
- Snow crosses bootc 1.17.1 to 1.17.3 and back with all gates passing.

### What it does not prove

- Snowfield's N boots ran under a dev-only lane copy; the main lane still
  blocks a Snowfield image at lockdown `none`, which is correct.
- It does not prove a Snowfield pass, a fix of fs-verity on linux-surface, or
  Surface hardware, graphical or desktop behavior.

The VMs are gone (`keep-vm-on-failure=false`). Evidence stays on minideb under
`/var/lib/snosi-lab/snow-bootc-evidence/`; a PASS run keeps no serial log.
The `lab-dev` lease is released.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Decisions: [ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md),
  [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0014](../adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
- Precedents: [Snowfield rerun with lockdown fixed](2026-11-04-snowfield-bootc-lifecycle-run.md),
  [first Snowfield run](2026-11-03-snowfield-bootc-lifecycle-run.md)
