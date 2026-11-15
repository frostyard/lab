# AI fix requested workflow

The [AI fix requested workflow](../.github/workflows/ai-fix-requested.yml)
assigns an open issue to the GitHub Copilot coding agent when a maintainer adds
the `ai-fix-requested` label. It validates that the target is an open, labeled
issue and does nothing when Copilot is already assigned.

## Repository setup

GitHub's agent-assignment API requires a user-to-server token; the workflow's
default `GITHUB_TOKEN` is an installation token and cannot start the agent.
A repository administrator must:

1. Create a fine-grained personal access token owned by an account with access
   to Copilot coding agent and this repository.
2. Grant the token the permissions required by GitHub's agent-assignment API:
   metadata read access and read/write access to Actions, Contents, Issues, and
   Pull requests.
3. Store it as an Actions repository secret named `COPILOT_ASSIGNMENT_TOKEN`.

`COPILOT_ASSIGNMENT_TOKEN` is the canonical name from core
[ADR-0020](https://github.com/frostyard/core/blob/main/docs/adr/0020-ai-automation-trust-boundaries.md).
Do not add aliases for the old spellings `COPILOT_AGENT_TOKEN` or
`COPILOT_ASSIGN_PAT`: a missing secret fails the run with
`COPILOT_ASSIGNMENT_TOKEN is not configured`, and it never skips silently.
`tests/test_ai_fix_workflow.py` guards the name.

Give the token access only to this repository, set an expiration, and rotate it
before it expires. See GitHub's
[Copilot cloud agent API documentation](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-via-the-api)
for the current authentication and permission requirements.

## Manual replay

If an event fails after the label is applied, open **Actions**, select
**AI fix requested**, choose **Run workflow**, and enter the issue number. The
manual path applies the same open-state, label, and duplicate-assignment checks
as the label event.
