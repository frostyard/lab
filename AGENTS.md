# Agent instructions

This repository is the GitOps-driven QA pipeline for snosi bootc images.

- Keep changes small and focused.
- Treat git as the source of truth; do not hand-apply Argo Workflows or Kubernetes manifests.
- For pipeline changes, keep `argo/workflow-templates/` scoped to the `frostyard-lab` application and `manifests/` scoped to `frostyard-lab-infra`.
- Validate Argo YAML changes with the existing project tooling where practical.
- Keep documentation tone and structure consistent with the existing files in `docs/`.
- Preserve the deny-by-default controls in `policies/agent-governance.json`; validate policy changes with `python3 policies/check_agent_governance.py` and the focused pytest contract.

## Org-wide decisions

Org-level conventions this repo follows are recorded as ADRs in
frostyard/core — see [docs/org-adrs.md](docs/org-adrs.md) for the list that
binds this repo. Change the ADR (in core) before changing behavior it covers.
