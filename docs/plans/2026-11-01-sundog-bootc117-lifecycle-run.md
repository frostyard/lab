# Plan: Sundog lifecycle run with a bootc 1.17.1 N (snosi#1130 prediction, lab#174)

This plan prepares and records dev runs of the
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
lane for **Sundog**, with an image N that already has bootc 1.17.1. It tests the
prediction of [snosi#1130](https://github.com/frostyard/snosi/issues/1130)
(investigation update 5): bootc 1.17.1 (`9aad3dd87`) passes the manifest verity
and skips the full re-read at stage time, so the `stage` phase of such a run
should log no more fs-verity errors than a plain boot of N (its boot noise).
Every earlier lifecycle run staged with bootc 1.16.8.

It is also the first run of the lane with the kept kernel lines
(`fsverity.txt`, [lab#174](https://github.com/frostyard/lab/issues/174),
[quality.md](../quality.md#kept-kernel-lines-fsveritytxt-lab174)), so the
`stage` lines can be matched to the boot lines by inode. The lane stays
advisory and not a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
[ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)).

## Selected inputs

The manifest is [`manifest.json`](sundog-bootc117-lifecycle-run-2026-11-01/manifest.json)
(file SHA-256 `e0dd6737…dcf856`; canonical lane `manifest_sha256`
`c28607fd…a23524`). Every value is public. Selected on 2026-11-02 at about
04:05Z. snosi was read only.

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261102034308_x86-64.iso`, SHA-256 `e38361f9…1725bb` | The single entry in the signed `SHA256SUMS` index at selection and at both submits. Firn 0.10.0 (`2a965b92…497af`), so no `compress=zstd:1` install (firn#119). |
| N | `20261026201734`, `sha256:b326e4f1…7896d0` | `bootc-1.17.1-storage-digest-v1`: the first Sundog build after snosi#1157, one build after the N of [`rs6sh`](2026-10-30-sundog-bootc-lifecycle-run.md) (`20261026191618`, bootc 1.16.8). |
| N+1 | `20261030194311`, `sha256:199ca989…65da4` | The same N+1 as `rs6sh`, so N's bootc is the one variable between the two runs. |
| Target | `ghcr.io/frostyard/sundog:qa-controlled` | Already at N+1 since the `rs6sh` run (T589a). No tag was created or moved. |
| Trust pins | snosi `39cf1988`, index key `977fe2de…0fc97`, cosign key `3e797504…14fc5`, MOK cert `0446e478…545b4`, fingerprint `F37282A3…8216FD68` | The lane's pinned values, unchanged. |

The [preflight record](sundog-bootc117-lifecycle-run-2026-11-01/preflight-record.txt)
shows every check `PASS`, `target_ref PASS`, `lane_preflight PASS full qa.py
preflight` and `failures=0` (2026-11-02T04:10:25Z).

## Runs

Both runs are dev runs from the lab#174 branch (`murbella/lab174-fsverity-lines`
at `462597f3`), submitted with
[`scripts/snow-lifecycle-dev-submit.sh`](../../scripts/snow-lifecycle-dev-submit.sh)
(`SUBMIT` through `ssh minideb sudo -n k3s kubectl create`) under the `lab-dev`
lease (thread T676). They publish nothing and move no tag (standing permission
T237f, T551a); `keep-vm-on-failure` stays `false`.

### `dev-snow-bootc-lifecycle-pcl2l`: BLOCKED, phase_timeout in `rollback`

Submitted 04:46:32Z, ended 05:33:35Z.

```
BLOCKED: phase_timeout
```

| Phase | Boot ID | Kernel | fs-verity errors | Kept lines |
| --- | --- | --- | --- | --- |
| `installed-n` | `441ad3ab-9975-483e-a2da-67137c6d2562` | `7.2.12+deb14-amd64` | 0 | 0 |
| `stage` | `7f02bfbe-da64-45e1-a304-f5fd170b148d` | `7.2.12+deb14-amd64` | 0 | 0 |
| `boot-n-plus-1` | `0c4571e7-43d1-4a1c-a688-c1bcf28eca19` | `7.2.12+deb14-amd64` | 0 | 0 |
| `rollback` | no record | | | |

- The `rollback` boot's serial log stops about 9 s after switch-root (last
  line `[ 9.276587] Loaded X.509 cert 'wens: …'`), kernel lines included, and
  no record arrived in the 2100 s window. This is the intermittent Sundog
  `ttyS0` stall at switch-root (lab#168,
  [ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md));
  the lifecycle lane has no kmsg path. The lane reports it as BLOCKED, not as
  a product failure.
- The evidence directory has `fsverity.txt` (empty: no phase kept a line) and
  the redacted serial log.

### `dev-snow-bootc-lifecycle-52r5s`: BLOCKED, phase_timeout in `stage`

Submitted 05:33:42Z, ended 06:47:50Z.

```
BLOCKED: phase_timeout
```

| Phase | Boot ID | Kernel | fs-verity errors | Kept lines |
| --- | --- | --- | --- | --- |
| `installed-n` | `5706f2b9-04df-44ae-b986-47d91d14686e` | `7.2.12+deb14-amd64` | 3 | 3 |
| `stage` | no record | | | |

`fsverity.txt` (the first kept lines of any lane run):

```
phase=installed-n seq=1 device=dm-0 inode=193692 pos=29097984 level=-1
phase=installed-n seq=2 device=dm-0 inode=193692 pos=29229056 level=-1
phase=installed-n seq=3 device=dm-0 inode=193692 pos=29097984 level=-1
```

- All 3 errors of this N boot are in one file (inode 193692 on the LUKS root
  `dm-0`), at 2 offsets 128 KiB apart, one of them twice. The VM is deleted, so
  the path is not known.
- The `stage` boot stopped at the same point as the `pcl2l` rollback boot: the
  serial log ends at `[ 9.864573] Loaded X.509 cert 'wens: …'`. The guest was
  still `RUNNING` but idle: the QEMU process used no CPU time in 15 s (67 s in
  16 min). The guest unit powers off only after its record is written, so the
  most likely reading is the `ttyS0` stall of lab#168 holding the record write.
  Not proven: the VM was deleted.

### `dev-snow-bootc-lifecycle-vxlsb`: PASS

Submitted 06:48:12Z, ended 07:01:53Z. Workflow outputs `result`, `checks` and
`fsverity`.

```
PASS: verified_five_fresh_boots
```

| Phase | Boot ID | Kernel | fs-verity errors | Kept lines |
| --- | --- | --- | --- | --- |
| `installed-n` | `7b3308c3-a835-4ab4-98c3-b03c9d42d68b` | `7.2.12+deb14-amd64` | 0 | 0 |
| `stage` | `991a7398-01c3-436f-9d84-5978689b69b4` | `7.2.12+deb14-amd64` | 0 | 0 |
| `boot-n-plus-1` | `7ca2d341-c09e-414a-8599-d7c8fdb56bc1` | `7.2.12+deb14-amd64` | 0 | 0 |
| `rollback` | `fa17b644-5cb7-419a-87aa-57e59a98bc18` | `7.2.12+deb14-amd64` | 0 | 0 |
| `boot-n` | `2b1e4dbd-5aa7-4b8c-b41b-6d6c24eed352` | `7.2.12+deb14-amd64` | 0 | 0 |

`fsverity.txt` exists in the evidence directory and is empty: no boot logged a
`FILE CORRUPTED` line.

## What the runs show

- **The prediction holds in every observed `stage`.** With bootc 1.17.1 in N,
  the `stage` count is 0 in both recorded stage boots (`pcl2l`, `vxlsb`), the
  same as the plain boot of N in the same run (0, 0). In `rs6sh` (bootc 1.16.8
  in N, the build before this N, same N+1) `stage` was 2. Three runs are a
  small sample: the boot noise of Sundog varies per install (0, 3 and 0 at
  `installed-n` here; 3 in `rs6sh`).
- **Boot noise is per file.** The only lines of the 3 runs come from one file
  in one N boot (`52r5s`). A later run with lines in `stage` can now be matched
  by inode to the boot lines of the same install.
- **Lane, not image: a Sundog boot can stop at about 9 s and never report.**
  2 of 11 started boots (in `pcl2l` and `52r5s`). The lifecycle lane has no
  kmsg path and a blocked `ttyS0` write can hold the guest unit, so each hit
  costs the full phase window. Follow-up: [lab#177](https://github.com/frostyard/lab/issues/177).
- No run published anything or moved a tag. `qa-controlled` stays at N+1.
  The `lab-dev` lease (T676) was released after `vxlsb`.

## References

- Implements: [quality.md — kept kernel lines](../quality.md#kept-kernel-lines-fsveritytxt-lab174)
- Precedents: [Sundog](2026-10-30-sundog-bootc-lifecycle-run.md),
  [Floe](2026-10-28-floe-bootc-lifecycle-run.md),
  [Sundog fresh-install rerun](2026-10-29-sundog-fresh-install-rerun.md)
- Decisions: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md),
  [ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md)
