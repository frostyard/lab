from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/claude-code-review.yml"
DOCUMENTATION = ROOT / "docs/claude-code-review.md"
ACTION = "anthropics/claude-code-action"
# The pin is read from the workflow, never written here (core ADR-0088,
# ADR-0090). The owner reviews each new commit of this action in its bump
# pull request (AGENTS.md); tests/test_workflow_pins.py checks the comment and
# 1 SHA for each action across all workflows.
PIN_LINE = re.compile(
    r"^ +uses: (?P<action>[^@\s]+)@(?P<sha>\S+)(?: # (?P<comment>.*))?$",
    re.MULTILINE,
)
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r"v\d+\.\d+(\.\d+)?")
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
    assert not re.search(r"anthropics/claude-code-action@v", workflow)
    assert "anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}" in workflow
    assert "docs/review-rubric.md" in workflow
    assert f'--allowedTools "{ALLOWED_TOOLS}"' in workflow


def review_steps():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert list(workflow["jobs"]) == ["review"]
    return {step["name"]: step for step in workflow["jobs"]["review"]["steps"]}


def action_pins(text, action):
    return [
        match for match in PIN_LINE.finditer(text) if match["action"] == action
    ]


def assert_pinned(text, action):
    pins = action_pins(text, action)
    assert len(pins) == 1, f"want 1 {action} step, got {len(pins)}"
    assert COMMIT_SHA.fullmatch(pins[0]["sha"]), (
        f"{action} is pinned to {pins[0]['sha']!r}, not a full commit SHA"
    )
    assert pins[0]["comment"] and VERSION_COMMENT.fullmatch(pins[0]["comment"]), (
        f"{action} pin has comment {pins[0]['comment']!r}, want # vX.Y.Z or # vX.Y"
    )
    return pins[0]


def test_claude_action_is_pinned_in_the_review_step():
    text = WORKFLOW.read_text()
    pin = assert_pinned(text, ACTION)
    steps = review_steps()
    review = steps["Review pull request with Claude"]
    assert review["uses"] == f"{ACTION}@{pin['sha']}"
    assert review["if"] == "steps.credentials.outputs.available == 'true'"
    assert list(review["with"]) == ["anthropic_api_key", "prompt", "claude_args"]
    assert [step.get("uses", "").split("@")[0] for step in steps.values()] == [
        "",
        "actions/checkout",
        ACTION,
    ]


def test_checkout_is_pinned_and_does_not_persist_credentials():
    text = WORKFLOW.read_text()
    pin = assert_pinned(text, "actions/checkout")
    checkout = review_steps()["Checkout pull request"]
    assert checkout["uses"] == f"actions/checkout@{pin['sha']}"
    assert checkout["with"] == {"fetch-depth": 1, "persist-credentials": False}


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
