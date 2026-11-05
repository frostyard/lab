# 0018 — The report probe is a report-only lane mode

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

Snosi ships a problem-report sender (snosi ADR-0030): `/usr/libexec/snosi-health-report`
sends the records of `/var/lib/snosi-health/problems.json` to an intake, and
`snosi-health reporting on|off` plus `/etc/snosi-health/reporting.conf` decide
whether it may. `snosi-health-report.timer`/`.service` run it 20 s after boot and
weekly, and 1 report of kind `count` travels per week. Nothing in this lane
looked at that sender: the five-boot path attests images, and the sysext,
first-login and problems probes answer other questions. So "does the sender send
nothing while reporting is off, and does it send a real problem and 1 weekly
count, valid and without the client's IP, when reporting is on" had no evidence.

Two more facts shape the decision. The production intake
(`https://report.frostyard.org/v1/report`) accepts real reports, so a probe that
only used it would either send test data to production or prove nothing about
the path a real host takes. And the intake is a Cloudflare Worker
(frostyard/problem-intake), whose edge adds the client IP headers and whose
Worker must strip them: a Python mock of the intake would prove neither the
Worker's acceptance nor its IP handling.

The lane already has 3 optional probe modes that replace the five-boot plan and
report without gating the release
([ADR-0016](0016-first-login-probe-is-a-report-only-lane-mode.md),
[ADR-0017](0017-problems-probe-is-a-report-only-lane-mode.md)). A report check
has the same shape: it needs a real installed VM (the sender reads the installed
systemd units, journal and `/etc`, and its timer must fire), and its outcome is
evidence about the image, not a release decision.

## Decision

The manifest may carry the optional key `report_probe`, an object with exactly
`{"intake": "lab"}` or `{"intake": "production"}`, instead of `sysext_probe`,
`first_login_probe` or `problems_probe`; the four never travel together. In this
commit it is valid only for product `floe`, the product that ships the sender
first; a desktop product is a later change. It never stages N+1, so its rule is
`version_distinct`, its budget is `installed_boot`, and its plan is the one
`report` boot of the installed N.

The `report` boot runs the guest's report code as root, and its one guest record
is the evidence. The code does not fit in `qa.py`, so it travels in its own
SMBIOS credential (`snow-report.z`, xz of the comment-stripped program) and
`qa.py` only dispatches to it; the same strip-and-AST-check step that bounds
`qa.py` bounds it below the 64 KiB credential limit.

With `intake: lab` the run first creates a throwaway Incus **container** with
the real problem-intake Worker under `wrangler dev` and a mock GlitchTip behind
it, on the container's own address. The runner starts and deletes that container
through the mounted Incus socket, like the VM, and never keeps it: it holds the
run's random bearer. The proxy in the container adds the 3 headers a Cloudflare
edge adds (`CF-Connecting-IP`, `X-Forwarded-For`, `True-Client-IP`) and logs
every request; the mock keeps every envelope it receives. The guest then:

1. marks `off-begin`, injects 1 app problem and 1 crash and runs the collector
   twice, then proves the off state: no `/etc/snosi-health` before the consent
   change, a `snosi-health-report.service` start that its condition skips, a
   `send` that answers "problem reporting is off", no `sent.jsonl`, and what a
   `send --dry-run` would send;
2. sets the endpoint in `/etc/snosi-health/reporting.conf` as an administrator
   would, marks `on-begin` and turns reporting on, which must keep that
   endpoint;
3. restarts the timer and waits, bounded, for it to start the sender once, then
   marks `on-end` and reads the sender's own `/var/lib/snosi-health/sent.jsonl`.

The run then pulls the container's 2 JSON line logs (requests and envelopes) as
evidence. With `intake: production` there is no container, no mark and no
consent change: the guest sends exactly 1 lab test report (`app_problem`, code
`lab.injected_t857`) with `curl` and no redirect, over `https` only.

The host validates the record's exact shape, identity, boot id and booted image,
and reports one summary line:

