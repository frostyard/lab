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
- [ADR-0020 — Trust boundaries for AI automation in CI](https://github.com/frostyard/core/blob/main/docs/adr/0020-ai-automation-trust-boundaries.md) — copilot-review-apply admission rules, permissions: {}, idempotency markers
- [ADR-0021 — SHA-pinned actions and least-privilege CI workflows](https://github.com/frostyard/core/blob/main/docs/adr/0021-sha-pinned-actions-and-least-privilege-ci.md) — SHA pins, persist-credentials: false, nightly compliance
- [ADR-0023 — External downloads are version-pinned and checksum-verified](https://github.com/frostyard/core/blob/main/docs/adr/0023-verified-pinned-downloads.md) — KVER/KSHA-style inline pins in credential-bearing pods

When changing behavior covered by one of these, update or supersede the ADR
in frostyard/core first, then change this repo in the same effort.
