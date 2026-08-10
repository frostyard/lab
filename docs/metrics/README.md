# Public metrics

Lab publishes aggregate operational and repository metrics so maintainers can
audit the automation without exposing workflow logs, request content, session
content, or credentials.

| Signal | Public surface | Freshness |
|---|---|---|
| QA lane status and run history | [Lab dashboard](https://frostyard.github.io/lab/) | Updated from the Argo workflow collector when run data changes |
| Hive health, queues, agent states, repositories, and usage | [Hive dashboard](https://frostyard.github.io/lab/hive/) | Read live through the sanitized observer API |
| Pull request acceptance and time to merge | [PR metrics workflow](https://github.com/frostyard/lab/actions/workflows/pr-metrics.yml) | Calculated every Monday or on demand |

The QA dashboard is generated from `site/src/data/runs.json`. It reports the
latest state of each test lane and recent runs, including duration, trigger,
result summary, and lane-specific checks. The collector reads Argo directly,
so new workflow lanes appear without a separate reporting integration.

The Hive dashboard exposes only the observer's aggregate contract. The
internal Hive dashboard and individual session data are not publicly proxied;
see [`hive-observer/README.md`](../../hive-observer/README.md) for the
published fields and security boundary.

## Pull request acceptance

The `PR metrics` workflow measures the share of pull requests closed during
the measurement window that were merged:

```text
merged pull requests / all pull requests closed during the window
```

The default rolling window is 90 days. The report also includes the raw merged
and closed-without-merge counts and the median time to merge. Each run writes
the report to its GitHub Actions job summary and emits the same data as JSON in
the step log.

A pull request closed without merging may be obsolete, duplicated, or
superseded; the count should not be interpreted as a maintainer explicitly
rejecting every such change.

Run the metric locally with Node.js 22 or later:

```console
GH_TOKEN="$(gh auth token)" node scripts/pr-metrics.mjs \
  --repo frostyard/lab \
  --days 90
```

Authentication is optional for public repositories but avoids GitHub's low
unauthenticated API rate limit. For the distinction between operational
metrics and repository checks, see [`docs/quality.md`](../quality.md).

## Auto-QA feedback policy

`.github/auto-qa-tuning.json` defines the machine-readable policy for acting
on the acceptance-rate signal. A window with fewer than ten closed pull
requests holds the current policy. With enough data, a relative regression of
ten percent or more routes the observed failure pattern to focused guidance or
a targeted local check. Relaxation requires two consecutive improved windows.

Required and security checks are never relaxed. Any policy adjustment must be
reviewed through a pull request.
