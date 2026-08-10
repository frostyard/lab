# Quality dashboard

This page brings the repository's quality evidence into one place. A green
badge means the latest `main` run of that workflow passed. The workflows are
path-filtered, so a green badge is evidence about the files and commit that
triggered it, not a blanket statement that every part of the repository was
checked. For a pull request, use that pull request's checks rather than these
default-branch badges.

## Live signals

| Signal | Status | What it establishes |
|---|---|---|
| Reporting site end to end | [![e2e status][e2e-badge]][e2e-workflow] | Playwright builds the reporting site and checks the rendered dashboard against the committed run data. |
| Observer and collector tests | [![observer status][observer-badge]][observer-workflow] | `pytest` checks the observer's sanitized API contract and run-data collection behavior. |
| Reporting site deployment | [![pages status][pages-badge]][pages-workflow] | The site unit tests and Astro build passed before the current GitHub Pages deployment. |

The operational QA dashboard at <https://frostyard.github.io/lab/> is a
different signal: it reports the images and QA lanes exercised by Argo. It
does not report whether this repository's code passed the checks above.

## Evidence expected by change

| Changed area | Expected evidence |
|---|---|
| `site/`, `e2e/`, or Playwright configuration | `just site-e2e`; run `cd site && npm test` when changing the API/data helpers. |
| `scripts/` or `tests/` | `python -m pytest -q`. |
| `argo/`, `manifests/`, `argocd/`, or Kubernetes resources | `argo lint` where applicable and `just validate` against a configured cluster. |
| Documentation | Commands and links resolve, and claims about lane status agree with `README.md` and `docs/roadmap.md`. |
| Every pull request | Follow the [contributing guide](../CONTRIBUTING.md) and record the relevant result in the pull request's Testing section. |

## Known gaps

- The E2E and pytest workflows run on pull requests only when their path
  filters match. Documentation and Kubernetes manifest changes currently have
  no repository-wide automated check.
- `.coverage-thresholds.json` currently sets every minimum to zero, and no
  workflow publishes a coverage result. Its presence is configuration, not an
  effective coverage gate.
- `just validate` uses server-side dry runs against a live Kubernetes cluster.
  CI has no cluster credentials, so manifest validation remains review
  evidence supplied by the contributor.
- The Pages workflow runs after changes reach `main`; it is deployment
  evidence, not a pre-merge check.

These gaps must stay visible until a workflow actually closes them. Adding a
new check should update both the live-signals table and the change-area mapping
above.

[e2e-badge]: https://github.com/frostyard/lab/actions/workflows/e2e.yml/badge.svg?branch=main
[e2e-workflow]: https://github.com/frostyard/lab/actions/workflows/e2e.yml
[observer-badge]: https://github.com/frostyard/lab/actions/workflows/observer.yml/badge.svg?branch=main
[observer-workflow]: https://github.com/frostyard/lab/actions/workflows/observer.yml
[pages-badge]: https://github.com/frostyard/lab/actions/workflows/pages.yml/badge.svg?branch=main
[pages-workflow]: https://github.com/frostyard/lab/actions/workflows/pages.yml
