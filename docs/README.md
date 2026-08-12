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
- [0003 — A never-green lane reports `unproven`, not Failed](adr/0003-unproven-is-distinct-from-failed.md)
- [0004 — Evidence flows one way: checks.txt → collector → runs.json → Pages](adr/0004-one-way-evidence-pipeline.md)
- [0005 — Console markers over serial + SMBIOS credentials drive agentless guests](adr/0005-console-marker-protocol-for-agentless-guests.md)
- [0006 — Lanes reach the host's incus by mounting /usr/incus and the API socket, never SSH](adr/0006-host-daemon-access-by-mount-never-ssh.md)
- [0007 — Cross-workflow concurrency is bounded only by template-level semaphores](adr/0007-cross-workflow-concurrency-via-template-semaphores.md)
- [0008 — Host media cache with origin-fingerprint sidecars](adr/0008-media-cache-with-fingerprint-sidecars.md)
- [0009 — No artifact store: the workflow log is the surface, host disk the fallback](adr/0009-no-artifact-store-logs-are-the-surface.md)
- [0010 — Vacuous success is forbidden: green must mean work was done](adr/0010-vacuous-success-is-forbidden.md)

Org-wide decisions binding this repo: [org-adrs.md](org-adrs.md).

### Design

*(none yet)*

### Specs

*(none yet)*

### Plans

*(none yet)*

### Uncategorized (indexed where they stand)

- [roadmap.md](roadmap.md) — the working roadmap: lane status, phases, and
  the evidence audit
- [ops/bootstrap.md](ops/bootstrap.md) — from a bare k3s node to a
  reconciling lab
- [quality.md](quality.md) — quality signals, evidence, and known gaps
- [risk-tiers.md](risk-tiers.md) — change risk tiers used by review and CI
- [review-rubric.md](review-rubric.md) — what review checks, by tier
- [automated-review.md](automated-review.md) — the automated review pipeline
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