- `unproven-no-sender` when the image predates the sender;
- `unproven-intake` when a mark is missing from the intake's request log, or the
  marks are out of order (the guest never reached the intake);
- otherwise `ok`, or `missing-` plus the failed checks: `off` (a report posted
  before `on-begin`, or a failed guest off check), `timer`, `consent`, `count`
  (not exactly 1 accepted `count`), `app` (no accepted `lab.injected_t857`),
  `crash` (a crash was sent, which must never happen), `schema` (a posted body
  is not `frostyard.problem/1`, or a POST was not 202), `ip` (a peer IP reached
  GlitchTip's headers or body, an edge header survived in the envelope, or an
  event carries `user.ip_address`), `forward` (an accepted report has no single
  authenticated envelope with its event id, or a count has one) and `match` (the
  sender's log and the intake's accepted reports name different report ids);
- `production-accepted` when the production intake answered 202 for that 1
  report, else `missing-production`.

The record's schema check reuses the pinned copy of core's
`problem-report/v1` schema that
[ADR-0017](0017-problems-probe-is-a-report-only-lane-mode.md) added, so the
probe proves the schema a real report must satisfy.

The probe never fails the run for a product finding. A summary that starts with
`unproven-` ends `BLOCKED` with reason `report_<summary>`
([ADR-0003](0003-unproven-is-distinct-from-failed.md)); every other summary ends
`PASS` with `verified_report_probe;report=<summary>`. The report lands in
`report.txt` in the results and in the private evidence directory, with
`report.json`, `intake-requests.jsonl` and `intake-glitchtip.jsonl`, and the
workflow publishes it as the `report` output parameter.

## Consequences

- Phase 2 of T857 now has bounded, reproducible evidence on Floe that reporting
  off sends nothing, that reporting on sends the injected problem and 1 weekly
  count, that the intake accepts them as `frostyard.problem/1`, and that no
  client IP reaches GlitchTip.
- The lane needs a `git`, a Node 22+ and an `npm ci` inside the lab container:
  the run's setup time grows by minutes, and the pin of the intake commit, the
  Node release and its tarball sha256 must move together when the intake
  changes.
- The lab intake is real code, so a defect in the intake now shows up as a
  `missing-*` finding of this lane, not only in the intake's own tests.
- `report_probe` is Floe only until the desktop products ship the sender; a
  desktop image is refused with `report_product` rather than probed.
- The mode is report-only until 3 lab runs with `report=ok` and 1 accepted
  production report; making it a gate is a new decision, not a change to this
  one.
- The run's bearer and DSN key are random per run, live only in the container,
  and never reach the pod log, the evidence or a report.

## Alternatives considered

- **A Kubernetes Deployment for the lab intake:** a dev run cannot deploy to the
  cluster, and a long-lived service with a per-run bearer would be a new object
  to keep, secure and clean up instead of one throwaway container the run
  deletes.
- **The intake as a pod in the lane's own namespace:** `wrangler`, `npm` and the
  Worker's dependencies must not run in the pod that holds the Incus socket.
- **A Python mock instead of the Worker:** acceptance asks for evidence about the
  real Worker, including that it strips the edge's client-IP headers; a mock
  proves only the mock.
- **The production intake only:** that would send test data to production and
  would not let the probe force the off and on states of a lab host.
- **A separate WorkflowTemplate:** it would duplicate the installer, the SMBIOS
  credential plumbing, the serial channel and the evidence pipeline for one more
  boot mode.

## References

- Shapes: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — Manual Snow bootc lifecycle evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Run plan: [2026-11-05 — Report probe on the Floe report sender](../plans/2026-11-05-report-probe-runs.md)
- Builds on: [ADR-0003](0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0006](0006-host-daemon-access-by-mount-never-ssh.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md),
  [ADR-0014](0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md),
  [ADR-0016](0016-first-login-probe-is-a-report-only-lane-mode.md),
  [ADR-0017](0017-problems-probe-is-a-report-only-lane-mode.md)
- Related: [ADR-0012](0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)
