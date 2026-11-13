# Documentation

Docs are split by the question they answer
(core [ADR-0025](https://github.com/frostyard/core/blob/main/docs/adr/0025-consolidate-repository-docs-into-docs.md)):

| Directory | Question | Contents |
| --- | --- | --- |
| [adr/](adr/) | **Why** did we choose this? | Architecture Decision Records — immutable once accepted; superseded, never edited. Repo-local only: org-wide decisions live in [frostyard/core](https://github.com/frostyard/core/tree/main/docs/adr) (see [org-adrs.md](org-adrs.md)) |
| [design/](design/) | **How** does it fit together? | Living documents describing the current architecture |
| [specs/](specs/) | **What exactly** is the contract? | Precise, testable interface definitions |
| [plans/](plans/) | **When/in what order** do we build? | Roadmaps and phase plans; updated as work lands |

## Index

### Decisions (ADRs)

- [0001 — Two locally-sourced Argo CD Applications with a hand-applied bootstrap boundary](adr/0001-two-argocd-applications-and-hand-applied-bootstrap.md)
- [0002 — Digest-gated QA with compare-and-swap state, persisted only after QA passes](adr/0002-digest-gated-qa-with-compare-and-swap-state.md)
- [0003 — A never-green lane reports `unproven`, not Failed](adr/0003-unproven-is-distinct-from-failed.md) (product-only refinement: [0011](adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md))
- [0004 — Evidence flows one way: checks.txt → collector → runs.json → Pages](adr/0004-one-way-evidence-pipeline.md) (product roster exception: [0011](adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md))
- [0005 — Console markers over serial + SMBIOS credentials drive agentless guests](adr/0005-console-marker-protocol-for-agentless-guests.md)
- [0006 — Lanes reach the host's incus by mounting /usr/incus and the API socket, never SSH](adr/0006-host-daemon-access-by-mount-never-ssh.md)
- [0007 — Cross-workflow concurrency is bounded only by template-level semaphores](adr/0007-cross-workflow-concurrency-via-template-semaphores.md) (semaphore holders superseded in part: [0020](adr/0020-retire-the-native-ab-and-live-iso-vm-lanes.md))
- [0008 — Host media cache with origin-fingerprint sidecars](adr/0008-media-cache-with-fingerprint-sidecars.md) (`-latest` fingerprint and native variants superseded in part: [0020](adr/0020-retire-the-native-ab-and-live-iso-vm-lanes.md))
- [0009 — No artifact store: the workflow log is the surface, host disk the fallback](adr/0009-no-artifact-store-logs-are-the-surface.md)
- [0010 — Vacuous success is forbidden: green must mean work was done](adr/0010-vacuous-success-is-forbidden.md)
- [0011 — Roster product polls and qualify their QA evidence by digest and freshness](adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md) (product-only refinement to 0003/0004)
- [0012 — The lifecycle lane takes its product from the hashed manifest](adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)
- [0013 — Firn lane markers also travel through the kernel log](adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md) (amends 0005)
- [0014 — Lifecycle lane records also travel through the kernel log](adr/0014-lifecycle-lane-records-also-travel-through-the-kernel-log.md) (amends 0005)
- [0015 — The firn lane answers the LUKS passphrase prompt on the serial console](adr/0015-firn-lane-answers-the-luks-passphrase-prompt-on-serial.md) (builds on 0005, 0010, 0013)
- [0016 — The first-login probe is a report-only lane mode](adr/0016-first-login-probe-is-a-report-only-lane-mode.md)
- [0017 — The problems probe is a report-only lane mode](adr/0017-problems-probe-is-a-report-only-lane-mode.md)
- [0018 — The report probe is a report-only lane mode](adr/0018-report-probe-is-a-report-only-lane-mode.md)
- [0019 — Kernel-log marker copies use level err](adr/0019-kernel-log-copies-use-level-err.md) (amends 0013, 0014)
- [0020 — Retire the native A/B and live-ISO VM lanes](adr/0020-retire-the-native-ab-and-live-iso-vm-lanes.md) (supersedes parts of 0007, 0008)

Org-wide decisions binding this repo: [org-adrs.md](org-adrs.md).

### Design

- [architecture.md](design/architecture.md) — stack, the polling and QA
  pipeline, the container lanes, image lanes, suites, repository layout and
  known gaps
- [vm-lanes.md](design/vm-lanes.md) — the incus VM and installer lanes, and
  driving an agentless guest over serial and SMBIOS credentials
- [reporting.md](design/reporting.md) — the one-way results pipeline from the
  cluster to the published dashboard

### Specs

- [bootc-lifecycle-lane.md](specs/bootc-lifecycle-lane.md) — the manifest field
  contract and submission interface of the manual Snow bootc lifecycle lane

### Plans

<!-- Plans for finished work are removed once their record is in quality.md,
the roadmap or an ADR; git keeps them. -->

- [2026-11-04 — First-login probe on today's images (T843 t2)](plans/2026-11-04-first-login-probe-runs.md)
  — first-setup once on Snow and Snowfield, Sundog and Floe dev runs
- [2026-11-05 — Problems probe on the snosi-health images (T857 t7)](plans/2026-11-05-problems-probe-runs.md)
  — N before #1214 to N+1, per-boot lockdown and command line, bootc 1.17.1 to 1.17.3
- [2026-11-05 — Report probe on the Floe report sender (T857 t21)](plans/2026-11-05-report-probe-runs.md)
  — lab intake in a throwaway container, off and on states, 1 production report

### Uncategorized (indexed where they stand)

- [roadmap.md](roadmap.md) — the working roadmap: lane status, phases, and
  the evidence audit
- [ops/bootstrap.md](ops/bootstrap.md) — from a bare k3s node to a
  reconciling lab
- [ops/operating.md](ops/operating.md) — operator clients, the `just` recipes
  and the command each one runs
- [quality.md](quality.md) — quality signals, evidence, and known gaps
- [risk-tiers.md](risk-tiers.md) — change risk tiers used by review and CI
- [review-rubric.md](review-rubric.md) — what review checks, by tier
- [claude-code-review.md](claude-code-review.md) — advisory Claude review
  workflow
- [ai-fix-workflow.md](ai-fix-workflow.md) — the AI fix-request workflow
- [SECURITY-AI.md](SECURITY-AI.md) — security posture for AI automation
- [metrics/README.md](metrics/README.md) — PR metrics collection
- [org-adrs.md](org-adrs.md) — frostyard/core ADRs that bind this repo

## Conventions

- **New docs start from their category's `TEMPLATE.md`** (in each directory).
- New decision → new ADR with the next number; if it reverses an old one,
  mark the old one `Superseded by NNNN` rather than editing it. Repo-local
  decisions only — org-wide decisions get an ADR in frostyard/core plus a
  line in [org-adrs.md](org-adrs.md).
- Design docs are updated in place to always reflect reality.
- Specs change only alongside the code that implements them.
- Cross-links between categories are mandatory in both directions.
- Adding a doc means adding it to the index above.
