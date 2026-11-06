# Session summary

Use this file as the concise handoff for the next agent session. Replace the
current handoff after meaningful work rather than building an unbounded log;
durable corrections belong in `.memory/`.

## Persistent context

- This repository is the GitOps source of truth for the frostyard QA pipeline.
- Keep `argo/workflow-templates/` changes scoped to `frostyard-lab` and
  `manifests/` changes scoped to `frostyard-lab-infra`.
- Do not apply workflow or Kubernetes resources by hand; Argo CD reconciles
  changes from git.
- Validate changed workflow YAML and run the narrowest relevant tests before
  handing work off.

## Current handoff

- **Date:** 2026-11-06
- **Objective:** Bring the repository to frostyard core's repository baseline
  (ADR-0071, rows B1 to B17).
- **Outcome:** `make verify` / `make ci` gate, `mise.toml`, risk-path list and
  caller, Dependabot, `SECURITY.md`, PR template risk tier, README to the
  `infrastructure` kind; README detail moved to `docs/design/`,
  `docs/specs/bootc-lifecycle-lane.md` and `docs/ops/operating.md`; done
  plans deleted (git keeps them).
- **Decisions:** `docs/plans/` holds open work only; tests read frozen run
  records in `tests/fixtures/run-records/`.
- **Validation:** `make ci BASE=origin/main`.
- **Next step:** B5 follow-up: move the write permissions of `pages.yml` and
  `labeler.yml` from the top level to the jobs.
