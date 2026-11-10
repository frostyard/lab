# 0019 — Kernel-log marker copies use level err

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

[ADR-0013](0013-firn-lane-markers-also-travel-through-the-kernel-log.md) and
[ADR-0014](0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
write the kernel-log copy of each marker line at level 4 (warning). They assume
a `console_loglevel` of 7, which ADR-0013 measured on Sundog on 2026-10-29.

[snosi ADR-0033](https://github.com/frostyard/snosi/blob/main/docs/adr/0033-seal-the-plymouth-splash-into-the-secure-uki-command-line.md)
([frostyard/snosi#1263](https://github.com/frostyard/snosi/pull/1263)) seals
`splash quiet` into the UKI command line of Snow, Snowfield and Sundog, the
images whose rootfs ships `plymouthd`. `quiet` sets `console_loglevel` to 4
(`CONSOLE_LOGLEVEL_QUIET`). The kernel prints a message to a console only when
the message's level is lower than `console_loglevel`. So a level 4 copy no
longer reaches ttyS0 on those images. A stalled ttyS0 then fails the Firn lane
as `boot:no_report`, and the lifecycle lane as
`BLOCKED: guest_stopped_without_record` or a lost record, instead of the
outcome that ADR-0013 and ADR-0014 decide.

This follows from the printk rules ([lab#215](https://github.com/frostyard/lab/issues/215));
it is not yet seen in a run. Floe is not affected: it carries no `quiet`.

## Decision

Both agentless guests write every kernel-log copy at level 3 (err). 3 is lower
than 4, so the copy prints at `console_loglevel` 4 and at 7.

Nothing else in ADR-0013 or ADR-0014 changes: the fresh open per line, the
order, the chunk format, the host parsing and the judge codes stay as they are.

## Consequences

- The copies print on the serial console at `console_loglevel` 4 (quiet) and
  at 7.
- The copies show as errors in the guest journal and in
  `dmesg --level=err`. That is noise on the system under test. It changes no
  image.
- A guest whose `console_loglevel` is 3 or lower (for example a sysctl
  `kernel.printk` of 3) still drops the copies, and so does
  `printk_devkmsg=off`.
- The splash and the Plymouth check of the Firn lane are unchanged, because the
  lane does not change the command line.

## Alternatives considered

- **Raise `console_loglevel` in the QA guest:** rejected. A `loglevel=` addon
  under Secure Boot needs a signing key enrolled in MOK
  ([snosi-kargs](https://github.com/frostyard/snosi/blob/main/docs/snosi-kargs.md)),
  which an agentless lane cannot do, and it changes the effective command line
  of the system under test. A runtime write to `/proc/sys/kernel/printk`
  changes kernel state of the system under test for every later message.
  Level 3 needs neither change.
- **Read the markers from the guest journal:** rejected. The lanes are
  agentless ([ADR-0005](0005-console-marker-protocol-for-agentless-guests.md)),
  and the journal does not reach the host when ttyS0 stalls.
- **Levels 0 to 2:** rejected. emerg, alert and crit add nothing at
  `console_loglevel` 4, and they claim a severity that a marker line does not
  have.

## References

- Amends: [ADR-0013](0013-firn-lane-markers-also-travel-through-the-kernel-log.md),
  [ADR-0014](0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
- Implemented by:
  [argo/workflow-templates/run-firn-install-tests.yaml](../../argo/workflow-templates/run-firn-install-tests.yaml),
  [argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml](../../argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml),
  [tests/test_firn_install_lane.py](../../tests/test_firn_install_lane.py),
  [tests/test_snow_bootc_lifecycle.py](../../tests/test_snow_bootc_lifecycle.py)
- Related: [snosi ADR-0033](https://github.com/frostyard/snosi/blob/main/docs/adr/0033-seal-the-plymouth-splash-into-the-secure-uki-command-line.md),
  [lab#215](https://github.com/frostyard/lab/issues/215)
