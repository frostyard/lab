# Plan: notesd and maild suite sysexts on an installed Snow (sysext probe)

This plan records the first lab run of the suite sysexts that snosi
[#1169](https://github.com/frostyard/snosi/pull/1169) published
(`repository.frostyard.org/ext/notesd` and `ext/maild`, signed), as Brian
chose it (T377a). It adds an optional `sysext_probe` mode to the
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
lane (field contract in the [README](../../README.md#manual-snow-bootc-lifecycle))
and runs it as a dev run from an unmerged branch (`dev-snow-bootc-lifecycle-*`,
the 2026-10-26 lab exception: no publish, no tag move, no change to the main
WorkflowTemplate or ConfigMap). The probe is advisory and never a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md)).

## Selected inputs

The manifest is
[`manifest.json`](snow-sysext-probe-2026-10-28/manifest.json) (canonical lane
`manifest_sha256` `1ae9f760…e004eb`).

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261028150307_x86-64.iso`, SHA-256 `8fe55110…94b3a7` | The single entry of the signed index at submit time. |
| N | `20261028124727`, `sha256:249386e3…49289a` | `snow:latest`, built from snosi `c62ae5e` (#1169): the first image with the `notesd` and `maild` updex features (`Enabled=false`). |
| N+1 | `20261026133822`, `sha256:008bfa69…2192aa` | The current target of `qa-controlled`. Not staged in this mode; the tag was not moved. |
| Probe | features `notesd`, `maild`; units `frostnotes-lodge.socket`, `frostnotes.service`, `frostmail.socket`, `frostmail.service`; first start `frostmail-secrets-key.service` → `/etc/credstore.encrypted/frostmail-secrets-key` | The task's checks. |

## Phase 1 — Probe mode (this branch)

- `sysext_probe` replaces the five lifecycle boots with `installed-n` (full
  attestation), `sysext-enable` and `sysext-reboot`. `sysext-enable` runs
  `updex features enable <feature> --now` (download, verify, link, refresh)
  per feature, then waits up to 180 s for the units. Each boot publishes one
  `SNOW_SYSEXT_V1` record; the host compares the reboot record with the enable
  record (key bytes, a rerun, account ids).
- The dev submit packs the scripts (gzip, base64): the inline dev template had
  passed Argo's 128 KiB `MaxEnvVarLen`, and the controller cannot create the
  offload ConfigMap here (`clusterrole "argo-workflows-workflow-controller-cluster-template" not found`,
  `dev-snow-bootc-lifecycle-2xxzw`). The main lane mounts its ConfigMap and is
  not affected.
- `dev-snow-bootc-lifecycle-mjhb4` showed a probe defect: the probe unit was
  pulled by `multi-user.target`, a target orders itself after such a unit, so
  the target stayed inactive and its `Upholds=` never started a unit (no
  journal entry for any of the 4 units, in both boots). The probe unit now
  orders `After=multi-user.target`.
- **Done when:** a dev run of this branch reports each check with evidence.
  Met by `dev-snow-bootc-lifecycle-kb7qb` (below).

## Result (2026-10-28): FAILED in the enable boot, PASS after the reboot

`dev-snow-bootc-lifecycle-kb7qb` (branch head `a64bab40`, submitted 15:59Z,
ended 16:13Z). The VM was deleted by the lane's teardown.

```
FAILED: sysext_failed:sysusers_missing:frostmail,sysusers_missing:frostmail:lodge-apps,sysusers_missing:frostnotes,sysusers_missing:frostnotes:lodge-apps,sysusers_missing:lodge,sysusers_missing:lodge-apps,unit_inactive:frostmail.service,unit_inactive:frostmail.socket,unit_inactive:frostnotes-lodge.socket,unit_inactive:frostnotes.service
```

| Check | `sysext-enable` (after `updex features enable --now`) | `sysext-reboot` |
| --- | --- | --- |
| `updex features enable … --now` | rc 0 for both; downloaded `notesd@0.2.0`, `maild@0.2.0`, refreshed | — |
| Merged on `/usr`, link | `maild`, `notesd`; `maild_0.2.0_13_x86-64.raw`, `notesd_0.2.0_13_x86-64.raw` | same |
| sysusers (`frostmail`, `frostnotes`, `lodge`, `lodge-apps`, memberships) | **missing** | present (uid 974, 973; gid 976, 975; both in `lodge-apps`) |
| `frostnotes-lodge.socket`, `frostmail.socket` | **failed** (`start-limit-hit`) | active (running) |
| `frostnotes.service`, `frostmail.service` | **inactive** (dependency failed) | active (running), 0 restarts |
| `frostmail-secrets-key.service` | ran at 29 s: `make-secrets-key: made /etc/credstore.encrypted/frostmail-secrets-key`; file 0600, 1418 bytes | skipped (`ConditionResult=no`); file unchanged (same SHA-256) |

Journal lines of the enable boot (first and last of each unit):

```
snow-qa systemd[1]: Failed to listen on frostnotes-lodge.socket - Frostnotes' lodge app socket.
snow-qa systemd[1]: frostnotes-lodge.socket: Failed with result 'start-limit-hit'.
snow-qa systemd[1]: frostnotes.service: Job frostnotes.service/start failed with result 'dependency'.
snow-qa systemd[1]: frostnotes-lodge.socket: Unit needs to be started because active unit multi-user.target upholds it, but not starting since we tried this too often recently. Will retry later.
snow-qa systemd[1]: Failed to listen on frostmail.socket - Frostmail's lodge app socket.
snow-qa systemd[1]: frostmail.socket: Failed with result 'start-limit-hit'.
snow-qa systemd[1]: Dependency failed for frostmail.service - Frostmail mail engine (maild) as lodge's mail app.
snow-qa systemd[1]: frostmail.service: Unit needs to be started because active unit multi-user.target upholds it, but not starting since we tried this too often recently. Will retry later.
```

**Cause.** The sockets name `SocketUser=` and `SocketGroup=lodge` accounts that
only the sysexts' own `sysusers.d` fragments declare. On a live refresh
(`systemd-sysext refresh` with `EXTENSION_RELOAD_MANAGER=1`) systemd only
reloads the manager; snosi's `reload-sysext.service` (`systemd-sysusers`,
`systemd-tmpfiles`, `daemon-reload`) runs at boot only. So the accounts do not
exist until the next boot, the sockets cannot be created, `Upholds=` retries
until the start limit, and the services fail on the dependency. After the
reboot every check passes. The secrets key does not depend on the accounts and
was made at first start, in the enable boot.

This is a product finding for the live enable path (updex `--now`, or snosi's
refresh hook), not a lane defect. It proves nothing about lodge itself, the app
sockets' clients, Snowfield hardware or release readiness.

## Open questions

- **Live enable of a sysext with its own accounts:** who applies the merged
  `sysusers.d` (and `tmpfiles.d`) after a live refresh — updex after
  `systemd-sysext refresh`, or a snosi hook — and does it also reset the
  units' start limit? Decide in snosi/updex; rerun this probe after the fix.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle)
- Decisions: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)
- snosi: [#1169](https://github.com/frostyard/snosi/pull/1169),
  `mkosi.images/base/mkosi.extra/usr/lib/systemd/system/reload-sysext.service`
