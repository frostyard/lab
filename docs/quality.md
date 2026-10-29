# Quality dashboard

This page brings the repository's quality evidence into one place. A green
badge means the latest `main` run of that workflow passed. Some specialized
workflows are path-filtered, so a green badge is evidence about the files and
commit that triggered it, not a blanket statement that every part of the
repository was checked. The main CI workflow is unfiltered and runs its Python
and site matrices on every pull request. For a pull request, use that pull
request's checks rather than these default-branch badges.

## Live signals

| Signal | Status | What it establishes |
|---|---|---|
| Reporting site end to end | [![e2e status][e2e-badge]][e2e-workflow] | Playwright builds the reporting site and checks the rendered dashboard against the committed run data. |
| Repository CI | [![CI status][ci-badge]][ci-workflow] | Python 3.12/3.13 run every pytest contract and enforce production Python coverage, including Kubernetes 1.36 manifest schemas, Argo wiring, workload resource bounds, and the deny-by-default agent-governance policy; Node.js 22 runs the reporting-site unit suite. |
| Reporting site deployment | [![pages status][pages-badge]][pages-workflow] | The site unit tests and Astro build passed before the current GitHub Pages deployment. |
| Advisory AI review | [![Claude review status][claude-review-badge]][claude-review-workflow] | For eligible same-repository pull requests, Claude checked the diff against the repository review and security guidance; its comments still require human verification. |

The operational QA dashboard at <https://frostyard.github.io/lab/> is a
different signal: it reports the images and QA lanes exercised by Argo. It
does not report whether this repository's code passed the checks above. Its
`generated` time is the age of the committed snapshot, not a live cluster
timestamp; a successful unchanged-digest poll establishes registry polling,
not image QA. Product QA requires a recorded Behave outcome, while manual VM
and installer workflows provide separate, non-continuous evidence.

## Triaging product QA evidence

1. Start with the committed `site/src/data/runs.json`: note `generated`, exact
   workflow `name`, `phase`, `started`/`finished`, `result`, `digest`, and
   whether this is a scheduled
   `image-poll-{snow,floe,snowfield,sundog}-latest-*` or a manual VM run.
   Per-product `laneKey` and `qaOutcome` (`passed`, `failed`, `carried`,
   `not-run`, `unknown`) separate products and distinguish QA from poll
   execution. The current roster is every product snosi publishes: snow, floe,
   snowfield, sundog. Older entries may instead share one `image-poller` lane;
   identify their product by workflow name, never by the mixed lane's latest
   run. A record without `qaOutcome` remains legacy unknown despite suggestive
   `result` text. A Failed phase with zero observed scenarios is also unknown;
   empty container `checks` are not per-step coverage.
   `digest` is the poll's remote sha256 digest, or `null` when the registry
   returned no digest (the `none` sentinel) or the value is not a full digest.
   `carried` means phase `Succeeded`, no Behave result, `changed: "false"`, a
   valid `digest`, and `stored-digest` equal to it. Because image-poller stores
   a digest only after QA succeeds (ADR-0002), that digest passed earlier. A
   carried run without that proof is `not-run`. On a product lane, `qaRun`
   names the newest retained passed run with the same digest as the lane's
   latest run, as `{name, finished, result}`, or `null` when none is retained.
2. Interpret the snapshot as retained, bounded evidence, not live status:
   `generated` is its collection time, not proof of a current image result.
   Two freshness checks apply. Compare each product's own latest retained poll
   time with `generated`; evidence older than six hours is stale (an old
   confirmed failure remains failed but is not a current image diagnosis; an
   old pass or carried pass is not green). Separately, the page warns when the
   snapshot is missing, invalid, more than five minutes in the future, or older
   than two hours by the viewer's clock: the lab or the publisher stopped.
   `Succeeded` can mean an unchanged digest: it is `carried` with the stored
   digest proof, and `not-run` without it. `qaOutcome: passed`/`failed` records
   observed Behave evidence; `not-run`/`unknown` and absent or expired records
   are not observed failures or passes. A missing later run does not prove
   polling stopped. Summary `result` text cannot identify the cause or exact
   failing step. New snapshots record the digest; older snapshots may omit it,
   and no snapshot records the testsuite commit.
