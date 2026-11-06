"""The high-risk path list mirrors the governance policy (core ADR-0068, ADR-0019)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RISK_PATHS = ROOT / ".github/risk-paths.json"
POLICY = ROOT / "policies/agent-governance.json"


def risk_entries():
    return json.loads(RISK_PATHS.read_text())["paths"]


def test_every_why_names_a_protected_boundary():
    boundaries = set(json.loads(POLICY.read_text())["protectedBoundaries"])
    bad = [entry for entry in risk_entries() if entry["why"].split(":", 1)[0] not in boundaries]
    assert not bad, f"each why must start with a protectedBoundaries id and a colon: {bad}"


def test_reconciled_state_and_deploy_paths_are_listed():
    # docs/risk-tiers.md: argo/workflow-templates/, manifests/ and argocd/ alter
    # reconciled cluster state; .github/workflows/ holds the Pages deploy and the
    # token permissions.
    globs = {entry["glob"] for entry in risk_entries()}
    required = {"argo/workflow-templates/**", "manifests/**", "argocd/**", ".github/workflows/**",
                "policies/**", ".github/risk-paths.json"}
    assert required <= globs, sorted(required - globs)
