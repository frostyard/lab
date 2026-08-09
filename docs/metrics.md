# Pull request metrics

The `PR metrics` workflow measures the repository's pull request acceptance
rate every Monday. It can also be run on demand with a custom measurement
window. Each run writes the result to its GitHub Actions job summary and emits
the same data as JSON in the step log.

Acceptance rate is:

```text
merged pull requests / all pull requests closed during the window
```

The default rolling window is 90 days. The report also includes the raw merged
and closed-without-merge counts and the median time to merge. A pull request
closed without merging may be obsolete, duplicated, or superseded; the count
should not be interpreted as a maintainer explicitly rejecting every such
change.

Run the metric locally with Node.js 22 or later:

```console
GH_TOKEN="$(gh auth token)" node scripts/pr-metrics.mjs \
  --repo frostyard/lab \
  --days 90
```

Authentication is optional for public repositories but avoids GitHub's low
unauthenticated API rate limit.

## Auto-QA feedback policy

`.github/auto-qa-tuning.json` defines the machine-readable policy for acting
on the acceptance-rate signal. A window with fewer than ten closed pull
requests holds the current policy. With enough data, a relative regression of
ten percent or more routes the observed failure pattern to focused guidance or
a targeted local check. Relaxation requires two consecutive improved windows.

Required and security checks are never relaxed. Any policy adjustment must be
reviewed through a pull request.