3. For a workflow whose original evidence is available, record its exact name
   and phase; use `check-digest`/`run-pipeline` to determine whether QA ran or
   was skipped. Take the pinned `image-digest` passed to `run-pipeline` from
   Argo parameters or the runner's `Testing pinned digest` log (not the current
   `latest` tag). Record the testsuite commit SHA from the clone log **if
   present**; otherwise mark it `unknown` (a branch name is not a commit).
   For an observed Behave failure, capture the exact failing scenario and step
   from the runner log, not just the summary. Poll workflow retention is 7 days
   for success and 30 days for failure (see poll manifests); distinguish an
   observed failure from an absent or expired workflow and report missing
   details without inferring a cause. Route reproduced product findings to the
   relevant snosi/firn owner rather than changing those repos here.

## Bootc-only ISO install evidence

The [manual submit file](../argo/bootc-only-iso-install-test.yaml) runs on
minideb (10.0.1.175), never selfie. It covers snow, floe and sundog bootc
with `encryption=none` and `secureboot=true`; Snowfield is untested by
decision. The broader [firn matrix](../argo/firn-install-test.yaml) retains
other bootc cells but no A/B family: A/B is removed from this runner's scope.
Each run needs the immutable ISO URL and its lowercase SHA-256 checked against
the signed ISO index, and a product-specific image digest; placeholders are
not evidence and must be replaced before submission. The cells share the
`snosi-vm-qa` semaphore, so installs do not contend for the VM host.

