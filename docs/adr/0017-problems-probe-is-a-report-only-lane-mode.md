# 0017 — The problems probe is a report-only lane mode

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

Snosi ships a problem collector (snosi ADR-0029): `/usr/bin/snosi-health` writes
`/var/lib/snosi-health/problems.json` (mode 0644, owner `root:root`) and
`details.json` (mode 0640, owner `root`, group `sudo`) from a oneshot
`snosi-health.service` that `snosi-health.timer` starts 3 minutes after boot and
hourly, and that `preset-reconcile.service` enables on every boot. It reads
systemd unit failures, a failed `bootc-update-stage` run, systemd-coredump
journal entries and app problem entries that an application sends to the system
journal. `snosi-health --list` prints one line per record for any user.

Nothing in this lane looked at that collector: the five-boot path attests booted
images, updates and rollback only, and the sysext and first-login probes answer
other questions. So "does the collector record a real problem on a real
installed VM, in the format core's report schema v1 describes" had no evidence,
and plan T857 phase 1 needs exactly that proof before anything is built on the
records.

The lane already has two optional probe modes that replace the five-boot plan
and report without gating the release. A problems check has the same shape: it
needs a real installed VM (a container has no systemd-coredump, no journal and
no `preset-reconcile`), and its outcome is evidence about the image, not a
release decision.

## Decision

The manifest may carry the optional key `problems_probe`, an object with exactly
`{"path": "fresh"}` or `{"path": "update"}`, instead of `sysext_probe` and
`first_login_probe`; the three never travel together. `fresh` boots the
installed N once (phase `problems`) and uses the `version_distinct` rule, because
nothing is staged. `update` makes the unchanged `installed-n` and `stage` boots
and then `problems` as the first boot of N+1, so its rule stays
`version_order`. Its budget is `reboot`, and `installed_boot` on the fresh path.
The update path then reboots N+1 once more (`problems-reboot`, see the amendment
below).

The `problems` boot runs the guest's `problems` mode as root, and its one guest
record is the evidence:

1. Boot timing, read before any injection: the default target, its active wait
   (at most 180 s) and the manager's `Firmware`, `Loader`, `InitRD`, `Userspace`
   and `ActiveEnterTimestampMonotonic`, recorded as `systemd-analyze` computes
   them. Any value that cannot be read makes `boot` null, and the record still
   passes: the boot time is evidence, never a check.
2. Static facts: the collector binary, the timer's `is-enabled`/`is-active`
   tokens, `preset-reconcile.service`'s `Result`, the `systemd-coredump` account,
   the persistent journal directory and the `core_pattern` pipe.
3. Four injections, each local to the throwaway QA VM:
   - one oneshot unit `snow-qa-problem-local.service` with
     `ExecStart=/bin/false`, written into the installed `/etc` (never `/usr`),
     which must be a local unit: its `FragmentPath` is below `/etc`, so
     `problems.json` keeps only `{"result": "exit-code"}` for it and marks it not
     reportable, while its full name appears in `details.json` `extra.units`;
   - `/usr/libexec/bootc-update-stage` inside `unshare --net`: a network
     namespace of its own with no route fails the pull for a network reason
     without touching the VM's own network;
   - a child killed with `SIGSEGV` with an unlimited core limit, so
     systemd-coredump writes the one journal entry the collector reads;
   - one app problem datagram in the journal's native protocol, which is the only
     way to set the fields `systemd-cat` cannot.
4. The collector runs twice, one after the other: the timer can start a run
   during the injections, and the second start is always a full run after them.
5. `problems.json` (mode, owner, size, a bounded parse, and whether the local
   unit's name leaked into it), `details.json` (mode, owner, group and the record
   id that carries the injected unit) and `snosi-health --list` as `nobody`.

