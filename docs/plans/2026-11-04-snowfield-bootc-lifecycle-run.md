# Plan: Snowfield rerun with lockdown fixed

This plan records one Snowfield lifecycle rerun after snosi#1214 fixed the
missing `lockdown=integrity` UKI command-line karg found by the first run.
It adds one attested lifecycle result for Snowfield. It does not change the
lane, qualification or known-defect rules.

## Selected inputs

The manifest is
[`manifest.json`](snowfield-bootc-lifecycle-run-2026-11-04/manifest.json).
Its file SHA-256 is `e69c143c…8a2e2f4`. The canonical lane
`manifest_sha256` is
`04e38af01ad38b4ad8e5c3cadadd39f1f75b6115938f55c78924a6ed4d3163b3`.

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261104213621_x86-64.iso`, SHA-256 `2850a0ce…d79c5bf` | The only signed index entry at preflight (Firn 0.11.0). |
| N | `20261104172603`, `sha256:4c92f9aa345de1a82df3b5b9e8ab878dd67ac8c6013a2ba64cd5b800769a3db0` | Bootc assembly `bootc-1.17.1-storage-digest-v1`; it carries the snosi#1214 fix. |
| N+1 | `20261104190408`, `sha256:e7b09eb5a75cae19feee363f6ea36607ebd276a03ef19e9d1c24eb693c1fb4e4` | Bootc assembly `bootc-1.17.1-storage-digest-v1`; it carries the snosi#1214 fix. |
| Target | `ghcr.io/frostyard/snowfield:qa-controlled` | Operator-owned by Brian. It moved to N+1 at desk T828b and remained at N+1 after the run. |
| Trust pins | snosi `39cf1988`, index key `977fe2de…0fc97`, cosign key `3e797504…14fc5`, MOK cert `0446e478…545b4` | Unchanged from the first Snowfield run. |

## Known causes

- **btrfs fs-verity on linux-surface ([snosi#1130](https://github.com/frostyard/snosi/issues/1130)).**
  The first Snowfield plan describes this known cause in
  [Known causes](2026-11-03-snowfield-bootc-lifecycle-run.md#known-causes-of-a-non-pass-result).
  A positive `fsverity_errors` count on a kernel not on `known_defect_kernels`
  ends `FAILED: fsverity_errors`. This is a valid finding, not a lane defect.
- **Lockdown is product evidence.** The serial log must show the integrity
  lockdown mode under Secure Boot. The rerun fixes the earlier blocked gate.
- **Hardware qualification.** This VM run does not qualify Surface hardware,
  graphical hardware or a Microsoft Surface deployment.

## Phase 1 — Preflight (read-only)

The [preflight record](snowfield-bootc-lifecycle-run-2026-11-04/preflight-record.txt)
ran at 2026-11-05T00:10Z. Every check passed. `target_ref PASS` resolved
N+1, and `lane_preflight PASS` reported `failures=0 target_ready=1`.

- **Done when:** the record shows every check `PASS`, `lane_preflight PASS`,
  `failures=0` and `target_ready=1`. Met at 2026-11-05T00:10Z.

## Phase 2 — Dev run

Submit one workflow from lab commit `900981c2` at `origin/main`. The branch
had no lane code change. The run used the `lab-dev` lease, thread T853.

- **Done when:** the workflow ends, keeps `keep-vm-on-failure=false`, publishes
  nothing, moves no tag and returns result evidence. Met for
  `dev-snow-bootc-lifecycle-v4cgb`, with the lease released after the run.

## Phase 3 — Record the result

Record the workflow name, timestamps, manifest, result and kept evidence.
The VM was deleted. The evidence directory keeps `checks.txt`, `checks.json`,
`fsverity.txt`, `first-login.txt`, `manifest-sha256.txt`, `preflight.json`,
`result-summary.txt`, `serial-channel.txt` and `serial-redacted.log`.

- **Done when:** the result and all five phase records are durable in this
  plan. Met at 2026-11-05T00:27:17Z.

## Result 2026-11-05

`dev-snow-bootc-lifecycle-v4cgb`, submitted 2026-11-05T00:12:27Z and ended
2026-11-05T00:27:17Z. It used lab commit `900981c2` (`origin/main`), label
`snosi.io/trigger=dev`, `keep-vm-on-failure=false`, no publish and no tag move.
The `lab-dev` lease (thread T853) was released after the run.

```
FAILED: fsverity_errors
```

Canonical `manifest_sha256`:
`04e38af01ad38b4ad8e5c3cadadd39f1f75b6115938f55c78924a6ed4d3163b3`.

| Phase | Boot ID | Kernel | fs-verity errors | Kept lines |
| --- | --- | --- | --- | --- |
| `installed-n` | `dc0211bd-cc6a-42a6-8014-92376f25dbfb` | `7.2.15-1-surface` | 2 | 2 |
| `stage` | `aba41c81-e8ae-42da-82d6-64a32a1890bc` | `7.2.15-1-surface` | 1 | 1 |
| `boot-n-plus-1` | `24c2872c-8fa7-4e8e-8cc6-ea33ffd02a2e` | `7.2.15-1-surface` | 0 | 0 |
| `rollback` | `c402f53f-39ff-453b-885a-2ea4eeaf7fe9` | `7.2.15-1-surface` | 1 | 1 |
| `boot-n` | `756a05d1-f1f7-44e8-9bcb-4ce9bce43dd4` | `7.2.15-1-surface` | 2 | 2 |

### `checks.txt`

```
manifest_sha256=04e38af01ad38b4ad8e5c3cadadd39f1f75b6115938f55c78924a6ed4d3163b3
tag=before-install timestamp=2026-11-05T00:13:36.302190+00:00
tag=before-installed-n timestamp=2026-11-05T00:19:08.303399+00:00
phase=installed-n boot_id=dc0211bd-cc6a-42a6-8014-92376f25dbfb kernel=7.2.15-1-surface fsverity_errors=2
tag=before-stage timestamp=2026-11-05T00:21:37.230701+00:00
phase=stage boot_id=aba41c81-e8ae-42da-82d6-64a32a1890bc kernel=7.2.15-1-surface fsverity_errors=1
tag=after-stage timestamp=2026-11-05T00:22:40.761518+00:00
tag=before-boot-n-plus-1 timestamp=2026-11-05T00:23:11.649782+00:00
phase=boot-n-plus-1 boot_id=24c2872c-8fa7-4e8e-8cc6-ea33ffd02a2e kernel=7.2.15-1-surface fsverity_errors=0
tag=after-boot-n-plus-1 timestamp=2026-11-05T00:23:44.075011+00:00
tag=before-rollback timestamp=2026-11-05T00:24:30.039918+00:00
phase=rollback boot_id=c402f53f-39ff-453b-885a-2ea4eeaf7fe9 kernel=7.2.15-1-surface fsverity_errors=1
tag=after-rollback timestamp=2026-11-05T00:25:02.495191+00:00
tag=before-boot-n timestamp=2026-11-05T00:25:48.462602+00:00
phase=boot-n boot_id=756a05d1-f1f7-44e8-9bcb-4ce9bce43dd4 kernel=7.2.15-1-surface fsverity_errors=2
```

### `fsverity.txt`

```
phase=installed-n seq=1 device=dm-0 inode=145627 pos=36831232 level=-1
phase=installed-n seq=2 device=dm-0 inode=145627 pos=36831232 level=-1
phase=stage seq=1 device=dm-0 inode=228871 pos=8372224 level=-1
phase=rollback seq=1 device=dm-0 inode=228871 pos=8372224 level=-1
phase=boot-n seq=1 device=dm-0 inode=230733 pos=7995392 level=-1
phase=boot-n seq=2 device=dm-0 inode=228871 pos=8372224 level=-1
```

### Lockdown evidence

The serial log shows this command line:

```
rw composefs=?[REDACTED] lockdown=integrity console=uart,io,0x3f8 console=tty0
```

It also shows:

```
Kernel is locked down from command line; see man kernel_lockdown.7
Secure boot enabled
```

The guest `lockdown` gate passed in all 5 phases. Kernel
`7.2.15-1-surface` ran in all 5 phases. Each phase has a record on serial and
kmsg (`serial-channel.txt`).

### What it shows

- The snosi#1214 lockdown fix is effective. The UKI command line contains
  `lockdown=integrity`, and the kernel reaches the integrity lockdown state.
- All 5 lifecycle boots ran. The serial and kmsg record exists for every phase.
- The fs-verity errors are in the four boots of N (`installed-n`, `stage`,
  `rollback`, `boot-n`). The one boot of N+1 has 0 `FILE CORRUPTED` lines.
- The linux-surface kernel is not on `known_defect_kernels`. The lane correctly
  reports `FAILED: fsverity_errors`.

### What it does not prove

- It does not prove a pass. It does not prove that fs-verity corruption is
  fixed for this kernel.
- It does not qualify Snowfield on representative Microsoft Surface hardware.
- It does not prove graphical hardware behavior or desktop operation.

The VM is gone because `keep-vm-on-failure=false`. Evidence stays on minideb
under `/var/lib/snosi-lab/snow-bootc-evidence/dev-snow-bootc-lifecycle-v4cgb/`.
The `lab-dev` lease is released.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Decisions: [ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md),
  [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0014](../adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
- Precedents: [first Snowfield run](2026-11-03-snowfield-bootc-lifecycle-run.md),
  [Sundog bootc 1.17.1 run](2026-11-01-sundog-bootc117-lifecycle-run.md)
