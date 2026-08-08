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

- **Date:** 2026-08-08
- **Objective:** Add the ACMM session-summary learning artifact.
- **Outcome:** Added this repository-specific session handoff.
- **Decisions:** Keep summaries concise and store reusable corrections in
  `.memory/`.
- **Validation:** Confirmed the artifact exists at the ACMM-recognized path.
- **Next step:** No follow-up is required for this documentation-only change.
