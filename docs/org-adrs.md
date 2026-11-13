# Org-wide decisions (frostyard/core ADRs)

Conventions this repository follows that are decided at the org level are
recorded as ADRs in
[frostyard/core](https://github.com/frostyard/core/tree/main/docs/adr).
The ones that bind lab:

- [ADR-0004 — Product-namespaced filesystem paths, split by lifetime tier](https://github.com/frostyard/core/blob/main/docs/adr/0004-product-namespaced-filesystem-tiers.md) — the /var/lib/snosi-lab host cache/scratch root
- [ADR-0006 — OS artifact versions are 14-digit UTC timestamps](https://github.com/frostyard/core/blob/main/docs/adr/0006-os-artifact-versions-are-utc-timestamps.md) — lanes parse and compare these versions
- [ADR-0008 — Sysext distribution layout and update contract](https://github.com/frostyard/core/blob/main/docs/adr/0008-sysext-distribution-and-update-contract.md) — QA exercises the published ext/ trees
- [ADR-0009 — repository.frostyard.org is the single artifact origin](https://github.com/frostyard/core/blob/main/docs/adr/0009-single-artifact-origin-repository-frostyard-org.md) — lanes fetch ISOs/disk images from the frozen namespaces (bare product name vs -ab naming trap)
- [ADR-0014 — One GPG repository key, baked into images](https://github.com/frostyard/core/blob/main/docs/adr/0014-single-gpg-trust-root.md) — gpgv-verify SHA256SUMS.gpg before trusting any filename from it
- [ADR-0017 — io.snosi.* OCI capability labels and the mechanics QA tier](https://github.com/frostyard/core/blob/main/docs/adr/0017-io-snosi-capability-labels-and-mechanics-tier.md) — lanes select tiers via io.snosi.bootc.secureboot-capable; :mechanics vs :latest routing
- [ADR-0018 — Org-wide agent instruction and knowledge surfaces](https://github.com/frostyard/core/blob/main/docs/adr/0018-org-wide-agent-instruction-and-knowledge-surfaces.md) — AGENTS.md symlinks, .knowledge/, .memory/, .github/prompts
- [ADR-0019 — Repository governance as machine-readable policy with risk tiers](https://github.com/frostyard/core/blob/main/docs/adr/0019-governance-as-code-and-risk-tiers.md) — policies/agent-governance.json, risk tiers
- [ADR-0020 — Trust boundaries for AI automation in CI](https://github.com/frostyard/core/blob/main/docs/adr/0020-ai-automation-trust-boundaries.md) — admission rules and least-privilege controls for AI automation
- [ADR-0021 — SHA-pinned actions and least-privilege CI workflows](https://github.com/frostyard/core/blob/main/docs/adr/0021-sha-pinned-actions-and-least-privilege-ci.md) — SHA pins, persist-credentials: false, nightly compliance
- [ADR-0023 — External downloads are version-pinned and checksum-verified](https://github.com/frostyard/core/blob/main/docs/adr/0023-verified-pinned-downloads.md) — KVER/KSHA-style inline pins in credential-bearing pods
- [ADR-0025 — One docs/ tree per repository, in core's four-category shape](https://github.com/frostyard/core/blob/main/docs/adr/0025-consolidate-repository-docs-into-docs.md) — docs/adr, design, specs, plans, each with its TEMPLATE.md, indexed in docs/README.md
- [ADR-0029 — ACMM conformance via canonical aliases](https://github.com/frostyard/core/blob/main/docs/adr/0029-acmm-conformance-via-canonical-aliases.md) — CLAUDE.md, GEMINI.md, .cursorrules, .github/copilot-instructions.md and .claude/skills are symlinks to the canonical paths
- [ADR-0041 — Retire copilot-review-apply where Snowcat gates review](https://github.com/frostyard/core/blob/main/docs/adr/0041-retire-copilot-review-apply-where-snowcat-gates-review.md) — retires the unbounded review-apply loop while preserving request-on-ready review and human routing
- [ADR-0043 — Pin repository tools in mise and name the verify gate](https://github.com/frostyard/core/blob/main/docs/adr/0043-pin-repository-tools-in-mise-and-name-the-verify-gate.md) — mise.toml and mise.lock pin the Node of the gate; `make verify`
- [ADR-0044 — Expose the make gate triad in every repository](https://github.com/frostyard/core/blob/main/docs/adr/0044-expose-the-make-gate-triad-in-every-repository.md) — `make verify`, `make check`, `make ci`
- [ADR-0068 — Label pull requests that touch high-risk paths](https://github.com/frostyard/core/blob/main/docs/adr/0068-label-pull-requests-that-touch-high-risk-paths.md) — .github/risk-paths.json and the `pr-triage` caller workflow (.github/workflows/risk-paths.yml)
- [ADR-0070 — Build tool docs sites with ProperDocs and MaterialX on GitHub Pages](https://github.com/frostyard/core/blob/main/docs/adr/0070-build-tool-docs-sites-with-materialx-on-github-pages.md) — does not bind lab: its Astro site is a QA dashboard, not tool documentation (baseline row B9 exempts lab)
- [ADR-0071 — Hold every code repository to one baseline](https://github.com/frostyard/core/blob/main/docs/adr/0071-hold-every-code-repository-to-one-baseline.md) — rows B1 to B17 of core's docs/specs/repository-baseline.md (B8, B9, B11 do not apply)
- [ADR-0072 — Write every README to one standard](https://github.com/frostyard/core/blob/main/docs/adr/0072-write-every-readme-to-one-standard.md) — README.md is kind `infrastructure`, checked by the frostyard-readme skill
- [ADR-0073 — Let CI run the `make ci` targets as separate jobs](https://github.com/frostyard/core/blob/main/docs/adr/0073-let-ci-run-the-make-ci-targets-as-separate-jobs.md) — ci.yml job `Repository gate` runs `make ci` in 1 job
- [ADR-0076 — Require status checks on every default branch](https://github.com/frostyard/core/blob/main/docs/adr/0076-require-status-checks-on-every-default-branch.md) — ci.yml job `CI result` needs gate, python and site, so 1 required check covers the workflow and a matrix name can change without a ruleset edit
- [ADR-0077 — Bind ready-for-human to the head commit](https://github.com/frostyard/core/blob/main/docs/adr/0077-bind-ready-for-human-to-the-head-commit.md) — the `pr-triage` caller removes `ready-for-human` when the head moves after the label, or when the label comes while CI at the head is not green
- [ADR-0078 — Sort pull requests by levels L1 to L4](https://github.com/frostyard/core/blob/main/docs/adr/0078-sort-pull-requests-by-levels-l1-to-l4.md) — .github/risk-paths.json is version 2 with a `level` per glob; the caller adds `level:L1` to `level:L4` and keeps `risk:high` for L1 and L2
- [ADR-0081 — Store each correction in a file of its own](https://github.com/frostyard/core/blob/main/docs/adr/0081-store-each-correction-in-a-file-of-its-own.md) — each correction is 1 file in [`.memory/corrections/`](../.memory/README.md) in canonical form; `make verify` refuses a tree with both forms, and `make ci BASE=<ref>` also refuses a removed or renamed record, a changed record other than a filled empty `promoted_to`, and the old single corrections file

When changing behavior covered by one of these, update or supersede the ADR
in frostyard/core first, then change this repo in the same effort.