A passing cell demonstrates a fresh install to a blank virtual disk, the
Secure Boot MOK chain through installed boot, first boot with a login prompt
ready (display manager, tty1 getty or serial getty) after multi-user startup,
a btrfs root (directly, or a composefs overlay backed by a btrfs
`/sysroot`), and the installed bootc ref and digest.
`FAILED: <step>:<code> (<cell>) firn=<version>` identifies the failing stage;
`PASS: <cell> image=<ref> digest=<digest> firn=<version>` names the cell,
image ref and digest. The trailing version comes from `firn --version` in the
installer VM; `unknown` means it booted but printed no version, and
`not_reached` means the run failed before installer boot. It is recorded, not
judged. The guest check unit writes each `FIRN_QA__CHECK` line to
`/run/firn-qa-checks.txt` and to a fresh `/dev/ttyS0` open per line, then
replays the file plus `FIRN_QA__CHECKS_DONE` twice after serial-getty has set
up the TTY, because its `TTYVHangup` revokes older serial fds
([snosi#1123](https://github.com/frostyard/snosi/issues/1123)). Every line
also goes to the guest kernel log (`/dev/kmsg`, 1 open per line, before the
ttyS0 write), and every ttyS0 write is bounded by `timeout -k 2 5`, so results still
reach the host when the guest's ttyS0 stops transmitting
([ADR-0013](adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md)).
A `serial_tty` check probes ttyS0 with `tcdrain`; `serial_tty=stalled` fails the
cell as `boot:serial_tty_stalled` (`boot:serial_check_missing` and
`boot:serial_tty_unknown` cover a missing or odd value), with
`FIRN_QA__DIAG` lines in the console log as evidence. The host takes a marker only
as a complete record (the value ends the line, after `\r` is stripped), so a
line that kernel printk cut into is dropped; a key with 2 different complete
values fails the cell as `boot:checks_ambiguous` before any check is judged. Neither a passing VM nor offline contract
tests qualify hardware, graphical login, encrypted unlock or upgrades. No
run or pass is claimed by the submit file alone.

## fs-verity regression checks (snosi#1130)

Debian kernels 7.2.11 and 7.2.12 have a btrfs defect: on fs-verity files,
readahead verifies a whole large folio before all of it is read. The kernel
logs `fs-verity (...): FILE CORRUPTED! ... level=-1`. A synchronous re-read
then succeeds, so no bad bytes reach user space; it is log noise with a real
cause, not data corruption. The upstream fix is
[kdave/linux `dcf8811f63af`](https://github.com/kdave/linux/commit/dcf8811f63af)
("btrfs: fix the fsverity callback where unexpected range is verified"); it is
not yet in a Debian kernel. See
[snosi#1130](https://github.com/frostyard/snosi/issues/1130).

How often it hits depends on the extents:

- **Compressed extents** hit it on almost every extent. The trigger in our
  stack was Firn mounting the Btrfs install target with `compress=zstd:1`, so
  the install-time composefs objects in `/sysroot/composefs/objects/` were
  compressed ([firn#119](https://github.com/frostyard/firn/issues/119) removes
  that). That count is deterministic, so it is judged without exception.
- **Uncompressed files** hit it rarely: when a large folio spans two bios at
  an extent boundary. Update-lab evidence on 7.2.11: a plain
  `bootc install to-disk` Snow VM logged 1 `FILE CORRUPTED` at 8 of 10 boots
  (gnome-shell mmap of `libz3.so.4`), and plain Floe logged 1 error in a cold
  full read. Objects from `bootc install to-disk` or from updates are not
  compressed, so on these kernels errors still appear, from the known upstream
  defect and not from a regression.

So `fsverity_errors` is judged against a **known-defect kernel list**, the same
in every template that judges it (`known_defect_kernels` in the Firn judge, the
bootc install judge and the lifecycle `run.sh`): `7.2.11+deb14-amd64` and
`7.2.12+deb14-amd64`. A missing or non-numeric count always fails. A count of 0
passes. A count above 0 on a listed kernel is recorded with a visible note
(`fsverity_known_defect=<kernel>` in the checks and in the summary line) and
does not fail. The exemption needs exactly 1 distinct, well-formed `kernel=`
value (`[0-9A-Za-z._+~-]{1,64}`, identical duplicates are fine), compared with
each list entry by exact equality. Conflicting, malformed or missing kernel
values with a count above 0 fail with `fsverity_kernel_ambiguous`. A count above
0 on any other single kernel fails: a human then checks whether that kernel has
the fix. **Remove an entry
from the list** when the kernel canary says `clean` on that kernel or when a
fixed kernel ships. The lanes only read kernel logs: nothing deletes or filters
them.

| Check | Where | Meaning | Judged? |
|---|---|---|---|
| `cfs_compressed_objects=<N>` | Firn install probe | Regular files under `/sysroot/composefs/objects` with at least one `encoded` extent in `filefrag -v`. `n/a` = `findmnt` succeeded and `/sysroot` is not btrfs; `unknown` = `findmnt` failed, or `filefrag` or the directory is missing. | **FAIL** when N > 0. Also FAIL when the value is absent. On an attested composefs root (`rootfs=overlay` and `sysroot=btrfs`) the value must be the number 0: `n/a`, `unknown` or a malformed value fails (a guard that did not run is not a pass, [ADR-0010](adr/0010-vacuous-success-is-forbidden.md)). |
| `fsverity_errors=<N>` | Firn install probe, bootc install probe (agent and console channels), lifecycle guest record | Count of `FILE CORRUPTED` lines in `journalctl -q -k -b`, plus the numbers in `fsverity_msg: N callbacks suppressed` lines. Empty output or only `-- No entries --` (exit 0, no kernel entry read) gives `unknown` (`fsverity_log` in `qa.py`), never 0. | Missing or non-numeric: **FAIL** in all three lanes (bootc install: `FAILED: fs-verity guard did not report (snosi#1130)`; with both channels the largest number counts). N > 0: **FAIL** unless the kernel is on the known-defect list (see above). |
| `kernel=<uname -r>` | All three lanes (the lifecycle record has an attested `kernel` field) | Kernel release the count belongs to. | Used to judge `fsverity_errors`. |
| `kernel_btrfs_verity_canary=defect\|clean\|skipped` | Firn install probe only | Informational canary, **never fails a lane**. See below. | No |

The bootc install lane also widened its console and agent key patterns from
`[a-z]+` to `[a-z_]+`: before, any check key with an underscore was silently
dropped.

**Kernel canary.** After the error count (so its own log lines never count),
the Firn probe, as root in the disposable guest, makes a 64 MiB Btrfs image
under `/var/tmp`, mounts it with `-o compress-force=zstd`, writes a 32 MiB file
of `base64` random data, enables fs-verity (`fsverity enable --hash-alg=sha512
--block-size=4096` when the tool exists, else `FS_IOC_ENABLE_VERITY` from
`python3`), drops the page cache and reads the file back. New `FILE CORRUPTED`
lines in `dmesg` give `defect`; none gives `clean`; any setup failure, a
missing tool, a non-root run, a timeout (60 s) or a file whose extents are not
compressed gives `skipped`. The mount, loop device and image are always
removed. The canary is a property of the **kernel**, not of the image: when it
first says `clean` on a new kernel, the hosts can take that kernel, and that
kernel leaves the known-defect list (compressed files hit the defect almost every
time, so `clean` is strong evidence of a fix).

**Lifecycle.** Each guest boot adds `fsverity_errors` and `kernel` (read from
`/proc/sys/kernel/osrelease`) to its attested `SNOW_QA_V1` record, with the
count read after the stage or rollback action so the whole boot is counted.
`checks.txt` lines become
`phase=<phase> boot_id=<id> kernel=<release> fsverity_errors=<N>`. The runner
judges after all five boots were recorded, so a failure keeps every phase's
evidence: it fails with reason `fsverity_errors` when any phase has errors on a
kernel that is not on the known-defect list. Errors only on listed kernels pass
with `fsverity_known_defect=<kernels>` in `checks.txt` and in the PASS reason.
The installed system comes from the pinned Firn ISO: an ISO that still has the
`compress=zstd:1` mount (before firn#119) makes errors near certain on an
affected kernel. The lane is advisory and never a release gate.

## Manual Snow bootc lifecycle evidence

The [manual Snow bootc WorkflowTemplate](../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
accepts explicit public `manifest-json` and optional `keep-vm-on-failure`
(default `false`); the exact fields, trust pins,
operator-controlled N+1 target tag and submission interface are in the
[README](../README.md#manual-snow-bootc-lifecycle). It is not scheduled, not a
release gate. Its first manual main-lane PASS (`run-snow-bootc-lifecycle-tbjnp`,
2026-10-26) is recorded in the
[run plan](plans/2026-10-26-snow-bootc-lifecycle-run.md#result-2026-10-26-pass). The manifest field
`product` (`snow` or `floe`) selects the image
([ADR-0012](adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)); the result line
and `checks.txt` do not name it, so read a result with its `manifest_sha256`.
Floe has offline contract tests and a
[run plan](plans/2026-10-28-floe-bootc-lifecycle-run.md), but no run yet: there is no Floe
lifecycle evidence. Offline mocked tests cannot
establish published ISO/OCI availability or Snowfield hardware qualification.
Submission includes the JSON in the `argo` process argv; workflow/pod parameters
and env are visible: no secrets in the manifest. After tool provisioning,
before `qa.py`, the runner writes it to a private 0600 file, but this is not a
metadata secrecy boundary.

Each workflow gets a private 0700 host directory under
`/var/lib/snosi-lab/snow-bootc-evidence/<workflow-name>/` containing sanitized
0600 `manifest-sha256.txt`, `preflight.json`, `checks.json`, `checks.txt`, and
`result-summary.txt` as far as execution reached. Failed installs may add
`install-diagnostic.txt`: bounded redacted diagnostic text, or `unavailable`
or `unparsed` when it cannot be safely decoded. Non-PASS teardown attempts a
final console capture and may add a bounded, pattern-redacted
`serial-redacted.log` with diagnostic payloads elided; this is best-effort,
not a complete or raw serial record. With explicit `keep-vm-on-failure=true`,
an initialized non-PASS run may add `kept-vm.txt` with the VM name and submitter
cleanup command, but only after its installer device is detached. If detach
fails, teardown deletes the VM and records `;vm_kept_failed` (plus
`teardown_failed;vm_left=<vm>` if deletion fails). On any VM deletion failure,
the runner retains the ISO in the ISO cache and reports `;vm_left=<vm>`;
the cached ISO may be overwritten by a subsequent run, so inspect and clean
up the leftover VM promptly. A retained VM's result reason ends in
`;vm_kept=<vm>` so its name remains discoverable if `kept-vm.txt` cannot be
written or evidence persistence fails. The submitter must
delete that VM after inspection: guest `/run` still holds the LUKS passphrase,
and the VM consumes the pool despite releasing the workflow semaphore.
The install diagnostic redacts known secret literals and patterns and fails
closed if it cannot safely classify them. The serial snapshot is only
pattern-redacted, best-effort, and relies on Firn output never reaching the
console; neither file is a blanket guarantee against arbitrary secret text.
Preflight checks the signed
ISO index against the pinned fingerprint and records the ISO hash, key hashes,
verified N/N+1 OCI digests, version mappings, the observed
`io.snosi.bootc.secureboot-assembly` label of each image, and the extracted
Firn binary's hash and provenance. The assembly label must be one of the listed
`bootc-1.16.8-storage-digest-v1` or `bootc-1.17.1-storage-digest-v1`, so an N to
N+1 run may cross a bootc version. The narrow Firn v2 recipe validator runs
inside the disposable installer VM before `firn install`, after checking the
guest Firn hash against preflight. This validator check alone does **not** prove
full Firn installed compatibility. Before install, real-recipe validation reports
an allowlisted Firn issue code or `unclassified`, never its diagnostic text. The ISO `SHA256SUMS.gpg`
authenticates the ISO index only, not an A/B update index. Registry tag
resolution is read-only (`skopeo inspect`), not a signature check: cosign
verification and the guest policy check (the shipped snosi policy: reject by
default, every registry repository sigstore-signed with the pinned key, only
local transports open) plus signed pull checks are separate. On a composefs
boot `/` is an overlay, so the btrfs root check reads `/sysroot`.
`checks.json` records the preflight checks of both immutable version tags and
controlled target-tag rechecks before install and each phase and after stage,
N+1 boot and rollback; `checks.txt` records manifest hash, target-tag check
timestamps and fresh boot IDs for `installed-n`, `stage`, `boot-n-plus-1`,
`rollback`, `boot-n`. Workflow outputs `result` and `checks` are non-secret
sanitized summaries; a missing file/check is not a pass. Each stopped VM's
bounded console snapshot, taken after the guest powers itself off, becomes the
byte-exact baseline before its next start;
only newly appended bytes are checked for that boot's nonce and record.
Incus 7 starts a new console log whenever QEMU starts: at each instance start
and at a guest reboot inside a phase. A capture that does not contain the
previous boot's log (kept unchanged for the whole boot) is such a new log and
is searched whole (the fresh nonce and boot ID still bind the record). The
searched bytes must never contain that previous log: a changed log that still
carries it cannot be split safely and makes lineage ambiguous (`BLOCKED`), not
a fresh proof. The guest writes its one record line (or `SNOW_QA_ERR` code) to a fresh
`/dev/ttyS0` handle, because `/dev/console` is the graphical tty0 on Snow, and
writes it again once serial-getty's agetty runs; the runner treats identical
copies as one line. Incus keeps console output only when the host reads it,
so the guest repeats the line for 30 s before it powers off; a guest that
powers off without a captured record ends the phase at once as
`BLOCKED: guest_stopped_without_record`. Limit: a guest error printed before such a restart and not yet
seen by the runner is lost with the old log; a later attempt in the same phase
must then still produce a complete, valid record, so a phase result means the
final attempt passed, not every attempt. The full snapshots and per-boot slices are transient only. Raw
serial console, raw installer traces, passwords, recovery material and NVRAM
are never retained as evidence or output parameters; transient raw serial is
deleted. Redacted diagnostics and serial may be incomplete and must not be
treated as a secret-safe substitute for inspecting the retained VM.

`PASS` requires every preflight, tag, install, fresh-boot, signature-policy,
state/action and cleanup check. The guest contract is the one the real Firn
installer and Snow updater produce: the bootc spec image is `target_ref` in
every phase (Firn `--target-imgref`), with transport `registry` for
`installed-n` and the `stage` pre-action check, and `registry` or
`containers-storage` afterwards (the updater switches to `containers-storage`;
the record keeps the observed `spec_transport`, and any other transport
fails `target_transport`). `/run/snosi/update-staged` must carry
`digest=sha256:<N+1 digest>` and `image=<target_ref>` (`stage_marker`). Phases
end by a guest `systemctl --no-block poweroff` so bootc finalizes the staged
deployment; the runner never force-stops between phases and fails
`guest_shutdown` if the VM does not stop within the `reboot` timeout. A
per-boot drop-in condition keeps the image's hourly update timer from staging
N+1 early. The composefs spec rule for the staged and booted deployments has no
live proof here. `BLOCKED` is nonzero for unavailable publication,
host support or tools, or for a missing/unreadable/oversize serial capture or
timeout with no authenticated phase record, or an observed bounded `SNOW_QA_ERR`
guest probe/tool code (not itself proof of a product failure). Observed `SNOW_QA_ERR`
updater or rollback action failures are `FAILED: guest_action_failed`, not `BLOCKED`.
An absent record due to a guest hang or installer timeout without an explicit
failure marker remains `BLOCKED`: investigate it rather
than assuming either a product failure or a pass. `FAILED` is nonzero for a
present but malformed or contradictory `SNOW_QA_V1` record, an explicit
`SNOW_INSTALL_FAILED` marker, another observed mismatch/action failure or
cleanup failure. A capture error cannot validate a partial serial snapshot;
if cleanup fails after a `BLOCKED` result, the verdict changes to `FAILED:
cleanup_after_blocked:<original-reason>;<cleanup-reason>` so neither failure is
lost.
The reported Firn step is a listed start-event step, `run` for Firn run-level
failures (empty step), `unlisted`, or `unknown` when the stream cannot be safely
classified. Without Python the shell fallback recognizes a strictly flat,
compact start event and its listed steps; malformed, nested or noncompact
start events cannot establish a step and report `unknown`. An empty stream reports
`unknown:empty_stream` and an incomplete stream reports `unknown:stream_truncated`.
Neither is a passing lane. The host-side MOK varstore stand-in is **not** human
enrollment. An untested SMBIOS injection/serial channel is not a passing lane;
one real Incus/cluster/registry run passed (see the run plan). A VM `PASS`
proves only this bounded N/N+1 Snow bootc path: not recovery, key rotation,
reconciliation, Snowfield hardware or release readiness. Interpret alongside
the [console marker](adr/0005-console-marker-protocol-for-agentless-guests.md),
[semaphore](adr/0007-cross-workflow-concurrency-via-template-semaphores.md),
[private evidence limitation](adr/0009-no-artifact-store-logs-are-the-surface.md),
and [non-vacuous success](adr/0010-vacuous-success-is-forbidden.md) decisions.

## Evidence expected by change

| Changed area | Expected evidence |
|---|---|
| `site/`, `e2e/`, or Playwright configuration | `just site-e2e`; run `cd site && npm test` when changing the API/data helpers. |
| `scripts/`, `tests/`, or `policies/` | `python -m pytest -q`; for governance changes also run `python3 policies/check_agent_governance.py`. |
| `argo/`, `manifests/`, `argocd/`, or Kubernetes resources | `python -m pytest -q` for offline schema and cross-resource contracts; also run `argo lint` where applicable and `just validate` against a configured cluster. |
| Documentation | Commands and links resolve; present-tense lane claims agree with the committed run snapshot and `README.md`. August statements in `docs/roadmap.md` are historical. |
| Every pull request | Follow the [contributing guide](../CONTRIBUTING.md) and record the relevant result in the pull request's Testing section. Same-repository, non-draft pull requests also receive the advisory [Claude review](claude-code-review.md). |

## Known gaps

- The E2E workflow runs on pull requests only when its path filters match. The
  unfiltered CI workflow runs repository unit, policy, and offline manifest
  tests on every pull request.
- CI rejects malformed or duplicate-key YAML, strictly validates built-in
  resources against Kubernetes 1.36 schemas, and checks Argo references,
  Argo CD ownership, workload CPU/memory bounds, RBAC, and image-poller state.
  It cannot execute Argo CRD
  admission or cluster-specific policy without cluster credentials, so
  `argo lint` and `just validate` remain additional review evidence for
  manifest changes.
- The Pages workflow runs after changes reach `main`; it is deployment
  evidence, not a pre-merge check.
- Claude review requires the `ANTHROPIC_API_KEY` repository secret and skips
  fork pull requests. It is advisory evidence, not approval or a required
  replacement for deterministic checks and maintainer review.

These gaps must stay visible until a workflow actually closes them. Adding a
new check should update both the live-signals table and the change-area mapping
above.

[e2e-badge]: https://github.com/frostyard/lab/actions/workflows/e2e.yml/badge.svg?branch=main
[e2e-workflow]: https://github.com/frostyard/lab/actions/workflows/e2e.yml
[ci-badge]: https://github.com/frostyard/lab/actions/workflows/ci.yml/badge.svg?branch=main
[ci-workflow]: https://github.com/frostyard/lab/actions/workflows/ci.yml
[pages-badge]: https://github.com/frostyard/lab/actions/workflows/pages.yml/badge.svg?branch=main
[pages-workflow]: https://github.com/frostyard/lab/actions/workflows/pages.yml
[claude-review-badge]: https://github.com/frostyard/lab/actions/workflows/claude-code-review.yml/badge.svg?branch=main
[claude-review-workflow]: https://github.com/frostyard/lab/actions/workflows/claude-code-review.yml
