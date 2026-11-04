# 0016 — The first-login probe is a report-only lane mode

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

Snow, Snowfield and Sundog ship a first-run application: `first-setup` (an
autostart entry, a `python3` script and a completion script), and later
`chairlift` (`/usr/bin/chairlift-first-run`, which execs `/usr/bin/chairlift
--first-run` and logs `first-run: window presented` to the user journal).
Nothing in the lifecycle lane looks at a desktop login: the five-boot path
attests booted images, updates and rollback only. So "does the first-run app
actually start for a new user on a fresh install" was a question that no
evidence answered, and Floe (headless, no graphical stack) must not be held to
it.

The lane already has one optional probe mode, `sysext_probe`, which replaced
the five-boot plan with its own boots and reported results without gating the
release. A first-login check has the same shape: it needs a real fresh install
and a real login, it cannot be run from a container, and its outcome depends on
a desktop stack that the lane does not otherwise touch (Snowfield's
`installed-n` attestation boot is BLOCKED on kernel lockdown,
[frostyard/snosi#1213](https://github.com/frostyard/snosi/issues/1213), which
is not a first-login fact).

## Decision

The manifest may carry the optional key `first_login_probe` (an object with
exactly `session_wait`, 60-900, and `app_wait`, 30-600) instead of
`sysext_probe`; the two never travel together. With it the lane boots the
installed N image once (`login-1`) or twice (also `login-2`, all products but
Floe) and never attests `installed-n` or stages N+1, so the version rule is
`version_distinct`. Both waits plus 180 s must fit the smaller of the
`installed_boot` and `reboot` timeouts.

`login-1` alone installs a test-only autologin configuration into the installed
`/etc` (never the image): a systemd unit runs `login-setup.sh` as root, which
names the active display manager, creates the `snowqa` account, and writes
either GDM's `daemon.conf` or SDDM's `zz-snow-qa-autologin.conf`. A system with
no GDM or SDDM gets `dm=none` and no account.

The guest publishes one `SNOW_LOGIN_V1` record per boot: display manager,
whether the autologin configuration survived, the session (type, class, state,
desktop), the first-run autostart entries it found, which known apps have an
entry and a binary, which apps it saw running (a pid in two consecutive polls,
or `chairlift`'s journal line), the mark-done results of `login-1`, and the done
markers. The host validates every field, the phase, the nonce and the booted
image, keeps the record as evidence, and then reports one summary line:
`headless-*` for Floe, `unproven-*` when the probe proved nothing, and
`<app>-once`, `both-once`, `repeated-*`, `late-*` or `none` for the app it saw.

The probe never fails the run because of which app started. A summary that
starts with `unproven-` ends `BLOCKED` with reason `first_login_<summary>`
([ADR-0003](0003-unproven-is-distinct-from-failed.md)); every other summary
ends `PASS` with `verified_first_login_probe;first_login=<summary>`. The report
lands in `first-login.txt` in the results and in the private evidence
directory, and the workflow publishes it as the `first-login` output parameter.

## Consequences

- A first-login question now has bounded, reproducible evidence, and a mode
  that never claimed to prove more than it saw: it reports, it does not gate.
- The lane writes into the installed `/etc` of the QA VM only, in `login-1`,
  through one unit that keeps the credentials in SMBIOS like every other unit.
  The image itself is untouched, and a `dm=none` system changes nothing.
- A `BLOCKED: first_login_unproven-*` result is not a product failure. It means
  this probe could not prove a first login (no display manager named, no
  autologin, no session, or no second login boot), and it needs a human to look
  at the guest, not a retry loop.
- The mode costs two boots instead of five (one for Floe), so it is cheap to
  run per candidate image, but it proves nothing about updates or rollback.
- `chairlift` is evidence without a failure code today. When its first-run gate
  must be enforced, that is a new decision, not a change to this one.
- The record is report-only evidence from a desktop session the lane does not
  otherwise authenticate; a started app is a fact about that VM, not proof of
  the product's UX.

## Alternatives considered

- **Add the probe to the five-boot plan:** the plan proves N → N+1 → rollback;
  adding desktop boots would change every existing manifest's behavior and
  lengthen a release-relevant path for a report-only fact.
- **A separate WorkflowTemplate:** it would duplicate the installer, the
  SMBIOS credential plumbing, the serial channel and the evidence pipeline for
  one extra boot mode.
- **Fail the run when no first-run app starts:** that would make a desktop
  behavior a release gate, which this lane explicitly is not (see
  [ADR-0009](0009-no-artifact-store-logs-are-the-surface.md) and
  [ADR-0010](0010-vacuous-success-is-forbidden.md)); `unproven-` says the same
  thing without claiming a product failure.

## References

- Shapes: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — Manual Snow bootc lifecycle evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Builds on: [ADR-0003](0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md),
  [ADR-0014](0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
- Related: [ADR-0012](0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)
