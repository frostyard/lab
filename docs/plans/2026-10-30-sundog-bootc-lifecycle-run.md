# Plan: First Sundog bootc N → N+1 update and rollback run

This plan prepares one
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
run for **Sundog**, the KDE Plasma desktop image: a Firn-installed Sundog image
N, staged to N+1 by the shipped updater (`/usr/libexec/bootc-update-stage`),
booted, rolled back and booted again as N. The lane takes the product from the
manifest field `product`
([ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)):
Sundog is one entry in `PRODUCTS` plus this run plan. It uses the same updater,
signature policy rule, cosign key and Firn installer ISO as Snow and Floe.
Everything else is the lane that passed for Snow in the
[Snow run plan](2026-10-26-snow-bootc-lifecycle-run.md) and for Floe in the
[Floe run plan](2026-10-28-floe-bootc-lifecycle-run.md). The lane stays
advisory and not a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
[ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)). The field contract is
in the [README](../../README.md#manual-snow-bootc-lifecycle). Its first dev run
[passed](#result-2026-10-30-dev-run-pass).

## Selected inputs

The manifest is [`manifest.json`](sundog-bootc-lifecycle-run-2026-10-30/manifest.json)
(file SHA-256 `302a8d99…ed7739b`; canonical lane `manifest_sha256`
`b6999392…93d1c201`).
Every value is public. Selected on 2026-10-30 at about 23:20Z. snosi was read only.

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261030183923_x86-64.iso`, SHA-256 `f5fce13c…00e7f2` | The entry in the signed `SHA256SUMS` index at selection. Its Firn catalog has one `sundog` bootc entry. The index rotates with each ISO build, so refresh the ISO fields before a run (same procedure as the Floe plan). |
| N | `20261026191618`, `sha256:87c02c93…795795` | The last Sundog build before bootc 1.17.1: snosi `b3d14e2c`, `bootc-1.16.8-storage-digest-v1`, the same snosi commit as the Floe N. Installed Sundog hosts from earlier media run this assembly. |
| N+1 | `20261030194311`, `sha256:199ca989…65da4` | `bootc-1.17.1-storage-digest-v1`, snosi `827beb1a`, `latest` at selection. |
| Target | `ghcr.io/frostyard/sundog:qa-controlled` | Operator-owned. It does not exist yet. Brian must create it at N+1 before the run. |
| Trust pins | snosi `39cf1988`, index key `977fe2de…0fc97`, cosign key `3e797504…14fc5`, MOK cert `0446e478…545b4`, fingerprint `F37282A3…8216FD68` | The lane's pinned values, the same as for Snow and Floe. Sundog images are signed with the same cosign key, and the shipped signature policy names `ghcr.io/frostyard/sundog` with the same `sigstoreSigned` rule. |

The guest probe has no desktop check for any product, so Sundog runs the same
five attested boots as Snow. On Sundog, as on Snow, `/dev/console` is the
graphical `tty0`; the host reads the record from `ttyS0`.

## Known causes of a non-PASS result

- **btrfs fs-verity on kernel 7.2 ([snosi#1130](https://github.com/frostyard/snosi/issues/1130)).**
  As for Floe: on a listed Debian kernel the lane records the count
  (`fsverity_known_defect=<kernel>`) and does not fail; expect counts above 0,
  mostly in the `stage` boot (N's updater on bootc 1.16.8 reads every composefs
  object of N+1 in full). A kernel **not** on the list with a count above 0
  ends `FAILED: fsverity_errors` after all five boots. Do not edit the list to
  make a run pass. A PASS says nothing about the resolution of #1130.
- **Index rotation.** `iso_index_entry` fails as soon as a newer ISO replaces
  the selected one. Refresh the ISO fields; do not change N or N+1.
- **Tag drift.** Any move of `ghcr.io/frostyard/sundog:qa-controlled` during
  the run fails it (`tag_drift`).
- **Intermittent Sundog `ttyS0` stall at switch-root.** The Firn lane saw
  Sundog userspace stall on `ttyS0` at switch-root (lab#168,
  [ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md),
  [Sundog fresh-install rerun](2026-10-29-sundog-fresh-install-rerun.md)). This
  lifecycle lane reads its records from `ttyS0` only and has no kmsg path, so a
  stall there shows as a missing record for a phase, not as a product failure.
  Treat a missing record on Sundog as unproven, check the evidence, and rerun
  before you blame the image.

## Phase 1 — The lane accepts Sundog (this lab PR)

- `PRODUCTS` in `qa.py` has `"sundog"`; no other lane logic changes. Text that
  named Snow and Floe now names Sundog. `tests/test_sundog_bootc_lifecycle.py`
  covers the Sundog contract; the tests that used `sundog` as a rejected product
  now use `snowfield`.
- **Done when:** the PR is merged and Argo CD `frostyard-lab` is `Synced` at
  that commit, or the dev run in Phase 3 uses the branch lane.

## Phase 2 — Preflight (read-only, operator)

[`scripts/snow-lifecycle-preflight/run.sh`](../../scripts/snow-lifecycle-preflight/run.sh)
runs the same checks as for Snow and Floe, with the catalog entry `sundog`.
While `qa-controlled` does not resolve to N+1, only `target_ref` is deferred.

- **Done when:** the [record](sundog-bootc-lifecycle-run-2026-10-30/preflight-record.txt)
  shows every check `PASS`, `target_ref PENDING`, `lane_preflight PASS` and
  `failures=0`. Met at 2026-10-30T23:26:53Z (ISO `20261030183923`, from this
  branch's lane code): both images signed, both assembly labels accepted,
  `iso_catalog` on the `sundog` entry. Run it again just before the submit,
  after the tag exists: expect `target_ref PASS` and `lane_preflight PASS full
  qa.py preflight`.

## Phase 3 — Dev run from this branch

After Brian creates `ghcr.io/frostyard/sundog:qa-controlled` at N+1 (a
publication that stays with Brian), submit a dev run from the unmerged branch
with
[`scripts/snow-lifecycle-dev-submit.sh`](../../scripts/snow-lifecycle-dev-submit.sh)
(workflow `dev-snow-bootc-lifecycle-*`, label `snosi.io/trigger=dev`). A dev run
does not publish, does not move a tag and does not change the main
WorkflowTemplate or ConfigMap (standing permission T551a). Keep
`keep-vm-on-failure` at `false` and hold the tag still until the workflow ends.

- **Done when:** the workflow ends and its `result` and `checks` outputs exist.

## Phase 4 — Record the result

- Add the workflow name, `result`, `checks` and the canonical `manifest_sha256`
  here. A `FAILED` or `BLOCKED` result is evidence too: record the code and fix
  forward.

### Result (2026-10-30, dev run): PASS

`dev-snow-bootc-lifecycle-rs6sh` (dev run from this branch at lab
`d88fa83f`, label `snosi.io/trigger=dev`; submitted 23:33:12Z, ended
23:48:30Z), canonical `manifest_sha256`
`b6999392e1e7b70445c4145f96471a0216e78b41b104fd4030f1838993d1c201`:

```
PASS: verified_five_fresh_boots;fsverity_known_defect=7.2.12+deb14-amd64
```

| Phase | Boot ID | Kernel | fs-verity errors |
| --- | --- | --- | --- |
| `installed-n` | `a3d7609a-0957-48c2-93f9-6e8ae1dbd5f2` | `7.2.12+deb14-amd64` | 3 |
| `stage` | `cfd323de-e142-4001-a76c-a702333a23f4` | `7.2.12+deb14-amd64` | 2 |
| `boot-n-plus-1` | `2db3f367-37da-4e4b-9b34-f3a3f8fe7d10` | `7.2.12+deb14-amd64` | 0 |
| `rollback` | `de1eae63-51c2-43f9-af3f-a2e671f0dfb3` | `7.2.12+deb14-amd64` | 1 |
| `boot-n` | `25c2184e-a317-4d6a-8d7a-0d55fd045ec6` | `7.2.12+deb14-amd64` | 1 |

- **Before submit.** Brian created `ghcr.io/frostyard/sundog:qa-controlled`
  at N+1 (desk ask T589a). The final preflight, 23:31:32Z to 23:33:01Z, gave
  `target_ref PASS` (`sha256:199ca989…65da4`), `lane_preflight PASS full qa.py
  preflight` and `failures=0`; the signed index still held ISO
  `20261030183923`. Submitted with `scripts/snow-lifecycle-dev-submit.sh`
  (`SUBMIT` through `ssh minideb sudo -n k3s kubectl create`) under the
  `lab-dev` lease (thread T589). It published nothing and moved no tag.
- **fs-verity.** 7 errors over the five boots, all on `7.2.12+deb14-amd64`,
  which is on the known-defect list
  ([snosi#1130](https://github.com/frostyard/snosi/issues/1130)), so the lane
  recorded them and did not fail. Unlike Floe (1 error, `stage` only), Sundog
  also logged errors in the fresh `installed-n` boot and in both N boots after
  rollback. The cause of that spread is not checked; the larger desktop image
  most likely reads more large files at boot. This PASS
  says nothing about the resolution of #1130.
- **Serial and memory.** Every phase record arrived on `ttyS0`: the Sundog
  switch-root stall seen in the Firn lane (lab#168) did not occur in these
  five boots. The 8 GiB VM finished each phase without a timeout.
- **What it shows.** A Firn-installed Sundog host on bootc 1.16.8 takes the
  signed update to the bootc 1.17.1 image, boots it, rolls back and boots N
  again, with Secure Boot, lockdown, the measured UKI and the TPM-unlocked
  LUKS root attested on each of the five boots. It is a dev run of the branch
  lane; the lane stays advisory and the main lane has no Sundog run yet.
- `qa-controlled` stays at N+1 (do not delete it in the GitHub package UI:
  that deletes N+1 by digest).

- **Done when:** the result is recorded here.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Decisions: [ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md),
  [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md),
  [ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md)
- Precedents: [Snow](2026-10-26-snow-bootc-lifecycle-run.md),
  [Floe](2026-10-28-floe-bootc-lifecycle-run.md)
