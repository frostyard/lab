# Design: Reporting

Living document. Rationale: [ADR-0004](../adr/0004-one-way-evidence-pipeline.md), [ADR-0021](../adr/0021-run-data-publishes-to-the-data-runs-branch.md), [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md), [ADR-0011](../adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md).


`site/` is an [Astro](https://astro.build) page published to GitHub Pages,
showing per-lane status and recent run history:
**<https://frostyard.github.io/lab/>**

The pipeline-results data flow is one-way: the cluster publishes a git snapshot;
GitHub Pages does not query the cluster:

```
publish-results CronWorkflow (in cluster, every 30 minutes)
    │  reads the Argo API, regenerates runs.json (scripts/publish-runs.sh)
    ▼
git push → branch data/runs (orphan, 1 file: runs.json)
    │
    ▼  scheduled at :15 and :45
.github/workflows/pages.yml → scripts/fetch-runs-data.sh
    │  copies runs.json over site/src/data/runs.json
    ▼
builds site/ → GitHub Pages
```

The data branch exists because `main` accepts changes only through a pull
request with the required check `CI result`; a direct bot push to `main` is
rejected ([ADR-0021](../adr/0021-run-data-publishes-to-the-data-runs-branch.md)).
No ruleset covers `data/runs`, and none may: check that
`gh api repos/frostyard/lab/rules/branches/data/runs` returns `[]` before a
ruleset change. The first publish run creates the branch as an orphan root
commit. A rejected push regenerates on the new branch tip, at most 3 times,
and never forces. `tests/test_publish_runs.py` fails if the job pushes to
`main` again.

Pages rebuilds on a schedule, because a push to the orphan branch cannot
trigger a workflow: GitHub runs the workflow file of the pushed commit, and
`data/runs` holds none. A push to `main` under `site/**` and a manual
dispatch also build. When `data/runs` does not exist, the build keeps the
committed `site/src/data/runs.json` on `main` as its fallback; any other
fetch error, or a file without `generated`, `lanes` and `runs`, fails the
build and leaves the last deployment in place.

The collector reads the Kubernetes API rather than being wired into each lane,
so workflows appear without per-lane reporting hooks
([ADR-0004](../adr/0004-one-way-evidence-pipeline.md); the `unproven`
lane state it carries is
[ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md); the product-only
QA evidence refinement is
[ADR-0011](../adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md)).
It skips the commit when only the generation timestamp moved, so an idle cluster
does not push a commit every 30 minutes.

Each published run records `name`, `phase`, `started`, `finished`,
`durationSeconds`, `template`, `laneKey`, `qaOutcome`, `digest`, `kind`,
`label`, `trigger`, `result`, and `checks`. `digest` is the poll's remote
sha256 digest, or `null` when none resolved. `lanes` groups runs by `laneKey`
with `latest`, retained `runs` count, `everGreen`, and, for product lanes,
`qaRun`: the newest retained passed run with the same digest as the lane's
latest run, as `{name, finished, result}`, or `null`. `generated` timestamps
the snapshot. Scheduled product polls have distinct keys
`image-poll-{snow,floe,snowfield,sundog}-latest` (from the workflow name),
despite all using the `image-poller` template; that list is the roster of
products snosi publishes. For those polls, `qaOutcome` is `passed` or `failed`
only with observed Behave scenario counts and a matching terminal workflow
phase. `carried` means a successful poll with an absent or null Behave result
whose `changed` is `"false"` and whose valid remote digest equals
`stored-digest`: because image-poller stores a digest only after QA succeeds
(ADR-0002), that digest already passed. When retained, the dashboard shows the
short digest and the run that verified it. `not-run` means a successful poll
without that proof (for example `remote-digest: none`), and `unknown` means
insufficient or ambiguous evidence (including an empty result string or
zero-count failures). For non-product runs, `qaOutcome: unknown` means image QA
is not applicable; the workflow phase is the relevant status.
`phase: Succeeded` can mean a carried pass with proof, but **not** passing QA
without that proof; a Failed phase may still carry a Behave result. `result` is
a summary, not a failing step, and empty container `checks` does not mean all
steps passed. `trigger` defaults to `scheduled` unless the workflow supplies a
label; VM/installer runs are manual evidence, not continuous product QA. Other
lanes' phases describe workflow execution only.

Older snapshots may predate `laneKey`/`qaOutcome` publication and group all
product polls under one `image-poller` lane. The dashboard separates those
legacy runs by workflow name but leaves their QA outcome **unknown**, even
when their `result` text looks conclusive. Two freshness checks apply. Poll
evidence older than six hours by `generated` is stale: the poll stopped while
publication went on. An old confirmed failure stays a failure with a stale
qualifier; old unknown or not-run polls remain unverified and count as both
unknown and stale; an old pass or carried pass is not green. Separately, the
page warns when the snapshot is missing, invalid, more than five minutes in
the future, or older than two hours by the viewer's clock: the lab or the
publisher stopped. That check runs in the browser, so the banner stays hidden
with JavaScript off. New fields take effect through collector publication, not
by hand-editing `runs.json`.

`e2e/` holds the [Playwright](https://playwright.dev) end-to-end suite. It
builds the site and drives the same static output GitHub Pages serves, asserting
that what `runs.json` contains is what the dashboard renders. Run it with `just
site-e2e`; CI runs it via `.github/workflows/e2e.yml`.

The page is styled with the [frostyard design
system](https://github.com/frostyard/core/tree/main/.agents/skills/frostyard-design), following its Pilothouse
dashboard language: ink surfaces, hairline separation, square corners, cold
ice/sky accents, mono kickers. `site/src/styles/tokens/` is copied **verbatim**
from that repo — change tokens there and re-copy rather than patching them here,
or the next copy silently reverts the edit. Because those tokens define no light
palette, the page is dark-only by design.

`publish-results` is declared **unsuspended** in
`manifests/publish-results.yaml`, scheduled every 30 minutes. Publication
requires a cluster-side `github-token` secret with `contents:write` on
frostyard/lab; if absent the job fails at its guard (exit 1) without
publishing.
For secret creation instructions, see the header of
[`manifests/publish-results.yaml`](../../manifests/publish-results.yaml); do not commit
the credential to git.
Neither the manifest nor the snapshot proves the credential or publisher is
healthy now. The page always renders the last snapshot on `data/runs` at its
last build, which may be stale. To check the publisher, look at the newest
commit on `data/runs`: an active cluster adds one within 30 minutes of a
change in run data.

Locally: `just site-dev` and `just site-build` first run `just site-data`,
which copies the published `runs.json` over the tracked fallback
`site/src/data/runs.json`; do not commit that change. `just collect`
regenerates the same file from the cluster instead.

## References

- Rationale: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0004](../adr/0004-one-way-evidence-pipeline.md),
  [ADR-0011](../adr/0011-product-poll-qa-evidence-is-rostered-and-fresh.md)
- Architecture: [design/architecture.md](architecture.md)
- Evidence limits: [quality.md](../quality.md#triaging-product-qa-evidence)
