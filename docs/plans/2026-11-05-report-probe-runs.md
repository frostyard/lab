# Plan: Report probe on the image problem-report senders (T857 t21)

This plan proves phase 2 of plan T857 in the lab: the snosi problem-report sender
of snosi ADR-0030 (`/usr/libexec/snosi-health-report`, the
`snosi-health reporting on|off` commands and `snosi-health-report.timer`/`.service`)
on Floe, and chairlift v0.15.0's windowless sender
(`chairlift --send-pending-reports`, chairlift
`docs/specs/problem-reporting.md`) on Snow, Snowfield and Sundog. Neither sender
sends while reporting is off, neither sender lets the client's IP reach
GlitchTip, and the real frostyard/problem-intake Worker accepts their reports as
`frostyard.problem/1`. It uses the report-only
[report probe](../adr/0018-report-probe-is-a-report-only-lane-mode.md) of the
[bootc lifecycle lane](../../README.md#manual-snow-bootc-lifecycle). The probe
records; it gates nothing until 3 lab runs of a product end `report=ok` and 1
production report is accepted, and then a new decision promotes it.

## Selected inputs

The manifests are in [`report-probe-2026-11-05/`](report-probe-2026-11-05/). Every
image carries snosi `a1bf8d2` (#1227, the Floe sender); the desktop images were
rebuilt from it by snosi run 42103135516 with chairlift v0.15.0. Trust pins are
the lane's (snosi `39cf1988`). N+1 is each product's `qa-controlled` target and is
never staged.

| Product | N | N+1 | ISO |
| --- | --- | --- | --- |
| Floe | `20261105212138` `sha256:9d737cc8…` | `20261105072939` | `20261105202508` |
| Snow | `20261105212729` `sha256:e97d3769…` | `20261105072915` | `20261106012529` |
| Snowfield | `20261105212116` `sha256:71a43188…` | `20261104190408` | `20261106012529` |
| Sundog | `20261105212212` `sha256:01ee075c…` | `20261105073038` | `20261106012529` |

The lab intake is frostyard/problem-intake `572fdd87` under `wrangler dev`
(wrangler 4.157.0, Node v24.22.1), with the mocked GlitchTip and the edge proxy of
ADR-0018.

## Phase 1 — Lane mode (this pull request)

- `report_probe` (`lab` | `production`) on Floe, the guest report code in its own
  credential, the host `report.py`, the lab intake (`intake-setup.sh`,
  `intake-lab.py`), the `report` workflow output,
  [ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md).
- **Done when:** the full test suite passes, CI is green and the cross-review is
  Ready.

## Phase 2 — Floe lab-intake dev runs, 3 of them

Submitted with
[`scripts/snow-lifecycle-dev-submit.sh`](../../scripts/snow-lifecycle-dev-submit.sh)
(`dev-snow-bootc-lifecycle-*`, label `snosi.io/trigger=dev`,
`keep-vm-on-failure=false`) under the `lab-dev` lease. No publish, no tag move.

- **Done when:** 3 Floe runs end `PASS: verified_report_probe;report=ok`, so the
  promotion bar of ADR-0018 for the lab intake is met.

### Result

2026-11-05: 3 of 3. `kgbqh`, `td7t9` and `p98kx` each ended
`PASS: verified_report_probe;report=ok`. In each: the off checks held (no
consent file: the service condition skipped it; `send` said reporting is off;
`reporting off` and the service sent nothing; no `sent.jsonl`), `reporting on`
kept the lab endpoint, the real timer started the sender (30 to 35 s), and the
intake accepted the injected `app_problem` (`lab.injected_t857`) and 1 `count`
(202). No crash was posted although the collector kept 1. Every envelope that
reached the mock had the bearer, its event id equal to the report id, and no
client IP. `kgbqh` also sent 1 `integrity_error` (`fs-verity`): the known btrfs
fs-verity kernel defect ([snosi#1130](https://github.com/frostyard/snosi/issues/1130))
that the problems probe also saw.

Lane defects found on the way, each fixed with a test: `fnn87`
(`BLOCKED: intake_setup`: the Node tarball name lost its `v`), `5pcgp` (a false
`missing-ip` from the intake's own loopback health check).

## Phase 3 — 1 production report

One Floe dev run with `{"intake": "production"}`. It sends exactly 1 lab test
report (`app_problem`, code `lab.injected_t857`) to
`https://report.frostyard.org/v1/report`.

- **Done when:** that run ends `PASS: verified_report_probe;report=production-accepted`
  and `report.txt` names the report id and the 202 status.

### Result

- `gwdpl` (2026-11-05 22:53 UTC): `report=missing-production`, HTTP 502. The
  Worker could not forward: class `config` in Analytics Engine (its secrets).
  Nothing reached GlitchTip. The Worker secrets `INTAKE_BEARER` and
  `GLITCHTIP_DSNS` were then set again.
- `9x87t` (2026-11-06 00:49 UTC): `PASS: verified_report_probe;report=production-accepted`,
  HTTP 202. 1 lab test report through the production Worker and caddy into
  GlitchTip: report id `bda364d0-a159-416e-a764-05fc7374e590`, event
  `bda364d0a159416ea76405fc7374e590`, project `system`. Its deletion by report id
  (`scripts/glitchtip-delete-report.sh` in frostyard/problem-intake, as root on
  TrueNAS) follows the desktop proofs below; the event stays in GlitchTip until
  then.


## Phase 4 — Desktops (chairlift), lane mode

Snow, Snowfield and Sundog send through chairlift v0.15.0's windowless mode, in
the test-only user's own session.

- `report_probe` for every product, the chairlift guest path (session, the
  administrator endpoint, the consent file, the 2 off runs and the on run, the
  dry-run preview for `intake: production`), the record's `sender_version`,
  `session` and `cli` keys, the desktop summaries and `unproven-session`
  ([ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md)).
- **Done when:** the full test suite passes and the cross-review is Ready.
- Then 3 lab runs per desktop product, and 1 production run, each submitted as a
  dev run under the `lab-dev` lease:
  **Done when:** each desktop product has 3 runs with `report=ok`.

### Result

2026-11-06: 3 of 3 on each desktop, every one
`PASS: verified_report_probe;report=ok`:

| Product | Runs | Session |
| --- | --- | --- |
| Snow | `gf584`, `vrfqr`, `gsntg` | GNOME, Wayland |
| Snowfield | `q7xtt`, `psmgd`, `vtkw9` | GNOME, Wayland |
| Sundog | `5h8qj`, `lfckb`, `grt4h` | KDE, Wayland |

In each: chairlift 0.15.0 (`frostyard-chairlift`) printed
`problem reporting is off` and sent nothing with no consent file and with a
consent that is off; with the administrator endpoint in `/etc/chairlift/config.yml`
and an automatic consent it sent the injected `app_problem`, 1 `count` and 1
`integrity_error` (`fs-verity`, snosi#1130), all 202, with 2 authenticated
envelopes (the count is never forwarded) and no client IP.

Lane defects found on the way, each fixed with a test: `6swrx`
(`BLOCKED: phase_timeout`: the boot's SMBIOS values summed to about 68500
characters, so the guest unit never ran; the sum is now bounded),
`zf6pf`/`rghj8`/`gprvv` (`FAILED: report_logs`: the probe asked dpkg for
`chairlift` instead of `frostyard-chairlift`, and an empty GlitchTip log was
never created), `td7hd`/`szkq8` (`preflight_mismatch:iso_index`: the signed ISO
index moved). `t9p4r` (Snowfield) ended `BLOCKED: phase_timeout`: the boot
stalled at 9.7 s of kernel time after the switch to the installed root, before
the guest unit; it is not a probe result, and `vtkw9` replaced it.

The desktop production report is not run: 1 production report (phase 3) proves
the Worker, caddy and GlitchTip path, and every desktop report goes through the
same Worker.


## Later / ideas

- Promote the mode to a gate once the promotion bar is met, as a new ADR.
- Fold the lab intake into a lane-owned image if the container setup time
  becomes a problem.

## Open questions

- None. The snosi sender's `sent.jsonl` shape and chairlift's off line were
  answered by the runs above.

## References

- Implements: [ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md),
  [ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md)
- Shapes: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — Manual Snow bootc lifecycle evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
