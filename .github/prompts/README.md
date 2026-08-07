# Prompt catalog

Reusable prompts for coding agents (and humans) working in this repo. Each
`*.prompt.md` file is a task recipe: what to read first, what the repo's rules
are for that kind of change, and how to check the result.

They follow the [Copilot prompt file](https://docs.github.com/en/copilot/customizing-copilot/adding-repository-custom-instructions-for-github-copilot)
convention — YAML front matter with a `mode` and `description`, then the
instructions in Markdown.

| Prompt | Use it when |
|---|---|
| [`workflow-template.prompt.md`](workflow-template.prompt.md) | Adding or changing an Argo `WorkflowTemplate` / `CronWorkflow` under `argo/` |
| [`manifest.prompt.md`](manifest.prompt.md) | Changing cluster infra under `manifests/` or `argocd/` |
| [`new-qa-lane.prompt.md`](new-qa-lane.prompt.md) | Wiring a new QA lane end to end |
| [`triage-failed-run.prompt.md`](triage-failed-run.prompt.md) | A pipeline run went red and needs diagnosing |
| [`docs-update.prompt.md`](docs-update.prompt.md) | Updating `README.md`, `docs/`, or the reporting site copy |

## House rules that apply to every prompt

- **Git is the source of truth.** Argo CD runs with `selfHeal: true`; a manual
  `kubectl apply` is reverted on the next reconcile. Change the file in git.
- `argo/workflow-templates/` is reconciled by the `frostyard-lab` Application;
  `manifests/` by `frostyard-lab-infra`. Say which one a change touches.
- Validate before pushing: `just validate` (server-side dry run) for YAML,
  `pytest` for `scripts/`.
- Keep pull requests small and focused, per [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Adding a prompt

Create `<task-name>.prompt.md` with front matter, keep it scoped to one task,
and add a row to the table above.
