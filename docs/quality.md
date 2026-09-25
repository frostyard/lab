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
does not report whether this repository's code passed the checks above.

## Manual Snow bootc lifecycle evidence

The [manual Snow bootc WorkflowTemplate](../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
accepts explicit public `manifest-json` only; the exact fields, trust pins,
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
`result-summary.txt` as far as execution reached. Preflight checks the signed
ISO index against the pinned fingerprint and records the ISO hash, key hashes,
verified N/N+1 OCI digests, version mappings and narrow sandboxed Firn v1/v2
validator provenance; it does
**not** prove full Firn installed compatibility. The ISO `SHA256SUMS.gpg`
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
serial console, installer traces, passwords, recovery material and NVRAM are never retained
as evidence or output parameters; transient raw serial is deleted.

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
| Documentation | Commands and links resolve, and claims about lane status agree with `README.md` and `docs/roadmap.md`. |
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
