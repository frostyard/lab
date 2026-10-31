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
- [0007 — Cross-workflow concurrency is bounded only by template-level semaphores](adr/0007-cross-workflow-concurrency-via-template-semaphores.md)
- [0008 — Host media cache with origin-fingerprint sidecars](adr/0008-media-cache-with-fingerprint-sidecars.md)
- [0009 — No artifact store: the workflow log is the surface, host disk the fallback](adr/0009-no-artifact-store-logs-are-the-surface.md)
- [0010 — Vacuous success is forbidden: green must mean work was done](adr/0010-vacuous-success-is-forbidden.md)
- [0011 — Roster product polls and qualify their QA evidence by digest and freshness](adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md) (product-only refinement to 0003/0004)
- [0012 — The lifecycle lane takes its product from the hashed manifest](adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)
- [0013 — Firn lane markers also travel through the kernel log](adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md) (amends 0005)

Org-wide decisions binding this repo: [org-adrs.md](org-adrs.md).

### Design

*(none yet)*

### Specs

*(none yet)*

### Plans

- [2026-10-26 — First Snow bootc N → N+1 update and rollback run](plans/2026-10-26-snow-bootc-lifecycle-run.md)
  — selected inputs, preflight record and submit runbook
- [2026-10-28 — notesd and maild suite sysexts on an installed Snow (sysext probe)](plans/2026-10-28-snow-sysext-probe-run.md)
  — probe mode, dev run and result
- [2026-10-28 — First Floe bootc N → N+1 update and rollback run](plans/2026-10-28-floe-bootc-lifecycle-run.md)
  — selected inputs, known causes (snosi#1130), preflight record and submit runbook
- [2026-10-30 — First Sundog bootc N → N+1 update and rollback run](plans/2026-10-30-sundog-bootc-lifecycle-run.md)
  — selected inputs, known causes, dev-run phases
- [2026-10-29 — Sundog fresh-install rerun with console capture (snosi#1123)](plans/2026-10-29-sundog-fresh-install-rerun.md)
  — runs, the ffbw7 serial stall and the kernel-log marker fix
- [2026-10-29 — incus, coder and nix sysext activation on an installed Snow (lab#142)](plans/2026-10-29-snow-sysext-probe-lab142.md)
  — `.mount` units in the probe, dev run and result
- [2026-10-30 — Load-state evidence for the sysext probe (snosi#1189)](plans/2026-10-30-snow-sysext-probe-snosi1189.md)
  — `LoadState` and `NeedDaemonReload` in the record, control and reload dev runs

### Uncategorized (indexed where they stand)

- [roadmap.md](roadmap.md) — the working roadmap: lane status, phases, and
  the evidence audit
- [ops/bootstrap.md](ops/bootstrap.md) — from a bare k3s node to a
  reconciling lab
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
