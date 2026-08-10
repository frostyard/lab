---
mode: agent
description: Add or change an Argo WorkflowTemplate or CronWorkflow under argo/.
---

# Change an Argo workflow

Read first:

- `argo/workflow-templates/` — the templates the pipeline calls by
  `templateRef`. `snosi-qa-pipeline.yaml` is the DAG entry point;
  `image-poller.yaml` is the digest poll.
- `argo/*.yaml` — one-off `Workflow` objects used for manual submissions.
- `README.md` § Architecture, for how a poll turns into a pipeline run.

## Rules

- Templates live in `argo/workflow-templates/` and are reconciled by the
  `frostyard-lab` Application. Never `kubectl apply` one by hand — `selfHeal`
  reverts it.
- Keep the existing metadata shape: a `metadata.annotations.description`
  explaining what the lane proves, and the
  `app.kubernetes.io/component` / `app.kubernetes.io/part-of` labels.
- Every long-running template needs an `activeDeadlineSeconds`.
- Anything that pulls an image from the registry must take the
  `selfie-container-qa` semaphore from the `workflow-semaphores` ConfigMap —
  registry egress is the contended resource, and the semaphore is the only
  lock that binds across separate Workflow objects.
- The image under test is always pinned **by digest**, never by tag alone. The
  poller resolves the digest and passes it down; a template must not re-resolve
  it.
- A digest is recorded as seen only after QA passes, so never move the
  bookkeeping earlier in the DAG.

## Check your work

- `python -m pytest -q` — offline schemas and cross-resource contracts.
- `just validate` — server-side dry run of every manifest and template.
- `argo lint argo/workflow-templates/<file>.yaml` if the CLI is available.
- Say in the PR which lane(s) and which Application (`frostyard-lab`) are
  affected, and update `docs/roadmap.md` if a lane's status changes.
