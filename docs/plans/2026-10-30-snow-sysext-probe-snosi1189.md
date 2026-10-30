# Plan: why a sysext unit does not start after a live `updex features enable --now` (snosi#1189)

This plan records the evidence that
[snosi#1189](https://github.com/frostyard/snosi/issues/1189) needs. In
`dev-snow-bootc-lifecycle-c88m4` ([plan](2026-10-29-snow-sysext-probe-lab142.md))
9 units of `incus`, `coder` and `nix` were inactive, with no journal, after
`updex features enable … --now`, and active after a reboot. The probable cause
is that PID 1 did not reload, so the new unit files and the `multi-user.target`
`Upholds=` drop-ins were not loaded. The probe recorded no `LoadState`. It reuses
the `sysext_probe` mode of the
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
lane (field contract in the
[README](../../README.md#manual-snow-bootc-lifecycle)) as a dev run from an
unmerged branch (`dev-snow-bootc-lifecycle-*`, the 2026-10-26 lab exception
T237f: no publish, no tag move, no change to the main WorkflowTemplate or
ConfigMap). The probe is advisory and never a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md)).

## Phase 1 — Load-state evidence in the record (this branch)

- Each probed unit records `LoadState` and `NeedDaemonReload` from the last
  settle sample. The record has a new field `target` with `LoadState`,
  `NeedDaemonReload` and `Upholds` of `multi-user.target`, sampled right after
  the settle wait. `systemctl show` can load a unit file on demand, so
  `LoadState` alone can read `loaded`; `NeedDaemonReload=yes` on the target and
  its `Upholds` list show directly whether PID 1 loaded the drop-ins.
- Evidence only: no new failure code, no verdict change, and the host does not
  compare the new fields between the enable and reboot records. The host checks
  their shape (exact keys, text or `null`, bounded).
- The pod log prints `sysext_state` (per probed unit) and `sysext_target` lines
  for each boot, also when the run passes.
- **Done when:** the guest and host tests accept the new fields, reject a
  missing or wrongly typed one, and show that `NeedDaemonReload=yes` changes no
  verdict. Met (tests in `tests/test_snow_bootc_lifecycle.py`, snosi#1189
  comments).

## Phase 2 — Two dev runs

Both runs use the inputs of
[`snow-sysext-probe-2026-10-29/manifest.json`](snow-sysext-probe-2026-10-29/manifest.json)
(N, N+1, keys), so run A repeats `c88m4` with the same images. Only the ISO
changed: `snosi-installer_20261029193442` left the signed index, so both
manifests take its single entry at submit time,
`snosi-installer_20261030103127_x86-64.iso` (SHA-256 `8fa43330…67ed57`). N is
still `20261029193641` (`sha256:73a0c907…a93300`) and `qa-controlled` still
points at N+1 `20261026133822` (`sha256:008bfa69…2192aa`), checked 2026-10-30. The manifests are in
[`snow-sysext-probe-2026-10-30/`](snow-sysext-probe-2026-10-30/).

| Run | Manifest | Features | Units |
| --- | --- | --- | --- |
| A (control) | [`manifest-control.json`](snow-sysext-probe-2026-10-30/manifest-control.json) | `incus`, `coder`, `nix` | the 9 units of lab#142 |
| B (reload) | [`manifest-reload.json`](snow-sysext-probe-2026-10-30/manifest-reload.json) | `lodge`, `incus`, `coder`, `nix` | the 9 units and `lodge.service` |

- Run A repeats `c88m4` with the new fields. Expected: the 9 units inactive in
  the enable boot; the record shows whether `NeedDaemonReload` is `yes` on the
  target and whether its `Upholds` lack the 9 units.
- Run B enables `lodge` first. Its `extension-release` has
  `EXTENSION_RELOAD_MANAGER=1`, so that flag is merged when the next features
  refresh, and PID 1 may reload. Expected: if that is the mechanism, the 9 units
  start in the enable boot, and the target shows `Upholds` with them.
- Submit each with `scripts/snow-lifecycle-dev-submit.sh`, under the `lab-dev` lease (thread T558), one run after the other.
- **Done when:** both runs report the new fields for every probed unit and for
  the target in both sysext boots, and the comparison of A and B is written in
  snosi#1189.

## Result (2026-10-30): cause shown; a merged key starts the units in the enable boot

Both runs from branch head `1c47693a`, under the `lab-dev` lease (T558). The
VMs were deleted by the lane's teardown; `qa-controlled` was not moved.

**Run A — `dev-snow-bootc-lifecycle-mm4s5` (control, 15:48Z–16:04Z):
`FAILED: sysext_failed:unit_inactive:…,unit_restarted:…` (the 9 units) in the
enable boot, no failure after the reboot.** `updex features enable … --now` rc 0
for `incus`, `coder`, `nix`; all merged and linked (`incus` r5, `coder` r3,
`nix` r5).

| Boot | `multi-user.target` | 9 probed units |
| --- | --- | --- |
| `sysext-enable` | `LoadState=loaded`, `NeedDaemonReload=yes`, `Upholds=` empty | `LoadState=loaded`, `ActiveState=inactive` (dead), `ActiveEnterTimestampMonotonic=0`, no journal entries |
| `sysext-reboot` | `NeedDaemonReload=no`, `Upholds=` the 7 upheld units | all active, 0 restarts, settled |

`LoadState=loaded` comes from `systemctl show`, which loads a unit file on
demand; the target's `NeedDaemonReload=yes` and empty `Upholds=` show that PID 1
never reloaded after the live refresh, so the `multi-user.target.d` drop-ins
were not applied.

**Run B — `dev-snow-bootc-lifecycle-4hr54` (lodge first, 16:04Z–16:17Z):
`PASS: verified_sysext_probe`.** In the enable boot the target had
`NeedDaemonReload=no` and `Upholds=` with the 7 units and `lodge.service`; all
10 units were `loaded` and active, unchanged across the settle window, with 0
restarts. Each feature's units entered `active` after its own enable (incus
about 26.9 s, coder 30.7 s, nix 33.0 s monotonic). Same after the reboot.

So a merged `EXTENSION_RELOAD_MANAGER=1` (here from `lodge`) makes the updex
refresh reload PID 1, and the units start in the same boot. snosi#1193 adds the
key to every sysext that ships system units. After its republish, rerun
`manifest-control.json`: it must then pass in the enable boot. The pod log of
a passing run is removed with its pod (`podGC: OnWorkflowSuccess`); run B's
values come from the private evidence `sysext-enable.json` and
`sysext-reboot.json` on the lab host.

## Open questions

- None for the lane. The product follow-up is the rerun after snosi#1193
  republishes the sysexts.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle)
- Issue: [snosi#1189](https://github.com/frostyard/snosi/issues/1189)
- Earlier plan: [lab#142 probe](2026-10-29-snow-sysext-probe-lab142.md)
- Decisions: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)
