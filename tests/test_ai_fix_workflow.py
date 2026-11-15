from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ai-fix-requested.yml"
DOCUMENTATION = ROOT / "docs/ai-fix-workflow.md"

CANONICAL = "COPILOT_ASSIGNMENT_TOKEN"
LEGACY_NAMES = ("COPILOT_AGENT_TOKEN", "COPILOT_ASSIGN_PAT")
ADR_0020 = (
    "https://github.com/frostyard/core/blob/main/docs/adr/"
    "0020-ai-automation-trust-boundaries.md"
)


def test_ai_fix_workflow_uses_the_canonical_assignment_secret():
    workflow = WORKFLOW.read_text()

    assert workflow.count(f"secrets.{CANONICAL}") == 1
    assert f"GH_TOKEN: ${{{{ secrets.{CANONICAL} }}}}" in workflow
    assert f"{CANONICAL} is not configured" in workflow
    assert "github.token" not in workflow
    assert "secrets.GITHUB_TOKEN" not in workflow

    for legacy_name in LEGACY_NAMES:
        assert legacy_name not in workflow


def test_ai_fix_documents_the_canonical_secret_name():
    documentation = DOCUMENTATION.read_text()

    assert f"named `{CANONICAL}`" in documentation
    assert ADR_0020 in documentation
    assert "named `COPILOT_AGENT_TOKEN`" not in documentation

    # A legacy name may appear only where the document forbids it as an alias.
    alias_paragraphs = [
        paragraph
        for paragraph in documentation.split("\n\n")
        if any(legacy_name in paragraph for legacy_name in LEGACY_NAMES)
    ]
    assert len(alias_paragraphs) == 1
    alias_paragraph = alias_paragraphs[0]
    assert "alias" in alias_paragraph
    assert f"`{CANONICAL} is not configured`" in alias_paragraph
    for legacy_name in LEGACY_NAMES:
        assert f"`{legacy_name}`" in alias_paragraph


def test_no_legacy_secret_name_in_workflows_or_docs():
    workflows = [
        *sorted((ROOT / ".github/workflows").glob("*.yml")),
        *sorted((ROOT / ".github/workflows").glob("*.yaml")),
    ]
    docs = [
        path
        for path in sorted((ROOT / "docs").rglob("*.md"))
        if path != DOCUMENTATION  # checked paragraph by paragraph above
    ]
    searched = [*workflows, *docs]
    assert WORKFLOW in workflows
    assert len(docs) > 1

    for legacy_name in LEGACY_NAMES:
        for path in searched:
            assert legacy_name not in path.read_text(), path
