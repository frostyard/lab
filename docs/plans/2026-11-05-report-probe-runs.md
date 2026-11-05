# Plan: Report probe on the image problem-report senders (T857 t21)

This plan proves phase 2 of plan T857 in the lab: the snosi problem-report sender
of snosi ADR-0030 (`/usr/libexec/snosi-health-report`, the
`snosi-health reporting on|off` commands and `snosi-health-report.timer`/`.service`)
on Floe, and chairlift v0.15.0's windowless sender
(`chairlift --send-pending-reports`, chairlift
`docs/specs/problem-reporting.md`) on Snow, Snowfield and Sundog. Neither sender
sends while reporting is off, neither loses the client's IP when it sends, and
the real frostyard/problem-intake Worker accepts their reports as
`frostyard.problem/1`. It uses the report-only
[report probe](../adr/0018-report-probe-is-a-report-only-lane-mode.md) of the
[bootc lifecycle lane](../../README.md#manual-snow-bootc-lifecycle). The probe
records; it gates nothing until 3 lab runs of a product end `report=ok` and 1
production report is accepted, and then a new decision promotes it.

## Selected inputs

The images, the ISO and the trust pins of each run go here, one manifest per
phase in a folder beside this plan.

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

*(to be filled from the runs)*

## Phase 3 — 1 production report

One Floe dev run with `{"intake": "production"}`. It sends exactly 1 lab test
report (`app_problem`, code `lab.injected_t857`) to
`https://report.frostyard.org/v1/report`.

- **Done when:** that run ends `PASS: verified_report_probe;report=production-accepted`
  and `report.txt` names the report id and the 202 status.

### Result

*(to be filled from the run)*

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

*(to be filled from the runs)*

## Later / ideas

- Promote the mode to a gate once the promotion bar is met, as a new ADR.
- Fold the lab intake into a lane-owned image if the container setup time
  becomes a problem.

## Open questions

- **Question:** does the snosi sender's `/var/lib/snosi-health/sent.jsonl` line
  keep `status`, `http_status`, `problem_id` and a nested `report` object? The
  guest reads that shape; a different shape makes the `match` check fail and
  needs a guest fix, by phase 2. (Phase 2 answered this for Floe.)
- **Question:** does chairlift's `--send-pending-reports` output name the consent
  in the off runs, and how many problems does the live `problems_page` report? 2
  injected documents (1 app problem, 1 count) are expected on a fresh VM; more
  reportable items would make the `count` and `app` checks pass and the envelope
  checks fail, and phase 4 must then decide whether to dismiss them first.

## References

- Implements: [ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md),
  [ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md)
- Shapes: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — Manual Snow bootc lifecycle evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
