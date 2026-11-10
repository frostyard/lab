# 0014 — Lifecycle lane records also travel through the kernel log

- **Status:** Accepted
- **Date:** 2026-11-02

## Context

The Snow, Floe and Sundog lifecycle lane reads each phase result from the
serial log, as
[ADR-0005](0005-console-marker-protocol-for-agentless-guests.md) and
[ADR-0013](0013-firn-lane-markers-also-travel-through-the-kernel-log.md)
decide. The guest unit runs `qa.py guest` (or `qa.py sysext`), which writes its
one record line to `/dev/ttyS0` and `/dev/console`, then requests
`systemctl --no-block poweroff` ([lab#177](https://github.com/frostyard/lab/issues/177)).

Lab run `dev-snow-bootc-lifecycle-pcl2l` (Sundog) blocked at phase
`rollback`, and `dev-snow-bootc-lifecycle-52r5s` blocked at phase `stage`.
Each serial log ends about 9 s after the boot start, kernel lines included
(the last line is a cfg80211 `Loaded X.509 cert` line). The host then waited
out the whole phase window and reported `BLOCKED: phase_timeout`; in `52r5s`
the VM stayed `RUNNING` but idle. This matches the Sundog ttyS0 stall of
ADR-0013, but it is not proven: both VMs were deleted.

The code has 2 faults that turn such a stall into a lost phase window:

- `emit()` writes the record to `/dev/ttyS0` with no time bound. A ttyS0 that
  stopped transmitting blocks the write forever, so the unit never reaches its
  poweroff, and the host cannot tell a stall from a slow boot.
- The unit's `ExecStart` runs `qa.py` and then the poweroff under `bash -ec`,
  so a non-zero `qa.py` exit skips the poweroff and costs the same window.

The records are large: a `SNOW_QA_V1` line can reach 8 KiB and a
`SNOW_SYSEXT_V1` line 12 KiB, and one kernel-log record holds about 1 KiB.
2 of 11 started Sundog boots that night ended this way, so the lane needs a
second channel and a bounded guest, not a rerun.

## Decision

- **Kernel-log copy, before the serial writes.** The guest writes every record
  line to `/dev/kmsg` at level 4, with a fresh open per line, because devkmsg
  rate-limits per open. Since a line does not fit in one kmsg record, the guest
  encodes the line as base64url without padding and splits that text into
  chunks of at most 384 characters. It sends
  `SNOW_QA_KMSG1 <id> <i>/<n> <crc> <data>` lines, where `<id>` is the first 16
  hex characters of the line's SHA-256, `<i>/<n>` the chunk index and count
  (at most 64), and `<crc>` the first 8 hex characters of the chunk's SHA-256.
  The chunk text is encoded so that no chunk line contains a marker name: the
  runner's greps must never match a half-received record before the join.
- **Bounded serial writes.** Every write to a console target runs as a child
  under `timeout -k 2 5`. A target that has to be killed (exit 124 or 137) is
  added to `STALLED_TARGETS` and skipped for the rest of the guest process, so
  one stall costs seconds, not the record.
- **The whole replay stays inside the margin.** `publish()` runs against 1
  deadline, `PUBLISH_MARGIN` minus `PUBLISH_RESERVE` (20 s kept for the last
  getty query and the poweroff request). Every console write is cut short or
  skipped rather than allowed to cross it, so no write duration can extend the
  replay. `PUBLISH_WAIT` is 80 s and the hold 30 s, and both end at that same
  deadline at the latest: `PUBLISH_WAIT + PUBLISH_HOLD + PUBLISH_RESERVE <=
  PUBLISH_MARGIN`.
- **The guest powers off on either path.** `ExecStart` runs
  `qa.py … || true; systemctl --no-block poweroff`, so a failed guest run still
  stops the VM and the host ends the phase at once instead of waiting out the
  window.
- **The host joins the chunks.** After `serial()` writes the phase slice, the
  host runs `qa.py kmsg-join`. It keeps complete lines only, dropping the bytes
  after the last newline, because the guest always ends a record line with one
  and a cut tail is a copy still in transfer. It joins only complete, correct
  groups, decodes their base64url text with a canonical re-encode check,
  appends the recovered record lines to the slice and writes the slice back.
  It prints `serial_record=<0|1> kmsg_record=<0|1>`, from the kept lines. Every
  existing parser (the `await` greps, the 2 error parsers, `qa.py phase` and
  `qa.py sysext-phase`) then reads the joined lines with no change. The join is
  idempotent, and the next `serial()` call makes the slice again from the
  console snapshot.
- **Complete records only.** A stalled ttyS0 can cut a marker line and kernel
  text then follows on the same line. The host keeps only marker tokens that
  are complete (`SNOW_QA_V1 <base64url>`, `SNOW_QA_ERR <code>` and the sysext
  names) before the count check, as ADR-0013 decides for the Firn lane. An
  incomplete token alone is `record_encoding`; 2 distinct complete tokens are
  `record_count`. A chunk line carries a fragment of a record line in its own
  data, so no parser treats a chunk line as a record line (with or without the
  join), and `sysext_record_count` drops unreadable tokens as before.
- **Channel evidence.** After each accepted phase record the runner appends
  the join's summary line to `serial-channel.txt` as best effort. That file
  never changes `checks.txt`, the verdict or the reason.

## Consequences

- A ttyS0 stall costs 1 boot of the lane, not a whole phase window: the VM
  stops either way, and the record arrives through the kernel log when kernel
  console output still reaches the serial log (as in ADR-0013). When kernel
  output stops too, the phase ends at once as
  `BLOCKED: guest_stopped_without_record`, not after the window.
- A stall is not a failure of this lane. The Firn lane gate
  `boot:serial_tty_stalled` covers TTY health; here the evidence shows it as
  `serial_record=0 kmsg_record=1` in `serial-channel.txt`.
- The kernel copy needs a `console_loglevel` above 4. ADR-0013 measured 7 on
  Sundog; lower levels and a `printk_devkmsg` of `off` leave only the serial
  channel.
- The kernel log of the system under test carries the records, and a chunk
  line is noise on the console. The host greps only the 4 marker names.
- Chunk lines carry base64url text, so they cost about 1/3 more than the
  record. 64 chunks hold 18432 bytes: an 8 KiB `SNOW_QA_V1` line takes 29
  chunks and a `SNOW_SYSEXT_V1` line at its token bound takes 42. A record
  over the bound cannot join, which fails closed.
- An installer ISO has no `qa.py` and keeps its `SNOW_INSTALL_*` writes
  unchanged.

## Alternatives considered

- **One kmsg line without chunks:** rejected. One kernel-log record holds about
  1 KiB, so the kernel truncates a 8 KiB or 12 KiB record and the host would
  read a cut line as a record.
- **Dropping ttyS0 for the records:** rejected, as in ADR-0013. ttyS0 is still
  the getty, the evidence path and a user's serial login.
- **Failing the lane on a stall:** rejected. The TTY fault is not the subject
  of this lane and the Firn lane gate already reports it.

## References

- Shapes: [docs/quality.md — Manual Snow bootc lifecycle evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Amends: [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md)
- Extends: [ADR-0013](0013-firn-lane-markers-also-travel-through-the-kernel-log.md)
- Implemented by:
  [argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml](../../argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml),
  [tests/test_snow_bootc_lifecycle.py](../../tests/test_snow_bootc_lifecycle.py)
- Related: [ADR-0009](0009-no-artifact-store-logs-are-the-surface.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md)
- Amended by: [ADR-0019](0019-kernel-log-copies-use-level-err.md)
