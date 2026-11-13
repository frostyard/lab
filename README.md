<!-- readme-kind: infrastructure -->
# frostyard lab

[![CI](https://github.com/frostyard/lab/actions/workflows/ci.yml/badge.svg)](https://github.com/frostyard/lab/actions/workflows/ci.yml)

frostyard lab holds the GitOps QA pipeline for the
[snosi](https://github.com/frostyard/snosi) bootc images: Argo Workflows
templates, cluster manifests and the QA dashboard for the lab's single-node
k3s host. The snosi maintainers use it to learn whether a published image is
good; a change reaches the cluster only by a merge to `main`, which Argo CD
reconciles.

**Status:** in production.

**Documentation:** [docs/README.md](docs/README.md)

## What is here

| Path | What it is |
| --- | --- |
| `argo/workflow-templates/` | The WorkflowTemplates that Argo CD syncs (Application `frostyard-lab`): the digest poller, the QA pipeline, and the container, VM, install and lifecycle lanes |
| `argo/*.yaml` | Submit files for one-off manual runs |
| `manifests/` | CronWorkflows, RBAC, semaphores, digest state and result publication (Application `frostyard-lab-infra`) |
| `argocd/` | The Argo CD Applications, applied once by hand at bootstrap |
| `site/`, `e2e/` | The Astro QA dashboard on GitHub Pages, and its Playwright suite |
| `scripts/` | The run collector, the dev submit helpers, the lifecycle preflight and the template gate |
| `tests/`, `test/` | Offline pytest contracts for the templates, manifests and scripts; Node tests of the template gate |
| `policies/` | The deny-by-default agent governance policy and its checker |
| `Justfile` | Operator wrappers over `kubectl` and `argo` |
| `docs/` | Decisions, designs, the lifecycle lane spec, runbooks and open plans |

## Use

Results are public on the [QA dashboard](https://frostyard.github.io/lab/),
which renders the committed `site/src/data/runs.json`. To look at the cluster,
start read-only with the current kubeconfig context:

```bash
just status     # Argo CD sync and health, and the enabled lanes
just runs       # recent workflow runs
```

To change a pipeline, open a pull request: after the merge, Argo CD applies it,
and `selfHeal` reverts anything applied by hand. Never `kubectl apply` a
WorkflowTemplate or a manifest. The clients, every recipe and the manual runs
are in [operating the lab](docs/ops/operating.md); a cluster from zero is in
[bootstrap](docs/ops/bootstrap.md); the field contract of the manual bootc
lifecycle lane is in its [spec](docs/specs/bootc-lifecycle-lane.md).

## How it works

A CronWorkflow polls each product's registry digest. When the digest moves, the
QA pipeline runs the [testsuite](https://github.com/frostyard/testsuite) BDD
suites against that exact digest, in the image booted as a nested systemd
container, and stores the digest only after QA passes, so a failure retries on
the next poll. Incus VM lanes cover what a container cannot see: Secure Boot,
the installer and the update path. A collector publishes the results to the
dashboard in 1 direction; the dashboard never queries the cluster. Details:
[architecture](docs/design/architecture.md), [VM lanes](docs/design/vm-lanes.md)
and [reporting](docs/design/reporting.md).

## Limits

- Evidence is advisory: no lane is a release gate, and a VM lane is not
  hardware qualification ([quality](docs/quality.md)).
- The dashboard shows bounded workflow summaries, not full diagnostics, and can
  be stale ([reporting](docs/design/reporting.md)).
- The lab has no artifact store: the workflow log is the evidence surface
  ([ADR-0009](docs/adr/0009-no-artifact-store-logs-are-the-surface.md)).
- More known gaps are in [architecture](docs/design/architecture.md#known-gaps).
  The superseded [roadmap](docs/roadmap.md) is history; it still holds G4
  pre-promotion gating and the open questions.

## Develop

```sh
mise install                              # the pinned Node
python -m pip install -r requirements-test.txt
make ci                                   # what CI runs
```

The full setup, the test suites and the Playwright run are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Where to go next

- [AGENTS.md](AGENTS.md): the rules for agents and contributors.
- [docs/README.md](docs/README.md): the decisions, designs, specs and plans.
- [SECURITY.md](SECURITY.md): report a vulnerability privately.
- [LICENSE](LICENSE): MIT.