The host validates the record's exact shape, identity, boot id and booted image,
keeps it as `problems.json` evidence, and reports one summary line:
`unproven-no-collector` when the image predates the collector,
`unproven-too-large` when the document was above the parse bound, `ok` when every
check passed, otherwise `missing-` plus the failed check names (`file`, `schema`,
`timer`, `coredump`, `journal`, `details`, `unit`, `stage`, `crash`, `app`, and
`list` on Floe only). The record's schema check uses a byte copy of core's
`docs/specs/schemas/problem-report/v1.json` at commit `f1b62e0`, whose sha256 a
test pins: the host container has no `jsonschema` module, so `problems.py`
validates with a compact validator for exactly that keyword subset and refuses
any keyword it does not implement.

The probe never fails the run for a product finding. A summary that starts with
`unproven-` ends `BLOCKED` with reason `problems_<summary>`
([ADR-0003](0003-unproven-is-distinct-from-failed.md)); every other summary ends
`PASS` with `verified_problems_probe;problems=<summary>`. The report lands in
`problems.txt` in the results and in the private evidence directory, and the
workflow publishes it as the `problems` output parameter.

## Amendment 2026-11-05: the timer on the update path (T893d)

The first update runs (`x9htr`, `mfg6x`, `w7l84`) passed every check except
`timer`: `snosi-health.timer` was `enabled/inactive` on the first boot of N+1.
`preset-reconcile` runs `systemctl preset`, which enables a timer that the
update adds but does not start it, so the timer starts on the next boot. snosi
designed it so ([snosi#1229](https://github.com/frostyard/snosi/issues/1229)).
Brian decided (T893d) that the probe accepts that design and proves the next
boot:

- On the update path the lane makes one more boot of N+1, `problems-reboot`,
  after `problems`. Its guest runs the same `problems` mode, records the same
  static facts (timer, `preset-reconcile`, account, journal, `core_pattern`,
  boot timing) and injects nothing: `injected`, `collector_runs`, `problems`,
  `details` and `listing` are null, and the host refuses the record otherwise.
  The host also requires a new boot id and the N+1 digest, and keeps the record
  as `problems-reboot.json`.
- The `timer` check passes on the update path only when the first N+1 boot shows
  the timer `enabled` (`active` or `inactive`) and the second shows it
  `enabled/active`. The report adds 1 detail line,
  `reboot timer=<enabled>/<active> preset_reconcile=<result>`.
- The fresh path does not change: 1 boot, and the timer `enabled/active` on it.

## Consequences

- Phase 1 of T857 now has bounded, reproducible evidence on Snow, Floe,
  Snowfield and Sundog, on a fresh install and after an update, and a `missing-*`
  summary names the one check that failed instead of a vague failure.
- The lane writes into the installed QA VM only: one unit below `/etc`, one
  journal entry, one core dump and one line of `/run/snosi/update-check`. The
  image is untouched, and a VM without the collector changes nothing at all.
- A `BLOCKED: problems_unproven-*` result is not a product failure: it means the
  probe could not prove the collector's work (no collector in the image, or a
  document above the parse bound) and a human must look at the guest.
- The mode is report-only until 3 runs with `problems=ok` on each product and
  each path; making it a gate is a new decision, not a change to this one.
- A record that a real product would report is never sent anywhere: no report
  leaves the VM, and the local unit's name stays in `details.json`.

## Alternatives considered

- **A separate WorkflowTemplate:** it would duplicate the installer, the SMBIOS
  credential plumbing, the serial channel and the evidence pipeline for one more
  boot mode.
- **Fail the run when a check is missing:** that would make a collector behavior
  a release gate, which this lane is not (see
  [ADR-0009](0009-no-artifact-store-logs-are-the-surface.md) and
  [ADR-0010](0010-vacuous-success-is-forbidden.md)); `unproven-` and `missing-`
  say the same thing without claiming a product failure.
- **Inject the problems from the host:** the collector reads the guest's own
  systemd, journal and coredump state, so a host-side injection proves nothing
  about the installed image.

## References

- Shapes: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — Manual Snow bootc lifecycle evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Builds on: [ADR-0003](0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md),
  [ADR-0014](0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md),
  [ADR-0016](0016-first-login-probe-is-a-report-only-lane-mode.md)
- Related: [ADR-0012](0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)
