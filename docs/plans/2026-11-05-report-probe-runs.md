# Plan: Report probe on the Floe report sender (T857 t21)

This plan proves phase 2 of plan T857 in the lab: the snosi problem-report sender
of snosi ADR-0030 (`/usr/libexec/snosi-health-report`, the
`snosi-health reporting on|off` commands and `snosi-health-report.timer`/`.service`)
neither sends while reporting is off nor loses the client's IP when it sends,
and that the real frostyard/problem-intake Worker accepts its reports as
`frostyard.problem/1`. It uses the report-only
[report probe](../adr/0018-report-probe-is-a-report-only-lane-mode.md) of the
[bootc lifecycle lane](../../README.md#manual-snow-bootc-lifecycle) on Floe, the
first product that ships the sender. The probe records; it gates nothing until 3
lab runs end `report=ok` and 1 production report is accepted, and then a new
decision promotes it.

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

## Phase 4 — Desktops (chairlift)

The desktop products send chairlift problems; the mode is Floe only until they
ship the sender.

- Work item: allow `report_probe` for Snow, Snowfield and Sundog, and probe the
  chairlift `app_problem` the first-login probe already starts.
- **Done when:** each desktop product has 3 runs with `report=ok`.
- Blocked on chairlift v0.15.0.

## Later / ideas

- Promote the mode to a gate once the promotion bar is met, as a new ADR.
- Fold the lab intake into a lane-owned image if the container setup time
  becomes a problem.

## Open questions

- **Question:** does the sender's `/var/lib/snosi-health/sent.jsonl` line keep
  `status`, `http_status`, `problem_id` and a nested `report` object? The guest
  reads that shape; a different shape makes the `match` check fail and needs a
  guest fix, by phase 2.

## References

- Implements: [ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md),
  [ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md)
- Shapes: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — Manual Snow bootc lifecycle evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
