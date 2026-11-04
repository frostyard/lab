# Plan: First Snowfield bootc N → N+1 update and rollback run

This plan prepares one
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
run for **Snowfield**, the GNOME desktop image for Microsoft Surface hardware.
It uses the `linux-surface` kernel. A Firn-installed Snowfield image N stages
N+1 with the shipped updater (`/usr/libexec/bootc-update-stage`), boots, rolls
back and boots N again. The lane takes the product from the manifest field
`product` ([ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)).
Snowfield is one entry in `PRODUCTS` plus this run plan. It uses the same
updater, signature policy rule, cosign key and Firn installer ISO as Snow,
Floe and Sundog. The lane stays advisory and not a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
[ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)). The field contract
is in the [README](../../README.md#manual-snow-bootc-lifecycle). Its first dev
run [ended `BLOCKED` in the first installed boot](#result-2026-11-04-dev-run-blocked-lockdown):
the Snowfield kernel runs with Secure Boot on and kernel lockdown `none`
([snosi#1213](https://github.com/frostyard/snosi/issues/1213)).

## Selected inputs

The manifest is
[`manifest.json`](snowfield-bootc-lifecycle-run-2026-11-03/manifest.json)
(file SHA-256 `2285f37a…856c034b8`; canonical lane `manifest_sha256`
`d137680f…0ae2f516`). Every value is public. N, N+1 and the target were selected on 2026-11-04
at about 01:45Z (N+1 was `latest` then); the ISO fields were refreshed to the
only entry of the signed index just before the preflight at 02:03Z. N is from
the same snosi build run as the Sundog N.

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261103153531_x86-64.iso`, SHA-256 `801752ac…7fa3db5b` | The only entry in the signed `SHA256SUMS` index at preflight (Firn 0.11.0). Its Firn catalog has one `snowfield` bootc entry. The index rotates with each ISO build: refresh the ISO fields before a run. |
| N | `20261026191603`, `sha256:b40995ef197bfccf34d8c205bf633fa56262bd145db3dad4581a7e2c4665dea4` | Bootc assembly `bootc-1.16.8-storage-digest-v1`, the same snosi build run as the Sundog N. |
| N+1 | `20261103152032`, `sha256:88e8136f79bed1271913ffe2cc5fba363bd818bf7d764992a4785fbd555e8ab3` | Bootc assembly `bootc-1.17.1-storage-digest-v1`, `latest` at selection. |
| Target | `ghcr.io/frostyard/snowfield:qa-controlled` | Operator-owned. Brian created it at N+1 on 2026-11-03 (desk ask T791a). |
| Trust pins | snosi `39cf1988`, index key `977fe2de…0fc97`, cosign key `3e797504…14fc5`, MOK cert `0446e478…545b4`, fingerprint `F37282A35…8216FD68` | The lane's pinned values, copied from the newest Sundog manifest. Snowfield images use the shipped `sigstoreSigned` rule for `ghcr.io/frostyard/snowfield`. |

The guest probe has no desktop check. Snowfield runs the same five attested
boots as Snow, Floe and Sundog. Its `/dev/console` is the graphical `tty0`.
The host reads the record from `ttyS0` and joins kmsg chunks under
[ADR-0014](../adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md).

## Known causes of a non-PASS result

- **btrfs fs-verity on the linux-surface kernel ([snosi#1130](https://github.com/frostyard/snosi/issues/1130)).**
  The linux-surface kernel used by Snowfield is **NOT** on the lane's
  `known_defect_kernels` list. A positive `fsverity_errors` count on any kernel
  not on that list ends `FAILED: fsverity_errors` after all five boots. This is
  a valid finding. It is not a lane defect. Do not change `known_defect_kernels`
  or fs-verity logic to make the run pass.
- **Index rotation.** `iso_index_entry` fails when a newer ISO replaces the
  selected index entry. Refresh the ISO fields at preflight. Do not change N or
  N+1.
- **Tag drift.** A move of `ghcr.io/frostyard/snowfield:qa-controlled` during
  the run fails it (`tag_drift`).
- **Hardware qualification.** Snowfield is untested on representative Surface
  hardware in snosi. This VM run proves only the bounded Snowfield N/N+1
  lifecycle path. It proves nothing about Surface hardware, graphical hardware
  behavior or a Microsoft Surface deployment.

## Phase 1 — The lane accepts Snowfield (this lab PR)

- `PRODUCTS` in `qa.py` has `"snowfield": "ghcr.io/frostyard/snowfield"`.
  No other lane logic changes. Text that names accepted products names
  Snowfield too. `tests/test_snowfield_bootc_lifecycle.py` covers the
  Snowfield contract. Tests that used Snowfield as a rejected product use
  `cayo` instead.
- **Done when:** the PR is merged and Argo CD `frostyard-lab` is `Synced` at
  that commit, or the dev run in Phase 3 uses the branch lane.

## Phase 2 — Preflight (read-only, operator)

[`scripts/snow-lifecycle-preflight/run.sh`](../../scripts/snow-lifecycle-preflight/run.sh)
runs the read-only checks with the catalog entry `snowfield`. Refresh the ISO
fields before this step. While `qa-controlled` does not resolve to N+1, only
`target_ref` is deferred.

- **Done when:** the [record](snowfield-bootc-lifecycle-run-2026-11-03/preflight-record.txt)
  shows every check `PASS`, `lane_preflight PASS full qa.py preflight` and
  `failures=0`. Met at 2026-11-04T02:03:23Z from this branch's lane code: 29
  checks `PASS` (2 `INFO`), both images signed, both assembly labels accepted,
  `iso_catalog` on the `snowfield` entry, and `target_ref PASS` (the tag
  already points at N+1).

## Phase 3 — Dev run from this branch

With `ghcr.io/frostyard/snowfield:qa-controlled` at N+1, submit
one dev run from the unmerged branch with
[`scripts/snow-lifecycle-dev-submit.sh`](../../scripts/snow-lifecycle-dev-submit.sh).
The workflow name uses `dev-snow-bootc-lifecycle-*` and the
`snosi.io/trigger=dev` label. Keep `keep-vm-on-failure` at `false`. Hold the
tag unchanged until the workflow ends.

- **Done when:** the workflow ends and its `result` and `checks` outputs exist.

## Phase 4 — Record the result

- Add the workflow name, `result`, `checks` and the canonical
  `manifest_sha256` here. Record a `FAILED` or `BLOCKED` result with its code.

### Result 2026-11-04: dev run BLOCKED (lockdown)

`dev-snow-bootc-lifecycle-pnljz`, from this branch at `8404d7d6` (label
`snosi.io/trigger=dev`, no publish, no tag move), 2026-11-04 02:07:51Z to
02:16:31Z, under the `lab-dev` lease (thread T791). Canonical
`manifest_sha256` `d137680f60647366e2a2a1a219a7cddf6b2a738ff524ce911eac631d0ae2f516`.

```
BLOCKED: guest_probe_error_observed
```

`checks.txt`:

```
manifest_sha256=d137680f60647366e2a2a1a219a7cddf6b2a738ff524ce911eac631d0ae2f516
tag=before-install timestamp=2026-11-04T02:09:12.636928+00:00
tag=before-installed-n timestamp=2026-11-04T02:14:50.017148+00:00
```

`fsverity.txt` exists and is empty: no phase record arrived, so no phase kept
a line.

- Firn installed N, and N booted with Secure Boot (`Secure boot enabled`) on
  kernel `7.2.12-1-surface`. The guest probe of `installed-n` wrote
  `SNOW_QA_ERR lockdown` (to ttyS0 and as a kmsg chunk, ADR-0014) at about
  64 s: `/sys/kernel/security/lockdown` was neither `[integrity]` nor
  `[confidentiality]`. The kernel command line was
  `rw composefs=?… console=uart,io,0x3f8 console=tty0`, with no
  `lockdown=integrity`, and the serial log has no "Kernel is locked down"
  line.
- This is a product finding, not a lane defect:
  [snosi#1213](https://github.com/frostyard/snosi/issues/1213). The
  linux-surface kernel does not raise lockdown in Secure Boot mode, and the
  sealed UKI command line does not carry the `lockdown=integrity` karg. The
  Debian kernels of Snow, Floe and Sundog raise it by themselves.
- The lane reports a guest probe error as `BLOCKED` (unproven, ADR-0003); the
  gate is not relaxed. No later phase ran, so stage, N+1, rollback and
  fs-verity are unproven for Snowfield. Rerun after snosi#1213 is fixed, with
  a new N and N+1 that carry the fix.
- The VM is gone (`keep-vm-on-failure=false`); the evidence directory on
  minideb keeps `checks.txt`, `checks.json`, `fsverity.txt`,
  `preflight.json`, `result-summary.txt` and `serial-redacted.log`. The lease
  is released.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Decisions: [ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md),
  [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md),
  [ADR-0014](../adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
- Precedents: [Snow](2026-10-26-snow-bootc-lifecycle-run.md),
  [Floe](2026-10-28-floe-bootc-lifecycle-run.md),
  [Sundog](2026-10-30-sundog-bootc-lifecycle-run.md)
