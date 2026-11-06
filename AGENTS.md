# Agent instructions

This repository is the GitOps-driven QA pipeline for snosi bootc images.
Start at [docs/README.md](docs/README.md); the architecture is in
[docs/design/architecture.md](docs/design/architecture.md).

This file (`AGENTS.md`) is the canonical agent instruction file. `CLAUDE.md`,
`GEMINI.md`, `.cursorrules` and `.github/copilot-instructions.md` are relative
symlinks to it, and `.claude/skills` links to `.agents/skills/` (core
ADR-0018, ADR-0029). Edit only the canonical paths.

- Keep changes small and focused.
- Treat git as the source of truth; do not hand-apply Argo Workflows or Kubernetes manifests.
- For pipeline changes, keep `argo/workflow-templates/` scoped to the `frostyard-lab` application and `manifests/` scoped to `frostyard-lab-infra`.
- Validate Argo YAML changes with the existing project tooling where practical.
- Keep documentation tone and structure consistent with the existing files in `docs/`.
- Preserve the deny-by-default controls in `policies/agent-governance.json`; validate policy changes with `python3 policies/check_agent_governance.py` and the focused pytest contract.

## Gate and guards

- Run `make verify` before you call a change done (template gate, policy
  check, pytest with its coverage gate, dashboard unit tests). CI job
  `Repository gate` runs `make ci`, which also checks that
  `.memory/corrections.jsonl` is append-only against the PR base; run
  `make ci BASE=origin/main` for the same check locally. Install
  `requirements-test.txt` first (CONTRIBUTING.md).
- `mise.toml` and `mise.lock` pin the Node that the gate runs (core
  ADR-0043); run `mise install`. Change a pin only with `mise lock`.
- `.github/risk-paths.json` lists the high-risk paths (core ADR-0068). A PR
  that touches one gets `risk:high` and declares tier High
  ([docs/risk-tiers.md](docs/risk-tiers.md)). Each `why` starts with a
  `protectedBoundaries` id of `policies/agent-governance.json`
  (`tests/test_risk_paths.py`). Add a path when you add a file of that kind.
- `scripts/lib/risk-paths.mjs` and `scripts/check-corrections.mjs` are copies
  from frostyard/core; never edit them by hand (`make verify` checks their
  hashes).
- Blocker to guard: when a review finds a blocking point, the PR that fixes
  it also adds a guard (a targeted test that fails without the fix, or an
  `AGENTS.md` rule when no test can catch it) and appends 1 line to
  `.memory/corrections.jsonl` (see `.memory/README.md`).
- Tests that read a run record read a frozen copy in
  `tests/fixtures/run-records/`; never edit those copies.

## Documentation rules

`docs/` follows core's four-category shape — see the table and conventions
in [docs/README.md](docs/README.md). `make verify` fails when a doc under
`docs/` is not linked from `docs/README.md`. A plan in `docs/plans/` holds
open work only: when its work is done, fold the facts that still hold into
`docs/` and delete the plan (git keeps it). The root `README.md` follows
core's README standard (ADR-0072, kind `infrastructure`, at most 1000 words):
lane contracts and designs go in `docs/`, not in the README.

New docs start from their category's `TEMPLATE.md` and get indexed there. A repo-local decision gets an ADR in
`docs/adr/` (next free number); an org-wide one gets an ADR in
frostyard/core plus a line in [docs/org-adrs.md](docs/org-adrs.md).

## Org-wide decisions

Org-level conventions this repo follows are recorded as ADRs in
frostyard/core — see [docs/org-adrs.md](docs/org-adrs.md) for the list that
binds this repo. Change the ADR (in core) before changing behavior it covers.

## Snow bootc lifecycle dev runs

Dev runs of the Snow bootc lifecycle lane (Brian, 2026-10-26, T237f): an agent
may submit `scripts/snow-lifecycle-dev-submit.sh` Workflows
(`dev-snow-bootc-lifecycle-*`, label `snosi.io/trigger=dev`) from an unmerged
branch to test lane fixes. A dev run must not publish, move a tag or change
the main WorkflowTemplate or ConfigMap; lane fixes still merge through a
reviewed PR before a main-lane run.
