# 0013 — Firn lane markers also travel through the kernel log

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

The firn install lane reads the post-install results from the serial log
([ADR-0005](0005-console-marker-protocol-for-agentless-guests.md)): the guest
check unit writes each `FIRN_QA__CHECK` line, then `FIRN_QA__CHECKS_DONE`, to
`/dev/ttyS0`, and the pod greps `incus console --show-log`.

Lab run `bootc-only-iso-install-ffbw7` (Sundog, 2026-10-29) failed
`boot:no_report`. In its serial console, userspace output to ttyS0 stops at
PID 1's console reset at switch-root. The last userspace bytes are systemd's
terminal size query (`ESC[18t ESC7 ESC[32766;32766H ESC[6n ESC8`). The ANSI
reset that systemd always writes next (`ESC[!p ESC]104 ESC\ ...`) never
appears. After that only kernel printk lines reach the log (up to 10.8 s),
then nothing for 900 s: no getty prompt and no `FIRN_QA__CHECK` line. In
passing boots the reset sequence and the systemd status follow at once. So
kernel printk survives on the serial console when userspace writes to ttyS0 do
not. The guest kernel `console_loglevel` is 7 and `printk_devkmsg` is `on`.
The fault is intermittent: 1 of 24 Sundog installed boots that night
([run record](../plans/2026-10-29-sundog-fresh-install-rerun.md)).

With every marker on ttyS0 only, a stalled TTY loses the whole result and the
lane reports `no_report`, which does not say why.

## Decision

- **Kernel-log copy.** The guest check script writes every marker line to
  `/dev/kmsg` too, at level 4, which prints on the console at
  `console_loglevel` 7. Each line uses a fresh open of `/dev/kmsg`, because
  devkmsg rate-limits per open (10 lines per 5 s). The kmsg write comes
  before the ttyS0 write. The 2 final replays (all `FIRN_QA__CHECK` lines plus
  `FIRN_QA__CHECKS_DONE`) go through kmsg first, 1 line per open.
- **Bounded ttyS0 writes.** Every guest write to `/dev/ttyS0` runs under
  `timeout 5`, so a TTY that stopped transmitting cannot stop the script or
  the kmsg copy.
- **Serial health probe.** After the serial-getty wait, the script opens
  `/dev/ttyS0` with `O_NONBLOCK`, writes a probe line and calls
  `termios.tcdrain` under `timeout 10`, and emits `serial_tty=ok` (drained),
  `stalled` (killed by `timeout`) or `unknown` (any other failure). For a
  value other than `ok` it also writes 2 `FIRN_QA__DIAG` lines (the TIOCOUTQ
  byte count and the `stty -a` flags) to the journal and kmsg. They are not
  `CHECK` lines.
- **Judge.** The judge fails `boot:serial_check_missing` when `serial_tty` is
  absent, `boot:serial_tty_stalled` when it is `stalled`, and
  `boot:serial_tty_unknown` for any other value except `ok`. The checks run
  after the fs-verity checks and before the PASS line; no earlier code or its
  order changes.

The host side needs no change: the marker greps match anywhere in a line, so a
kernel-log line such as `[   39.1] FIRN_QA__CHECK booted=ok` is found, and
`sort -u` merges the ttyS0 and kernel-log copies.

## Consequences

- A stalled ttyS0 no longer hides the results: the lane gets the checks from
  the kernel log and fails with its own code, `boot:serial_tty_stalled`,
  instead of `boot:no_report`. A stalled TTY is a failure of the lane even
  when every other check passes, because the serial getty is the evidence path
  and a user's serial login.
- Every marker appears up to 3 times in the log (kernel log and ttyS0). The
  collector keeps `sort -u`, so duplicates are harmless.
- A kernel line adds a timestamp prefix and is subject to printk limits. The
  extraction pattern does not depend on the line start.
- The guest script writes to the kernel log of the system under test. This
  changes no image and adds no agent.
- The probe line and the `FIRN_QA__DIAG` lines are noise on the console; the
  host never greps them.
- The kmsg path rescues the results but not the underlying fault. The cause of
  the stall is a separate investigation, and the `FIRN_QA__DIAG` lines are
  its evidence.

## Alternatives considered

- **Incus agent via `incus exec`:** rejected, as in ADR-0005. Some images can
  start the agent (Sundog has `enable-incus-agent.service`), but not every
  image and not the installer ISO; the lanes stay agentless.
- **Dropping ttyS0 for the markers:** rejected. ttyS0 is still the getty and
  the evidence path of the serial console, and it stays in the lane next to
  kmsg.

## References

- Shapes: [docs/quality.md — Bootc-only ISO install evidence](../quality.md#bootc-only-iso-install-evidence)
- Amends: [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md)
- Implemented by:
  [argo/workflow-templates/run-firn-install-tests.yaml](../../argo/workflow-templates/run-firn-install-tests.yaml),
  [tests/test_firn_install_lane.py](../../tests/test_firn_install_lane.py)
- Related: [ADR-0009](0009-no-artifact-store-logs-are-the-surface.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md)
