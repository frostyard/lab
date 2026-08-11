# Claude code review workflow

`.github/workflows/claude-code-review.yml` provides an advisory AI review for
non-draft pull requests whose branch is in this repository. It runs when such a
pull request is opened, reopened, synchronized, or marked ready for review.
Fork pull requests are deliberately skipped because the `pull_request` event
does not expose repository secrets to forks, and switching to
`pull_request_target` would put a secret-bearing job in the trust path of an
untrusted diff.

## Setup

A maintainer must configure `ANTHROPIC_API_KEY` as a GitHub Actions repository
secret. A pre-check tests only whether the value is empty, then the workflow
passes it to the commit-pinned official `anthropics/claude-code-action`. If the
secret is absent, the job records a warning and summary and skips checkout and
review without failing. An invalid configured secret fails visibly without
changing repository or cluster state.

The workflow has only `contents: read` and `pull-requests: write`. Checkout does
not persist credentials. Claude's tools are limited to reading pull-request
metadata/diffs and creating review comments; it cannot change contents, run
repository commands, merge, deploy, publish, or access the cluster. Concurrent
reviews of the same pull request cancel the stale run so an obsolete diff does
not consume review budget.

## Trust and review model

Pull-request text and files are untrusted input. The prompt explicitly tells
Claude not to follow instructions from that data. This is defense in depth, not
a claim that model output is trusted: comments are advisory and require human
verification under `docs/review-rubric.md`. Existing CI and maintainer approval
remain authoritative.

## Failure and rollback

Skipping a review because the secret is absent is visible in the job log and
summary; it does not produce review evidence. Invalid credentials and action
failures remain failed workflow runs.

Rotate or remove `ANTHROPIC_API_KEY` to disable API access immediately. To
disable the integration while retaining its history, disable the workflow in
GitHub Actions or revert the workflow commit. No Argo CD Application, QA lane,
cluster credential, or runtime manifest is affected.
