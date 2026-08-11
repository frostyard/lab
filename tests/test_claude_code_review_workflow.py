from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/claude-code-review.yml"
DOCUMENTATION = ROOT / "docs/claude-code-review.md"
ACTION_SHA = "6b082c41935b4c8a3b8b0ef85ba4ba4d9eeb8975"
ALLOWED_TOOLS = (
    'mcp__github_inline_comment__create_inline_comment,'
    'Bash(gh pr comment:*),Bash(gh pr diff:*),Bash(gh pr view:*)'
)


def test_claude_review_workflow_is_pinned_and_least_privilege():
    workflow = WORKFLOW.read_text()

    assert "  pull_request:\n" in workflow
    assert "pull_request_target" not in workflow
    assert "permissions: {}" in workflow
    assert "github.event.pull_request.draft == false" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "timeout-minutes: 10" in workflow

    assert "      contents: read" in workflow
    assert "      pull-requests: write" in workflow
    assert "contents: write" not in workflow
    assert "issues: write" not in workflow
    assert "actions: write" not in workflow
    assert "id-token: write" not in workflow

    assert "id: credentials" in workflow
    assert "::warning title=Claude review skipped::" in workflow
    assert 'echo "available=false" >>"$GITHUB_OUTPUT"' in workflow
    assert workflow.count(
        "if: steps.credentials.outputs.available == 'true'"
    ) == 2
    assert workflow.count("        run: |") == 1
    assert workflow.index("id: credentials") < workflow.index(
        "Checkout pull request"
    )
    assert "persist-credentials: false" in workflow
    assert f"anthropics/claude-code-action@{ACTION_SHA}" in workflow
    assert not re.search(r"anthropics/claude-code-action@v", workflow)
    assert "anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}" in workflow
    assert "docs/review-rubric.md" in workflow
    assert f'--allowedTools "{ALLOWED_TOOLS}"' in workflow


def test_claude_review_documents_secret_and_trust_boundary():
    documentation = " ".join(DOCUMENTATION.read_text().split())

    for required_text in (
        "ANTHROPIC_API_KEY",
        "Fork pull requests are deliberately skipped",
        "comments are advisory and require human verification",
        "contents: read",
        "pull-requests: write",
        "pull_request_target",
        "skips checkout and review without failing",
        "Invalid credentials and action failures remain failed",
    ):
        assert required_text in documentation
