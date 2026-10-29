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
  `timeout -k 2 5`, so a TTY that stopped transmitting cannot stop the script or
  the kmsg copy.
- **Serial health probe.** After the serial-getty wait, the script opens
  `/dev/ttyS0` with `O_NONBLOCK`, writes the whole probe line (it retries
  EAGAIN and short writes, and any other write error gives `unknown`), then calls
  `termios.tcdrain`, all under `timeout -k 2 10`, and emits `serial_tty=ok` (drained),
  `stalled` (killed by `timeout`) or `unknown` (any other failure). For a
  value other than `ok` it also writes 2 `FIRN_QA__DIAG` lines (the TIOCOUTQ
  byte count and the `stty -a` flags) to the journal and kmsg. They are not
  `CHECK` lines.
- **Complete records only.** The host strips `\r` and accepts a marker only when
  its value runs to the end of the line (a kernel prefix before the marker is
  fine). Kernel printk can cut into a userspace line without a newline, for
  example `FIRN_QA__CHECK serial_tty=o[   12.3] kernel text`; that fragment is
  dropped, not kept as a wrong value. After the extraction each key must have
  1 distinct value. The guest emits each key once per run, so 2 complete values
  for 1 key mean a corrupted record, and the lane fails `boot:checks_ambiguous`
  before the judge runs and prints the conflicting lines.
- **Bounded children.** Every new guest `timeout` is `timeout -k 2 N`, so a
  process that ignores TERM is killed. A probe killed that way (rc 137) counts
  as `stalled`, like rc 124.
- **Judge.** The judge fails `boot:serial_check_missing` when `serial_tty` is
  absent, `boot:serial_tty_stalled` when it is `stalled`, and
  `boot:serial_tty_unknown` for any other value except `ok`. The checks run
  after the fs-verity checks and before the PASS line; no earlier code or its
  order changes.

The marker greps match anywhere in a line, so a kernel-log line such as
`[   39.1] FIRN_QA__CHECK booted=ok` is found, and `sort -u` merges the
identical ttyS0 and kernel-log copies.

## Consequences

- A stalled ttyS0 no longer hides the results: the lane gets the checks from
  the kernel log and fails with its own code, `boot:serial_tty_stalled`,
  instead of `boot:no_report`. A stalled TTY is a failure of the lane even
  when every other check passes, because the serial getty is the evidence path
  and a user's serial login.
- Each `FIRN_QA__CHECK` line is sent 3 times on each of the 2 paths (emit plus
  2 replays), so up to 6 copies reach the log. `FIRN_QA__CHECKS_DONE` is sent 2
  times on each path, so up to 4 copies. `sort -u` merges identical copies.
  A cut copy is dropped by the complete-record rule; 2 different complete values
  fail the lane as `boot:checks_ambiguous`, which is the safe answer for a log
  that cannot be trusted.
- A kernel line adds a timestamp prefix and is subject to printk limits. The
  extraction pattern does not depend on the line start, but it needs the value
  to end the line.
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
