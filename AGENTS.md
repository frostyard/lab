# Agent instructions

This repository is the GitOps-driven QA pipeline for snosi bootc images.

- Keep changes small and focused.
- Treat git as the source of truth; do not hand-apply Argo Workflows or Kubernetes manifests.
- For pipeline changes, keep `argo/workflow-templates/` scoped to the `frostyard-lab` application and `manifests/` scoped to `frostyard-lab-infra`.
- Validate Argo YAML changes with the existing project tooling where practical.
- Keep documentation tone and structure consistent with the existing files in `docs/`.
- Preserve the deny-by-default controls in `policies/agent-governance.json`; validate policy changes with `python3 policies/check_agent_governance.py` and the focused pytest contract.

## Documentation rules

`docs/` follows core's four-category shape — see the table and conventions
in [docs/README.md](docs/README.md). New docs start from their category's
`TEMPLATE.md` and get indexed there. A repo-local decision gets an ADR in
`docs/adr/` (next free number); an org-wide one gets an ADR in
frostyard/core plus a line in [docs/org-adrs.md](docs/org-adrs.md).

## Org-wide decisions

Org-level conventions this repo follows are recorded as ADRs in
frostyard/core — see [docs/org-adrs.md](docs/org-adrs.md) for the list that
binds this repo. Change the ADR (in core) before changing behavior it covers.
