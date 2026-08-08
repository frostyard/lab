# Automated review application

`.github/workflows/copilot-review-apply.yml` closes the feedback loop on pull
requests created by the Copilot coding agent. When Copilot's reviewer or a
trusted repository collaborator submits actionable feedback, the workflow posts
an `@copilot` instruction that asks the coding agent to address the unresolved
review and run relevant checks.

## Repository setup

Create a repository secret named `COPILOT_AGENT_TOKEN`. It must be a
least-privileged user token whose owner has write access to this repository and
access to Copilot coding agent. Limit its repository access to `frostyard/lab`
and grant only the pull-request metadata read and issue-comment write access
needed to validate a review and post the instruction.

The workflow cannot use its `GITHUB_TOKEN` for the comment because events
created by that token do not invoke another workflow or coding-agent run. The
job itself declares no `GITHUB_TOKEN` permissions and does not check out or run
pull-request code.

## Admission and safety rules

Automatic and manual runs enforce the same rules:

- the pull request is open, non-draft, and uses a branch in this repository;
- the pull request was opened by the `copilot-swe-agent` GitHub App;
- the review is `COMMENTED` with inline feedback or `CHANGES_REQUESTED` with
  feedback;
- the reviewer is the Copilot pull-request reviewer App or has an
  `OWNER`, `MEMBER`, or `COLLABORATOR` association; and
- the review ID has not already been handed to Copilot.

The workflow re-fetches the pull request and review from the GitHub API instead
of trusting dispatch inputs. It never executes or copies review text into a
shell command; the generated comment contains only a fixed instruction, the
API-provided reviewer login, and the review URL. A hidden review-ID marker makes
reruns idempotent.

This automation updates an existing agent pull request only. It cannot merge,
change branch protection, deploy GitOps resources, or bypass CI and maintainer
review. Normal review and required checks remain the acceptance boundary.

## Manual retry

A maintainer can run **Apply review feedback** from the Actions tab with the
pull request number and submitted review ID. This is useful after configuring a
missing token or retrying a transient API failure; the same admission checks and
idempotency marker still apply.
