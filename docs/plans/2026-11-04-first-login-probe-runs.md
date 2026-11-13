# Plan: First-login probe on today's images (T843 t2)

**Status:** Done 2026-11-04 (recorded 2026-11-13). The lane mode merged in
[lab#182](https://github.com/frostyard/lab/pull/182), and Phase 1 is met: the dev run of
each of the 4 products passed ([lab#184](https://github.com/frostyard/lab/pull/184)).
The probe stays report-only
([ADR-0016](../adr/0016-first-login-probe-is-a-report-only-lane-mode.md)); a
gate is a separate decision.

This plan proves the report-only
[first-login probe](../adr/0016-first-login-probe-is-a-report-only-lane-mode.md)
of the [bootc lifecycle lane](../specs/bootc-lifecycle-lane.md) on
the 4 products before the first-login trigger moves from first-setup to the
chairlift first run (plan T843, task t3 in snosi). The expected result on
today's images: first-setup once on Snow and Snowfield, nothing on Sundog
(KDE, no first-login app) and no first-run entry on Floe (headless). The probe
records; it gates nothing until t3 promotes it.

## Selected inputs

One manifest per product in
[`first-login-probe-2026-11-04/`](first-login-probe-2026-11-04/), each with
`first_login_probe` `{"session_wait": 600, "app_wait": 180}`. N is the
`latest` image of 2026-11-04 (about 19:05Z build). N+1 is the image that
`<product>:qa-controlled` pointed at; the probe never stages it
(`version_distinct`, N is newer). Every image carries the bootc assembly
`bootc-1.17.1-storage-digest-v1`. Trust pins are the lane's (snosi
`39cf1988`).

| Product | N | N+1 (qa-controlled) | ISO |
| --- | --- | --- | --- |
| Snow | `20261104190452` `sha256:f2f103b4…` | `20261026133822` `sha256:008bfa69…` | `20261104190155` |
| Snowfield | `20261104190408` `sha256:e7b09eb5…` | `20261103152032` `sha256:88e8136f…` | `20261104190155` |
| Sundog | `20261104190733` `sha256:f2b2c9dd…` | `20261030194311` `sha256:199ca989…` | `20261104213621` |
| Floe | `20261104190417` `sha256:c671e8d9…` | `20261028230726` `sha256:bb1269a5…` | `20261104213621` |

The Sundog and Floe ISO fields were refreshed after their first submit ended
`FAILED: preflight_mismatch:iso_index` (the signed index rotated to a new
installer ISO, a known cause in the earlier run plans). N and N+1 did not change.

## Phase 1 — Dev runs from the PR branch

Submitted with
[`scripts/snow-lifecycle-dev-submit.sh`](../../scripts/snow-lifecycle-dev-submit.sh)
(`dev-snow-bootc-lifecycle-*`, label `snosi.io/trigger=dev`,
`keep-vm-on-failure=false`) under the `lab-dev` lease (thread T843). No
publish, no tag move.

- **Done when:** each product has one dev run whose `first-login` output shows
  the expected summary. Met on 2026-11-04 (results below).

### Result 2026-11-04: first runs at `46a0565e` FAILED (unit_size)

`dev-snow-bootc-lifecycle-9hvgj` (Snow) and `-nqzng` (Snowfield) ended
`FAILED: unit_size` at `login-1`: the larger `qa.py` pushed the guest unit
credential over 60000 characters, for every lane mode. Fixed in `56d24225`
(the payload is `ast.unparse` of the source, AST-checked; guard test
`test_guest_unit_stays_under_the_smbios_bound`). The queued Sundog and Floe
runs were deleted before they started.

### Result 2026-11-04: Snow PASS, first-setup once

`dev-snow-bootc-lifecycle-n7f7s` at `56d24225`, submitted 21:04Z, ended 21:25Z.

```
PASS: verified_first_login_probe;first_login=first-setup-once
phase=login-1 dm=gdm autologin=yes session=wayland started=first-setup entries=org.frostyard.FirstSetup.autostart.desktop mark_done=first-setup:0 markers=first-setup waited=180
phase=login-2 dm=gdm autologin=yes session=wayland started=none entries=org.frostyard.FirstSetup.autostart.desktop mark_done=none markers=first-setup waited=180
```

### Result 2026-11-04: Snowfield PASS, first-setup once

`dev-snow-bootc-lifecycle-72cjn` at `56d24225`, submitted 21:04Z (queued for the semaphore), ended 21:42Z. No
installed-n attestation in this mode, so snosi#1213 (lockdown) does not block
it.

```
PASS: verified_first_login_probe;first_login=first-setup-once
phase=login-1 dm=gdm autologin=yes session=wayland started=first-setup entries=org.frostyard.FirstSetup.autostart.desktop mark_done=first-setup:0 markers=first-setup waited=180
phase=login-2 dm=gdm autologin=yes session=wayland started=none entries=org.frostyard.FirstSetup.autostart.desktop mark_done=none markers=first-setup waited=180
```

### Result 2026-11-04: Sundog PASS, no first-login app

`dev-snow-bootc-lifecycle-cb9qj` at `25dab840`, 22:02Z to 22:18Z. SDDM logged
the user in to a Plasma Wayland session in both boots; nothing first-run
started in the 180 s window, and no first-run autostart entry exists.

```
PASS: verified_first_login_probe;first_login=none
phase=login-1 dm=sddm autologin=yes session=wayland started=none entries=none mark_done=none markers=none waited=180
phase=login-2 dm=sddm autologin=yes session=wayland started=none entries=none mark_done=none markers=none waited=180
```

### Result 2026-11-04: Floe PASS, headless with no first-run entry

`dev-snow-bootc-lifecycle-w5n6r` at `25dab840`, 22:02Z to 22:25Z. One boot:
no display manager, so no account and no session; no first-run autostart
entry, no first-setup or chairlift binary.

```
PASS: verified_first_login_probe;first_login=headless-no-entry
phase=login-1 dm=none autologin=no session=none started=none entries=none mark_done=none markers=none waited=0
```

All 4 products show the expected result on today's images: first-setup once
on Snow and Snowfield, nothing on Sundog and Floe. The VMs are gone
(`keep-vm-on-failure=false`); the evidence directories on minideb keep
`checks.txt`, `first-login.txt`, `login-1.json`, `login-2.json` (not Floe) and
`result-summary.txt`. The `lab-dev` lease is released.

## Later / ideas

- Task t3 (snosi): dev runs on the swap PR's images should show
  `chairlift-once` on Snow and Snowfield, plus the upgrade case from a
  first-setup image whose marker exists. Then the probe gates after 3 green
  main runs.

## References

- Implements: plan T843 section 7 (lab proof), task t2;
  [ADR-0016](../adr/0016-first-login-probe-is-a-report-only-lane-mode.md)
- Decisions: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md),
  [ADR-0014](../adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
