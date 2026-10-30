# Plan: incus, coder and nix sysext activation on an installed Snow (lab#142)

This plan records the fresh-host check that
[lab#142](https://github.com/frostyard/lab/issues/142) asks for: snosi
[#1114](https://github.com/frostyard/snosi/pull/1114) changed how the
`coder`, `incus` and `nix` sysexts start (each `multi-user.target` drop-in
`Upholds=` only units that stay active; snosi#1100 was a start loop of
93,566 journal lines). It reuses the `sysext_probe` mode of the
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
lane ([first plan](2026-10-28-snow-sysext-probe-run.md), field contract in the
[README](../../README.md#manual-snow-bootc-lifecycle)) as a dev run from an
unmerged branch (`dev-snow-bootc-lifecycle-*`, the 2026-10-26 lab exception
T237f: no publish, no tag move, no change to the main WorkflowTemplate or
ConfigMap). The probe is advisory and never a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md)).

## Phase 1 — `.mount` units in the probe (this branch)

The probe accepted only `.service` and `.socket` units; lab#142 needs
`nix.mount`. The `units` rule now also accepts `.mount`. A `.mount` has no
`NRestarts`, like a `.socket`, so the 30 s settle window (lab#165) checks it
by `ActiveState` at both samples and an unchanged, non-zero
`ActiveEnterTimestampMonotonic`. `first_start` stays `.service` only. No other
lane behaviour changed.

- **Done when:** the validator and the guest tests accept an active, settled
  `nix.mount` and fail an inactive or re-entered one. Met (tests in
  `tests/test_snow_bootc_lifecycle.py`, `lab#142` comments).

## Selected inputs

The manifest is [`manifest.json`](snow-sysext-probe-2026-10-29/manifest.json).

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261029193442_x86-64.iso`, SHA-256 `0b476b48…451993` | The single entry of the signed index at submit time (`VALIDSIG F37282A3…16FD68`). |
| N | `20261029193641`, `sha256:73a0c907…a93300` | `snow:latest` at submit time; built after snosi#1114. |
| N+1 | `20261026133822`, `sha256:008bfa69…2192aa` | The current target of `qa-controlled`. Not staged in this mode; the tag was not moved. |
| Keys | `index_key_sha256`, `cosign_key_sha256`, `mok_cert_sha256` | Recomputed from snosi `39cf1988` (the pinned commit); all three match. |
| Probe | features `incus`, `coder`, `nix`; no first-start entries | lab#142. |

Probed units (lab#142, "must be active after merge"):

| Feature | Units |
| --- | --- |
| incus | `incus-sysext-setup.service`, `incus.socket`, `incus-user.socket`, `incus-startup.service`, `incus-lxcfs.service` |
| coder | `coder-sysext-setup.service` |
| nix | `nix.mount`, `nix-sysext-setup.service`, `nix-daemon.socket` |

`coder.service` is not probed: it is skipped by design until
`/etc/coder.d/coder.env` holds a usable configuration (a non-empty
`CODER_ACCESS_URL` and a bind address that its `ExecCondition=` helper
`/usr/lib/coder-sysext/coder-configured` accepts), and
`coder-sysext-setup.service` only `Wants=` it.

Sysext versions in the repository at submit time (`/ext/<name>/SHA256SUMS`,
newest): `incus_1+7.5.1-debian13-202610281111+r5`, `coder_2.37.8-1+r3`,
`nix_2.26.3+dfsg-1+r5`. All are at or after the revisions lab#142 names
(incus r5, coder r1, nix r3). The run records the file each
`/var/lib/extensions/<feature>.raw` resolves to.

## Phase 2 — Dev run

- Submit the manifest with `scripts/snow-lifecycle-dev-submit.sh` from this
  branch, under the `lab` lease. No publish, no tag move, no change to the
  main WorkflowTemplate or ConfigMap.
- **Done when:** the run reports each probed unit, in both sysext boots, with
  evidence, and every failure is either a scoped snosi issue or a named
  environment failure. Met by `dev-snow-bootc-lifecycle-c88m4` (below):
  [snosi#1189](https://github.com/frostyard/snosi/issues/1189).

## Result (2026-10-29): FAILED in the enable boot, PASS after the reboot

`dev-snow-bootc-lifecycle-c88m4` (branch head `1af0ac92`, submitted 23:48Z,
ended 00:04Z). The VM was deleted by the lane's teardown; the probe mode
never stages N+1 and `qa-controlled` was not moved.

```
FAILED: sysext_failed:unit_inactive:<each of the 9 units>,unit_restarted:<each of the 9 units>
```

The `unit_restarted` codes follow from `ActiveEnterTimestampMonotonic=0`
(the units never entered `active`), not from a restart. The `sysext-reboot`
record has `failures: []`. `installed-n` recorded the known fs-verity defect
of kernel `7.2.12+deb14-amd64` (snosi#1130), not failed.

| Check | `sysext-enable` (after `updex features enable … --now`) | `sysext-reboot` |
| --- | --- | --- |
| `updex features enable … --now` | rc 0 for `incus`, `coder`, `nix`; downloaded and refreshed | — |
| Merged on `/usr`, link | `coder`, `incus`, `nix`; `incus_1+7.5.1-debian13-202610281111+r5`, `coder_2.37.8-1+r3`, `nix_2.26.3+dfsg-1+r5` | same |
| sysusers | present (`incus` 979, `coder` 976, `nixbld1`…`nixbld10`; groups `incus-admin`, `nix-users`, `nixbld`) | present |
| 9 probed units | **inactive (dead)**, `ActiveEnterTimestampMonotonic=0`, `NRestarts=0`, `journalctl -b -u` has **no entries** | all active, 0 restarts, same `ActiveEnterTimestampMonotonic` across the 30 s window |

Unit states after the reboot (second sample of the settle window):

| Unit | State | `NRestarts` |
| --- | --- | --- |
| `incus-sysext-setup.service` | active (exited) | 0 |
| `incus.socket` | active (listening) | — |
| `incus-user.socket` | active (listening) | — |
| `incus-startup.service` | active (exited) | 0 |
| `incus-lxcfs.service` | active (running) | 0 |
| `coder-sysext-setup.service` | active (exited) | 0 |
| `nix.mount` | active (mounted) | — |
| `nix-sysext-setup.service` | active (exited) | 0 |
| `nix-daemon.socket` | active (running) | — |

**After the reboot, lab#142's check passes for the settle window:** all 9
units are active at both samples, with an unchanged, non-zero
`ActiveEnterTimestampMonotonic` across the 30 s window, and the 5 services
have `NRestarts=0`. So no unit re-entered `active` in the window: no sign of
the snosi#1100 start loop there. The probe keeps no journal for a passing
unit, so this run does not prove exactly 1 start over the whole boot.

**In the enable boot, PID 1 never starts the units.** The probable cause,
not proven by this run: the suite sysexts ship an `extension-release` file
with `EXTENSION_RELOAD_MANAGER=1`; `incus`, `coder` and `nix` do not, so the
live refresh does not reload PID 1, and the new unit files and `Upholds=`
drop-ins load only at the next boot (`reload-sysext.service`). The probe does
not record `LoadState`. Filed as
[snosi#1189](https://github.com/frostyard/snosi/issues/1189). This is a
product finding for the live enable path, not a lane defect.

**Not an environment failure.** The guest is a full QEMU VM on the lab Incus
host (no nested container: `incus-lxcfs.service` with
`ConditionVirtualization=!container` runs after the reboot), and the same VM
passes after the reboot.

This proves nothing about `coder.service` (skipped by design), running Incus
instances or Nix builds, other sysexts with `Upholds=` drop-ins, Snowfield
hardware or release readiness.

## Open questions

- **Live enable of a non-suite sysext:** must `incus`, `coder` and `nix` set
  `EXTENSION_RELOAD_MANAGER=1`, or must the docs say that a first enable needs
  a reboot? Decide in [snosi#1189](https://github.com/frostyard/snosi/issues/1189),
  then rerun this manifest.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle)
- Issue: [lab#142](https://github.com/frostyard/lab/issues/142)
- Decisions: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)
- snosi: [#1114](https://github.com/frostyard/snosi/pull/1114),
  [#1100](https://github.com/frostyard/snosi/issues/1100),
  `shared/sysext/finalize/sysext-upholds-guard.sh` (snosi#1113)
