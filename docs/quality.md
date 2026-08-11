# Quality dashboard

This page brings the repository's quality evidence into one place. A green
badge means the latest `main` run of that workflow passed. Some specialized
workflows are path-filtered, so a green badge is evidence about the files and
commit that triggered it, not a blanket statement that every part of the
repository was checked. The main CI workflow is unfiltered and runs its Python
and site matrices on every pull request. For a pull request, use that pull
request's checks rather than these default-branch badges.

## Live signals

| Signal | Status | What it establishes |
|---|---|---|
| Reporting site end to end | [![e2e status][e2e-badge]][e2e-workflow] | Playwright builds the reporting site and checks the rendered dashboard against the committed run data. |
| Repository CI | [![CI status][ci-badge]][ci-workflow] | Python 3.12/3.13 run every pytest contract, including Kubernetes 1.36 manifest schemas, Argo wiring, workload resource bounds, and the deny-by-default agent-governance policy; Node.js 22 runs the reporting-site unit suite. |
| Reporting site deployment | [![pages status][pages-badge]][pages-workflow] | The site unit tests and Astro build passed before the current GitHub Pages deployment. |

The operational QA dashboard at <https://frostyard.github.io/lab/> is a
different signal: it reports the images and QA lanes exercised by Argo. It
does not report whether this repository's code passed the checks above.

## Evidence expected by change

| Changed area | Expected evidence |
|---|---|
| `site/`, `e2e/`, or Playwright configuration | `just site-e2e`; run `cd site && npm test` when changing the API/data helpers. |
| `scripts/`, `tests/`, or `policies/` | `python -m pytest -q`; for governance changes also run `python3 policies/check_agent_governance.py`. |
| `argo/`, `manifests/`, `argocd/`, or Kubernetes resources | `python -m pytest -q` for offline schema and cross-resource contracts; also run `argo lint` where applicable and `just validate` against a configured cluster. |
| Documentation | Commands and links resolve, and claims about lane status agree with `README.md` and `docs/roadmap.md`. |
| Every pull request | Follow the [contributing guide](../CONTRIBUTING.md) and record the relevant result in the pull request's Testing section. |

## Known gaps

- The E2E workflow runs on pull requests only when its path filters match. The
  unfiltered CI workflow runs repository unit, policy, and offline manifest
  tests on every pull request.
- `.coverage-thresholds.json` currently sets every minimum to zero, and no
  workflow publishes a coverage result. Its presence is configuration, not an
  effective coverage gate.
- CI rejects malformed or duplicate-key YAML, strictly validates built-in
  resources against Kubernetes 1.36 schemas, and checks Argo references,
  Argo CD ownership, workload CPU/memory bounds, RBAC, and image-poller state.
  It cannot execute Argo CRD
  admission or cluster-specific policy without cluster credentials, so
  `argo lint` and `just validate` remain additional review evidence for
  manifest changes.
- The Pages workflow runs after changes reach `main`; it is deployment
  evidence, not a pre-merge check.

These gaps must stay visible until a workflow actually closes them. Adding a
new check should update both the live-signals table and the change-area mapping
above.

[e2e-badge]: https://github.com/frostyard/lab/actions/workflows/e2e.yml/badge.svg?branch=main
[e2e-workflow]: https://github.com/frostyard/lab/actions/workflows/e2e.yml
[ci-badge]: https://github.com/frostyard/lab/actions/workflows/ci.yml/badge.svg?branch=main
[ci-workflow]: https://github.com/frostyard/lab/actions/workflows/ci.yml
[pages-badge]: https://github.com/frostyard/lab/actions/workflows/pages.yml/badge.svg?branch=main
[pages-workflow]: https://github.com/frostyard/lab/actions/workflows/pages.yml
