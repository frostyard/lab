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
   workflow `name`, `phase`, `started`/`finished`, `result`, and whether this is
   a scheduled `image-poll-{snow,floe,snowfield}-latest-*` or a manual VM run.
   Per-product `laneKey` and `qaOutcome` (`passed`, `failed`, `not-run`,
   `unknown`) separate products and distinguish QA from poll execution.
   Older entries may instead share one `image-poller` lane; identify their
   product by workflow name, never by the mixed lane's latest run. A record
   without `qaOutcome` remains legacy unknown despite suggestive `result` text.
   A Failed phase with zero observed scenarios is also unknown; empty container
   `checks` are not per-step coverage.
2. Interpret the snapshot as retained, bounded evidence, not live status:
   `generated` is its collection time, not proof of a current image result.
   Compare each product's own latest retained poll time with `generated`;
   evidence older than six hours is stale (an old confirmed failure remains
   failed but is not a current image diagnosis). `Succeeded` can mean an
   unchanged digest and QA not run. `qaOutcome: passed`/`failed` records observed
   Behave evidence; `not-run`/`unknown` and absent or expired records are not
   observed failures or passes. A missing later run does not prove polling
   stopped. Summary `result` text cannot identify the cause or exact failing
   step, and the snapshot may omit the digest and testsuite commit.
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

## Manual Snow bootc lifecycle evidence

The [manual Snow bootc WorkflowTemplate](../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
accepts explicit public `manifest-json` and optional `keep-vm-on-failure`
(default `false`); the exact fields, trust pins,
operator-controlled N+1 target tag and submission interface are in the
[README](../README.md#manual-snow-bootc-lifecycle). It is not scheduled, not a
release gate, and has no live pass documented. Offline mocked tests cannot
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
verified N/N+1 OCI digests, version mappings, and the extracted Firn binary's
hash and provenance. The narrow Firn v1/v2 recipe validator runs inside the
disposable installer VM before `firn install`, after checking the guest Firn
hash against preflight. This validator check alone does **not** prove full
Firn installed compatibility. Before install, real-recipe validation reports
an allowlisted Firn issue code or `unclassified`, never its diagnostic text. The ISO `SHA256SUMS.gpg`
authenticates the ISO index only, not an A/B update index. Registry tag
resolution is read-only (`skopeo inspect`), not a signature check: cosign
verification and exact guest policy plus signed pull checks are separate.
`checks.json` records the preflight checks of both immutable version tags and
controlled target-tag rechecks before install and each phase and after stage,
N+1 boot and rollback; `checks.txt` records manifest hash, target-tag check
timestamps and fresh boot IDs for `installed-n`, `stage`, `boot-n-plus-1`,
`rollback`, `boot-n`. Workflow outputs `result` and `checks` are non-secret
sanitized summaries; a missing file/check is not a pass. Each stopped VM's
bounded console snapshot becomes the byte-exact baseline before its next start;
only newly appended bytes are checked for that boot's nonce and record. A
truncated, reset or changed prefix makes lineage ambiguous (`BLOCKED`), not
a fresh proof. The full snapshots and per-boot slices are transient only. Raw
serial console, raw installer traces, passwords, recovery material and NVRAM
are never retained as evidence or output parameters; transient raw serial is
deleted. Redacted diagnostics and serial may be incomplete and must not be
treated as a secret-safe substitute for inspecting the retained VM.

`PASS` requires every preflight, tag, install, fresh-boot, signature-policy,
state/action and cleanup check. `BLOCKED` is nonzero for unavailable publication,
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
no real Incus/cluster/registry run is claimed. Even a VM `PASS` would prove
only this bounded N/N+1 Snow bootc path: not recovery, key rotation,
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
