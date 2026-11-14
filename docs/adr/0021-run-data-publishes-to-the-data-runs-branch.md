# 0021 — Run data publishes to the `data/runs` branch, not to `main`

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

[ADR-0004](0004-one-way-evidence-pipeline.md) decision 4 made publication a
git push: the `publish-results` CronWorkflow committed
`site/src/data/runs.json` straight to `main` every 30 minutes, and that push
triggered the Pages build.

The repository ruleset `frostyard: required checks` now covers the default
branch. It requires the check `CI result` (core
[ADR-0076](https://github.com/frostyard/core/blob/main/docs/adr/0076-require-status-checks-on-every-default-branch.md))
and has no bypass actors. A direct push carries no check run, so the ruleset
rejects it. Since 2026-11-09 16:14 UTC every `publish-results` run fails after
3 push attempts, and the last data commit on `main` is `052f46d0`
(2026-11-09 16:00 UTC). The dashboard froze there.

A bot commit every 30 minutes does not belong on a branch where every change
goes through a pull request and its checks. Adding a bypass actor for the
cluster token would weaken the merge rule for every path in `main`, not only
for the data file.

## Decision

1. **The run data lives on the orphan branch `data/runs`.** The branch holds
   1 file at its root, `runs.json`, in the collector's unchanged output
   contract. It shares no history with `main`. No ruleset covers it; it must
   stay outside every ruleset, or publication stops again.
2. **`publish-results` pushes only to `data/runs`.** The job still clones
   `main` for the collector, then runs
   [`scripts/publish-runs.sh`](../../scripts/publish-runs.sh): it regenerates
   `runs.json` on top of the branch tip, skips a timestamp-only change, and
   pushes with the refspec `HEAD:refs/heads/data/runs`, never with force. The
   first run creates the branch as an orphan root commit; the collector
   regenerates the whole file from cluster state, so no seed copy is needed.
   A rejected push regenerates on the new tip, at most 3 times.
3. **Pages reads the branch at build time.** The `pages` workflow runs
   [`scripts/fetch-runs-data.sh`](../../scripts/fetch-runs-data.sh) before
   the build. It fetches `runs.json` from `data/runs`, checks its shape, and
   writes it over `site/src/data/runs.json`. When the branch does not exist,
   the build keeps the committed copy on `main` as its fallback. Any other
   fetch or shape error fails the build, so a network fault cannot replace a
   fresh deployment with the old fallback.
4. **Pages rebuilds on a schedule**, at minute 15 and 45 of each hour, 15
   minutes after each publish run. A push to `main` under `site/**` and a
   manual dispatch still build too.
5. **`site/src/data/runs.json` on `main` is the fallback, not the live
   data.** It stays frozen at its last publish. The e2e suite and the local
   preview use it; the dashboard's freshness banner shows its age.

## Consequences

- Every rule on `main` stays as it is. The cluster token keeps its single
  `contents:write` grant; it now writes only to `data/runs`.
- The dashboard is at most about 45 minutes behind the cluster (publish
  every 30 minutes, then the next scheduled build), against about 30 minutes
  before. The freshness banner (older than 2 hours) still has a wide margin.
- Pages builds 48 times a day even when the data did not change. Public
  repository minutes are free; a build is about 1 minute.
- GitHub disables a schedule in a public repository after 60 days with no
  activity. This repository merges far more often than that.
- `data/runs` grows by 1 commit for each real data change, as `main` did
  before.
- A new ruleset that targets all branches, or a pattern that matches
  `data/runs`, stops publication. Check
  `gh api repos/frostyard/lab/rules/branches/data/runs` (it must return
  `[]`) before such a change.
- [`tests/test_publish_runs.py`](../../tests/test_publish_runs.py) guards
  the target: it fails when the manifest or the script pushes to `main`.

## Alternatives considered

- **A bypass actor for the cluster token on the ruleset:** rejected. It lets
  that token change any file on `main` without the required check.
- **A pull request per data change:** rejected. 48 bot pull requests a day,
  each waiting on CI and on a human merge, for derived data.
- **A push trigger on `data/runs`:** does not work. A push event runs the
  workflow file of the pushed commit, and the orphan branch holds no workflow;
  the `github-pages` environment also deploys from `main`.
- **`repository_dispatch` from the job after each push:** rejected. It needs
  a token permission that the cluster token is not proven to have, and it adds
  a second write call to a job that holds a credential.

## References

- Shapes: [design/reporting.md](../design/reporting.md),
  [manifests/publish-results.yaml](../../manifests/publish-results.yaml),
  [.github/workflows/pages.yml](../../.github/workflows/pages.yml)
- Builds on: [ADR-0004](0004-one-way-evidence-pipeline.md) (supersedes its
  decision 4: the push target and the Pages trigger)
