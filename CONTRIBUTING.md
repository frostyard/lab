# Contributing to frostyard/lab

Thanks for taking the time to contribute. This repo is the GitOps-driven QA
pipeline for snosi bootc images, so most changes fall into one of two
buckets: pipeline definitions (`argo/`, `manifests/`) reconciled by Argo CD,
or supporting docs/tooling (`docs/`, `Justfile`, `scripts/`, `site/`).

## Ground rules

- **Git is the source of truth.** `selfHeal: true` means a manual
  `kubectl apply` against the cluster will be reverted on the next Argo CD
  reconcile. Never hand-apply a `WorkflowTemplate` or manifest — change it in
  git and let Argo CD/Argo Workflows pick it up.
- Keep pipeline changes scoped: `argo/workflow-templates/` maps to the
  `frostyard-lab` Application, `manifests/` maps to `frostyard-lab-infra`.
- Prefer small, focused pull requests over large ones.

## Getting started

1. Fork the repository and create a branch for your change.
2. Read the [README](README.md) for an overview of the pipeline architecture,
   and [docs/](docs/) for deeper operational guides (bootstrap, roadmap,
   etc.).
3. The [`Justfile`](Justfile) has convenience recipes for common operations
   (`just status`, `just refresh`, `just smoke`, `just qa ...`). Run
   `just --list` to see everything available.
4. The [prompt catalog](.github/prompts/) has task recipes for the common
   kinds of change here (workflow templates, manifests, new QA lanes, triaging
   a failed run, docs). They are written for coding agents, but they double as
   a checklist for humans.

## Running Python tests locally

CI runs the pytest suite with Python 3.12 and 3.13. From the repository root,
create an isolated environment, install the same test dependencies as CI, and
run the suite:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-test.txt
python -m pytest -q
```

`requirements-test.txt` pins pytest plus the offline YAML and Kubernetes 1.36
schema validators. `pytest.ini` discovers the tests under `tests/`. When changing
`scripts/collect_runs.py`, you can run its focused tests during development:

```bash
python -m pytest -q tests/test_collect_runs.py
```

Run the full suite before opening a pull request. Changes to the agent
governance policy also require `python3 policies/check_agent_governance.py`.

## Making changes

- If you're changing an Argo `WorkflowTemplate` or `CronWorkflow`, validate
  the YAML with `python -m pytest -q` and `argo lint` before opening a PR, and
  describe what lane(s) are affected.
- If you're changing the reporting site (`site/`), run the end-to-end suite
  with `just site-e2e` (Playwright specs live in `e2e/`).
- If you're changing docs, keep the tone and structure consistent with the
  existing files in `docs/`.
- Update `docs/roadmap.md` if your change affects the status of a QA lane.
- Use the [pull request review rubric](docs/review-rubric.md) to evaluate
  correctness, GitOps ownership, evidence, safety, and validation consistently.

## Submitting a pull request

- Describe what changed and why, and which lane(s) or Applications
  (`frostyard-lab` / `frostyard-lab-infra`) are affected.
- Link any related issues.
- Be prepared to iterate based on review feedback.

## Reporting issues

Use the issue templates under `.github/ISSUE_TEMPLATE/` for bug reports and
feature requests. Include as much detail as possible — logs, the affected
lane, and the image/tag/suite combination if relevant.

## Code of conduct

Be respectful and constructive. This is a small operational project; treat
maintainers' and contributors' time accordingly.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE) that covers this project.
