# Spec: Manual bootc lifecycle lane

This document is the field contract of the `run-snow-bootc-lifecycle`
WorkflowTemplate. The lane's run plans and
[ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md),
[ADR-0016](../adr/0016-first-login-probe-is-a-report-only-lane-mode.md),
[ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md),
[ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md) use it.

## Manual Snow bootc lifecycle

[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
is an advisory WorkflowTemplate for the Snow, Floe, Snowfield and Sundog bootc images; it is
not scheduled, not a release gate. The manifest field `product` (`snow`,
`floe`, `snowfield` or `sundog`) selects the image
([ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md));
the template has no separate product parameter, so the product is bound to
the canonical `manifest_sha256`. Floe is a headless guest: the guest probe has
no desktop check for any product, so every product runs the same five boots. The
[Floe run plan](https://github.com/frostyard/lab/blob/cc3e68a4d51b346f05182ab565aa43a0e8ed34af/docs/plans/2026-10-28-floe-bootc-lifecycle-run.md) holds the
Floe inputs and its first main-lane PASS (`run-snow-bootc-lifecycle-v9bkv`,
2026-10-29). Sundog is the KDE Plasma
desktop image: it has the same updater, signature policy rule, cosign key and
Firn ISO as Snow, Floe and Snowfield. The
[Sundog run plan](https://github.com/frostyard/lab/blob/cc3e68a4d51b346f05182ab565aa43a0e8ed34af/docs/plans/2026-10-30-sundog-bootc-lifecycle-run.md) holds its
inputs and its first dev-run PASS (`dev-snow-bootc-lifecycle-rs6sh`,
2026-10-30, from the unmerged lane branch); no main-lane Sundog run yet.
Snowfield is the GNOME image for Microsoft Surface hardware, with the
linux-surface kernel. The
[first Snowfield run plan](https://github.com/frostyard/lab/blob/cc3e68a4d51b346f05182ab565aa43a0e8ed34af/docs/plans/2026-11-03-snowfield-bootc-lifecycle-run.md)
holds the initial N and N+1 inputs and run `dev-snow-bootc-lifecycle-pnljz`
(2026-11-04): `BLOCKED` in `installed-n` because kernel lockdown was `none`
([snosi#1213](https://github.com/frostyard/snosi/issues/1213)). The rerun
[`dev-snow-bootc-lifecycle-v4cgb`](https://github.com/frostyard/lab/blob/cc3e68a4d51b346f05182ab565aa43a0e8ed34af/docs/plans/2026-11-04-snowfield-bootc-lifecycle-run.md)
fixed lockdown by snosi#1214, then ended `FAILED: fsverity_errors` on
linux-surface ([snosi#1130](https://github.com/frostyard/snosi/issues/1130)).
The [snosi#1214 lockdown proof](https://github.com/frostyard/lab/blob/cc3e68a4d51b346f05182ab565aa43a0e8ed34af/docs/plans/2026-11-05-lockdown-1214-lifecycle-runs.md)
updated an N from before #1214 to N+1 and back: Snow `rj697` PASS (bootc
1.17.1 to 1.17.3), Snowfield `dm5bp` `FAILED: fsverity_errors` from a dev-only
lane copy, with `lockdown=integrity` on every N+1 boot.
Snowfield hardware qualification is outside this VM lane.
The lane's first manual main-lane PASS (Snow,
`run-snow-bootc-lifecycle-tbjnp`, 2026-10-26, bootc 1.16.8 N → N+1) is
recorded in the [run plan](https://github.com/frostyard/lab/blob/cc3e68a4d51b346f05182ab565aa43a0e8ed34af/docs/plans/2026-10-26-snow-bootc-lifecycle-run.md#result-2026-10-26-pass). It instantiates the
existing console, concurrency, logging and non-vacuous-evidence decisions in
[ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
[ADR-0007](../adr/0007-cross-workflow-concurrency-via-template-semaphores.md),
[ADR-0009](../adr/0009-no-artifact-store-logs-are-the-surface.md), and
[ADR-0010](../adr/0010-vacuous-success-is-forbidden.md).
Offline mocked tests are not published-media or hardware qualification.

Prepare a local UTF-8 JSON object with **exactly** these keys (no extras).
Every value is public: the submission passes JSON in the `argo` process argv,
and Argo workflow/pod metadata and the pod environment expose `manifest-json`
to readers. After tool provisioning, before `qa.py` runs, the runner writes
the env value to a private 0600 working file and unsets it; this does **not**
make the workflow parameter secret. Never put a password, token, recovery key or other
secret in the manifest, workflow parameters, or submission command.

| JSON key | Required value |
|---|---|
| `family` | Equal to `product`: `snow`, `floe`, `snowfield` or `sundog` (bootc, not snow-ab or floe-ab). |
| `product` | `snow`, `floe`, `snowfield` or `sundog`. It selects the repository `ghcr.io/frostyard/<product>` for every image and tag below, the Firn catalog entry of that name, and the signature-policy entry the guest requires. |
| `iso_url` | Published immutable versioned `snosi-installer_<iso_version>_x86-64.iso` URL under `https://repository.frostyard.org/isos/native/v1/`; never `latest`. |
| `iso_sha256` | Lowercase 64-hex SHA-256 of that ISO, independently checked against the signed ISO index. |
| `iso_version` | 14-digit ISO version matching the URL and extracted release metadata. |
| `image_n` | `ghcr.io/frostyard/<product>@sha256:<64 lowercase hex>` for N. |
| `version_n` | 14-digit OCI image version for N. |
| `version_tag_n` | Immutable `ghcr.io/frostyard/<product>:<version_n>` (trixie) or `ghcr.io/frostyard/<product>:forky-<version_n>` (forky test image, [snosi ADR-0038](https://github.com/frostyard/snosi/blob/main/docs/adr/0038-publish-signed-forky-test-images.md)) mapping to `image_n`. |
| `image_n_plus_1` | Distinct signed `ghcr.io/frostyard/<product>@sha256:<64 lowercase hex>` for N+1. |
| `version_n_plus_1` | 14-digit OCI version newer than N. |
| `version_tag_n_plus_1` | Immutable `ghcr.io/frostyard/<product>:<version_n_plus_1>` or `ghcr.io/frostyard/<product>:forky-<version_n_plus_1>` mapping to `image_n_plus_1`; N and N+1 choose the form independently, so trixie N to forky N+1 passes. |
| `target_ref` | `ghcr.io/frostyard/<product>:<tag>` mapping to N+1: either an operator-owned mutable controlled tag (for example `qa-controlled`), or `version_tag_n_plus_1` itself (an immutable target needs no registry write; the updater pulls whatever `target_ref` resolves to). Never `latest` and never `version_tag_n`. |
| `snosi_commit` | Exact pinned source commit `39cf19887547f83200866e94e5753888706b49fa`. |
| `index_key_sha256` | SHA-256 of public `shared/native-ab/keys/import-pubring.gpg` at that commit. |
| `cosign_key_sha256` | SHA-256 of public `cosign.pub` at that commit. |
| `mok_cert_sha256` | SHA-256 of public `shared/native-ab/keys/mok-2026.crt` at that commit. |
| `trust_fingerprint` | Signed ISO index primary-key fingerprint `F37282A35CB6BDFEBFC8FE775A2EAC5C8216FD68`. |
| `secureboot` | JSON boolean `true`. |
| `encryption` | `tpm2-luks-passphrase`. |
| `timeouts` | Object of integer seconds, each at least 300, with maxima: `iso_boot` 1800, `install` 7200, `installed_boot` 1800, `stage` 3600, `reboot` 1800. |

Before submitting, independently compare the Snosi commit and all three
public key/cert hashes against committed source (not a mutable branch); verify
the ISO checksum with `SHA256SUMS` authenticated by `SHA256SUMS.gpg` and the
pinned fingerprint. That signature covers the **ISO index only**, not the
native A/B update index. Confirm both signed OCI digest references, their
immutable version-tag mappings, and, for a mutable target, **your ownership** of
the nonlatest N+1 target tag; do not race another publisher. An immutable
`version_tag_n_plus_1` target needs no ownership and no tag move. There is deliberately no runnable
placeholder digest or tag. After reviewing kube context, Argo permissions,
manifest content and controlled tag ownership, submit explicitly from a local
file (do not paste the JSON into shell history):

```bash
if [[ -n ${MANIFEST_FILE:-} && -s "$MANIFEST_FILE" ]]; then
  argo submit --namespace argo --from workflowtemplate/run-snow-bootc-lifecycle \
    -p "manifest-json=$(<"$MANIFEST_FILE")"
else
  printf '%s\n' 'Set MANIFEST_FILE to a reviewed nonempty public JSON file' >&2
  false
fi
```

This is a manual submission interface, **not** a command run in this repo.
The optional workflow parameter `keep-vm-on-failure` defaults to `"false"`;
set `-p keep-vm-on-failure=true` only for a deliberate failure investigation.
An initialized VM is then kept on `FAILED` or `BLOCKED` (never on `PASS`)
only if its installer device can be detached before the ISO is removed.
If detach fails, teardown deletes the VM instead and reports `;vm_kept_failed`;
if deletion also fails, the result includes `teardown_failed;vm_kept_failed;vm_left=<vm>`.
On any VM deletion failure, the runner leaves the ISO in the ISO cache to
avoid a dangling installer device and reports `;vm_left=<vm>` for manual
cleanup, even when the keep option is off. The ISO cache path can be
overwritten by a later run; inspect and clean up the leftover VM promptly.
The keep decision follows the first result and evidence writes, so a write
failure there can keep an opted-in VM. A later teardown or working-directory
cleanup failure can change the verdict after deletion; a deleted VM cannot
be recovered, but the final summary records the failure.
For a VM actually kept, the result reason ends in `;vm_kept=<vm>`, including
the VM name even if writing `kept-vm.txt` or evidence persistence fails.
The run submitter owns cleanup with
`incus delete --force <vm>` after inspection; the name and command are recorded
in private `kept-vm.txt` when writable. A kept VM still holds the LUKS passphrase in guest
`/run` and must be deleted after inspection. It no longer holds the
`snosi-vm-qa` semaphore after the workflow ends, but continues to consume the
VM pool. Do not enable this option without arranging cleanup.

Preflight authenticates the ISO and embedded public keys, records Firn provenance
and its hash, verifies signed OCI images with the pinned cosign key and checks
version/tag mapping. Each image must carry one of the listed bootc
secure-assembly labels, `bootc-1.16.8-storage-digest-v1`,
`bootc-1.17.1-storage-digest-v1`, `bootc-1.17.3-storage-digest-v1`, or
`bootc-1.17.4-storage-digest-v1` (forky test images), and
preflight records the observed label for N and N+1, so an N to N+1 run may cross a bootc version. The disposable
installer VM then checks the Firn hash and narrow Firn v2 recipe validation
before install (validator only, not full installed compatibility). Installed
guest checks enforce the shipped signature policy (reject by default; every
registry repository sigstore-signed with the pinned key, the manifest
product's repository exactly so) and
signed pulls; read-only
`skopeo inspect` tag resolution alone is **not** signature verification. Both
immutable version tags are checked once in preflight; the
target tag (mutable or the immutable N+1 tag) is rechecked before install and each phase, and
after stage, N+1 boot and rollback. The lane attempts a secure UEFI
vTPM Firn v2 encrypted-btrfs install, then five distinct fresh boots:
`installed-n`, `stage`, `boot-n-plus-1`, `rollback`, `boot-n`. The guest
contract follows the real Firn installer and the shipped updater (the same
`bootc-update-stage` on Snow, Floe, Snowfield and Sundog). Firn installs with
`--target-imgref`, so every phase must report `target_ref` as the bootc spec
image (never `image_n`): `installed-n` and the pre-`stage` boot must be
transport `registry`; after the updater (`bootc switch --transport
containers-storage`) later phases may report `containers-storage` or
`registry`, and the observed value is kept in the record. Any other transport
fails. `qa.py phase` prints the accepted value on its line 5, and the runner
writes it last on the phase line of `checks.txt`
(`... cmdline_lockdown=<value|absent> spec_transport=<registry|containers-storage>`);
a missing line 5 or a value outside that allow-list fails `phase_mismatch`.
The `stage` line holds the transport after the updater ran; the `rollback`
line holds it before `bootc rollback`. Runs before this field have no
`spec_transport` on their phase lines. The `stage` phase runs `/usr/libexec/bootc-update-stage` and parses
`/run/snosi/update-staged` (`image=`, `digest=`, `staged_at=` lines; bounded,
no duplicate keys) requiring `digest=sha256:<N+1 digest>` and `image=target_ref`.
Every phase boot also injects a drop-in that skips `bootc-update-stage.service`
(`ConditionPathExists=/run/snow-qa-timer-disabled`), so the image's hourly
timer cannot stage N+1 early. After a phase record, and also when `qa.py`
fails, the guest unit runs `systemctl --no-block poweroff` and the runner waits
(bounded by the `reboot` timeout) for the VM to stop, never `--force`, so bootc
finalizes the staged deployment at shutdown; a VM that does not stop fails with
`guest_shutdown`. Each serial write is bounded, and the record also travels
through `/dev/kmsg` at level 3 (err), so it prints on a `quiet` console, as
`SNOW_QA_KMSG1` chunks that the runner joins back
([ADR-0014](../adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md),
[ADR-0019](../adr/0019-kernel-log-copies-use-level-err.md)),
so a stalled ttyS0 costs one boot rather than the phase window. See
[private evidence and verdict limits](../quality.md#manual-snow-bootc-lifecycle-evidence)
before treating any result as evidence.

The optional manifest key `sysext_probe` replaces the five-boot plan with a
three-boot probe of the installed N image: `installed-n` (the full attestation
and fs-verity record), `sysext-enable` and `sysext-reboot`. It is an object
with exactly `features`, `units` and `first_start`: 1-8 unique updex feature
names (`[a-z][a-z0-9-]{0,31}`), 1-16 unique unit names ending in `.service`,
`.socket` or `.mount`, and 0-4 first-start entries with exactly `unit` (a `.service` name)
and `path` (a file below `/etc`, no `..` part). With this key the rule
`version_n < version_n_plus_1` becomes `version_distinct`, because the lane
never stages N+1; N+1 is only the current target of the controlled tag.
`sysext-enable` first saves the existing `/usr/lib/sysusers.d` fragments, then
runs `updex features enable <feature> --now` for each feature and waits up to
180 s for every `units` entry to be `active`; `sysext-reboot` only waits and
re-checks. The wait alone is not enough: a `Type=simple` unit in a crash loop
samples as `active` between two restarts. So every `units` entry must also stay
`active` for a 30 s settle window, with the same `NRestarts` and the same
`ActiveEnterTimestampMonotonic` at both samples, else the code is
`unit_inactive:<unit>` or `unit_restarted:<unit>` (lab#165, see
[ADR 0010](../adr/0010-vacuous-success-is-forbidden.md)).
Load-state evidence (snosi#1189, evidence only: no failure code, no verdict
change, not compared between the two boots): each `units` entry also has
`LoadState` and `NeedDaemonReload`, and the record has one `target` object with
`LoadState`, `NeedDaemonReload` and `Upholds` of `multi-user.target`. Units come
from the second settle sample and `target` is sampled right after it, so both
show the state after the 30 s wait. Every value is text (control characters
removed, at most 240 characters; `Upholds` at most 1024) or `null` when
`systemctl show` gave none; a missing key or another type fails the record
(`record_target`, `record_unit_load`). `systemctl show` can load a unit file on
demand, so `LoadState=loaded` alone does not prove that PID 1 read the new
files: `NeedDaemonReload=yes` on the target, or an `Upholds` list without the
units that the sysext's `multi-user.target.d` drop-in upholds, shows that the
drop-ins were not loaded. Not every probed unit is upheld (a setup unit can
pull one in with `Wants=`), and a list cut at 1024 characters cannot show that
a unit is absent. This evidence never enters the verdict. The pod log prints one
`sysext_state` line per probed unit and one `sysext_target` line per boot, also
when the run passes.
Each boot publishes one `SNOW_SYSEXT_V1` record: merged `/usr` extensions, the
file each `/var/lib/extensions/<feature>.raw` resolves to, unit states, the
first-start file size/mode/sha256, the new sysusers accounts, and a sorted
`failures` list. A failing unit keeps the first 40 journal lines of the boot
and the last 20, so the first cause of a failure survives; truncation drops
tail lines before head lines. The probe unit runs after `multi-user.target` is
active, so its `Upholds=` apply. The runner keeps `sysext-enable.json` and
`sysext-reboot.json` evidence (the reboot record is compared with the enable
record: first-start bytes, a rerun, account ids), prints the failing units'
journal head and tail lines in the pod log, and judges after both sysext boots:
any failure is `sysext_failed:<codes>`, otherwise the reason is
`verified_sysext_probe`. This probe is not a release gate.

The optional manifest key `first_login_probe` replaces the five-boot plan with
a report-only first-login probe: `login-1`, and `login-2` for every product but
Floe (headless). It is an object with exactly `session_wait` (60-900 s) and
`app_wait` (30-600 s), never present with `sysext_probe`. With this key the
version rule is `version_distinct` as for the sysext probe, and the two waits
plus 180 s must fit the smaller of the `installed_boot` and `reboot` timeouts
(else `first_login_window`). There is no `installed-n` attestation boot in this
mode: it proves first login only, and Snowfield's `installed-n` boot is
BLOCKED on kernel lockdown ([snosi#1213](https://github.com/frostyard/snosi/issues/1213)),
which is not a first-login fact. `login-1` alone also runs
`/opt/snow-qa/login-setup.sh` from its own unit: it names the active display
manager (`gdm`/`gdm3`/`sddm`), creates a `snowqa` account, and writes a
test-only autologin configuration into the installed `/etc` (GDM
`daemon.conf`, or SDDM's `zz-snow-qa-autologin.conf` with the first Plasma
session it finds; SDDM reads every file of `/etc/sddm.conf.d` in alphabetical
order and the last one wins, so `zz-` sorts after a drop-in such as
`10-sundog.conf`, and because SDDM reads `/etc/sddm.conf` after that directory
the setup comments out the active `User`, `Session` and `Relogin` keys of its
`[Autologin]` section, keeping the file's mode and never creating it). It
never writes below `/usr` and never touches the image; a system with no GDM or
SDDM records `dm=none` and creates no account. Each boot publishes one
`SNOW_LOGIN_V1` record: display manager, whether the autologin configuration is
still there (SDDM: the drop-in's effective `User` is `snowqa` and
`/etc/sddm.conf` has no active `User`, `Session` or `Relogin` key in
`[Autologin]`; GDM: `[daemon]` has `AutomaticLoginEnable=true` and
`AutomaticLogin=snowqa`; whitespace around `=` and around a section name is
allowed), the session (type `wayland`/`x11`, class `user`, state
`active`/`online`, desktop), the first-run autostart entries it found, which
known apps have an entry and a binary, which apps ran (`first-setup` in two
consecutive process polls; `chairlift` only through its own `first-run: window
presented` journal line, never through a bare `--first-run` process), the
`login-1` mark-done results and the done markers. The runner keeps `login-N.json` and `first-login.txt`
evidence and prints one `first_login=<summary>` line in `checks.txt`. Known
apps today are `first-setup` and `chairlift`. The probe never fails the run
because of which app started: a summary that starts with `unproven-` ends
`BLOCKED` with `first_login_<summary>`
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md)), and every
other summary ends `PASS` with `verified_first_login_probe;first_login=<summary>`
([ADR-0016](../adr/0016-first-login-probe-is-a-report-only-lane-mode.md)).

The optional manifest key `problems_probe` replaces the five-boot plan with a
report-only problems probe of the installed image's snosi-health collector
(snosi ADR-0029) and systemd-coredump. It is an object with exactly
`{"path": "fresh"}` or `{"path": "update"}`, never present with `sysext_probe`
or `first_login_probe`. `fresh` boots the installed N once (phase `problems`)
and uses the `version_distinct` rule, because nothing is staged; `update` makes
the unchanged `installed-n` and `stage` boots, then `problems` as the first
boot of N+1 and `problems-reboot` as the second, so its rule stays
`version_order`. The `problems` boot records the
boot timing (the default target and the `Firmware`, `Loader`, `InitRD`,
`Userspace` and active-enter timestamps, as `systemd-analyze` computes them,
`null` when one cannot be read), the collector binary, the
`snosi-health.timer` state, `preset-reconcile.service`'s `Result`, the
`systemd-coredump` account, the persistent journal directory and the
`core_pattern` pipe. It then injects 4 problems that are local to the throwaway
VM: a oneshot unit in the installed `/etc` that fails (a local unit, so its name
stays out of `problems.json` and its problems entry is not reportable),
`bootc-update-stage` inside a network namespace with no route, a child killed
with `SIGSEGV` for systemd-coredump, and one app problem datagram in the
journal's native protocol. It starts the collector twice and reads
`problems.json`, `details.json` and `snosi-health --list`. The host validates the
record against a pinned copy of core's `problem-report/v1` schema and prints one
`problems=<summary>` line in `checks.txt` and `problems.txt`: `ok`,
`unproven-no-collector`, `unproven-too-large`, or `missing-` plus the failed
checks (`file`, `schema`, `timer`, `coredump`, `journal`, `details`, `unit`,
`stage`, `crash`, `app`, and `list` on Floe). `preset-reconcile` starts a timer
that an update adds only on the next boot
([snosi#1229](https://github.com/frostyard/snosi/issues/1229)), so on the
`update` path the `timer` check accepts `enabled/inactive` on the first N+1 boot
and requires `enabled/active` on the `problems-reboot` boot, which only reads
the static facts and injects nothing; the `fresh` path requires
`enabled/active` on its one boot. A summary that starts with
`unproven-` ends `BLOCKED` with `problems_<summary>`
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md)); every other
summary ends `PASS` with `verified_problems_probe;problems=<summary>`. The probe
never fails the run for a product finding
([ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md)).

The optional manifest key `report_probe` replaces the five-boot plan with a
report-only report probe of the installed image's problem-report sender. It is
an object with exactly `{"intake": "lab"}` or `{"intake": "production"}`, never
present with `sysext_probe`, `first_login_probe` or `problems_probe`, and valid
for all 4 products. On Floe the sender is the snosi sender (snosi ADR-0030); on
Snow, Snowfield and Sundog it is chairlift v0.15.0's
`chairlift --send-pending-reports`, run in the test-only user's own session,
which the boot gets from the first-login probe's autologin setup unit. It never
stages N+1, so its rule is `version_distinct`, its budget is `installed_boot`,
and its plan is the one `report` boot of the installed N. Its guest code travels
in its own `snow-report.z` credential, because it does not fit in `qa.py`. With
`intake: lab` the run first creates a throwaway Incus **container** with the real
frostyard/problem-intake Worker under `wrangler dev` and a mock GlitchTip, on the
container's own address, with a proxy that adds the 3 headers a Cloudflare edge
adds; the run starts and deletes that container through the mounted Incus socket
and never keeps it, because it holds the run's random bearer. The guest marks
`off-begin`, injects 1 app problem and 1 crash, runs the collector twice, and
proves the off state: no `/etc/snosi-health` before the consent change, a
sender start that its condition skips, a `send` that answers "problem reporting
is off", no `sent.jsonl`, and what `send --dry-run` would send. It writes the
`endpoint=` line of `reporting.conf` as an administrator would, marks `on-begin`,
turns reporting on (which must keep that endpoint), restarts the timer and waits,
bounded, for 1 send, marks `on-end`, and reads the sender's own `sent.jsonl`. The
run pulls the intake's 2 JSON line logs as evidence. With `intake: production`
there is no container and no consent change: the guest sends exactly 1 lab test
report (`app_problem`, code `lab.injected_t857`) with `curl`, over `https` only.
On a desktop the record proves chairlift instead: the probe waits, bounded, for
the test-only user's session, writes the lab endpoint into
`/etc/chairlift/config.yml` as an administrator would, runs the sender with no
consent file and then with a stored off consent, turns the consent on and runs it
once more, and reads chairlift's own
`~/.local/state/chairlift/reports.json`. Its `intake: production` path takes the
1 injected document from `chairlift --send-pending-reports --dry-run`, which
sends nothing and writes no state, and posts it with `curl`. The host validates
the record and the intake's logs and prints one `report=<summary>` line in
`checks.txt` and `report.txt`: `ok`, `unproven-no-sender`, `unproven-session`
(a desktop with no session), `unproven-intake`, `production-accepted`,
`missing-production`, or `missing-` plus the failed checks (`off`, `timer`,
`consent` on Floe; `off` and `cli` on a desktop; `count`, `app`, `crash`,
`schema`, `ip`, `forward` and `match` on both). A
summary that starts with `unproven-` ends `BLOCKED` with `report_<summary>`
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md)); every other
summary ends `PASS` with `verified_report_probe;report=<summary>`. The run's
bearer and DSN key are random per run and never leave the lab container
([ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md)).

On failure, private evidence may also include `install-diagnostic.txt` (a
bounded, redacted installer diagnostic or `unavailable`/`unparsed`) and
`serial-redacted.log` (best-effort, bounded redacted console snapshot; never
raw serial). `kept-vm.txt` exists only for an initialized VM deliberately kept
on a non-PASS result. No recovery material belongs in these evidence files.

To test a lane fix before it merges, run `scripts/snow-lifecycle-dev-submit.sh`
(see the dev-run rule in [AGENTS.md](../../AGENTS.md)): it renders one
`dev-snow-bootc-lifecycle-*` Workflow from the current checkout, with the
scripts as input artifacts instead of the ConfigMap, and `--print` only
renders. A dev run publishes nothing, moves no tag and changes neither the
main WorkflowTemplate nor its ConfigMap.

For the firn install lane the same rule applies with
`scripts/firn-install-dev-submit.sh NAME=VALUE...`: it renders one
`dev-firn-install-*` Workflow from the current checkout with the branch's
`run-firn-install-tests` templates inline, and takes the cell's input
parameters (`iso-url`, `iso-sha256`, `image-digest` are required). `--print`
only renders.

## References

- Rationale: [ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md),
  [ADR-0016](../adr/0016-first-login-probe-is-a-report-only-lane-mode.md),
  [ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md),
  [ADR-0018](../adr/0018-report-probe-is-a-report-only-lane-mode.md)
- Shapes: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0007](../adr/0007-cross-workflow-concurrency-via-template-semaphores.md),
  [ADR-0009](../adr/0009-no-artifact-store-logs-are-the-surface.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md),
  [ADR-0014](../adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md)
- Design: [design/architecture.md](../design/architecture.md),
  [design/vm-lanes.md](../design/vm-lanes.md)
- Evidence limits: [quality.md](../quality.md#manual-snow-bootc-lifecycle-evidence)
