"""The risk-path list mirrors the governance policy (core ADR-0068, ADR-0019, ADR-0078)."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RISK_PATHS = ROOT / ".github/risk-paths.json"
POLICY = ROOT / "policies/agent-governance.json"
WORKFLOWS = ROOT / ".github/workflows"
CALLER = WORKFLOWS / "risk-paths.yml"

LEVELS = ("L1", "L2", "L3", "L4")
# Rule H of core's docs/specs/risk-paths.md: a governance-like why is L2.
GOVERNANCE_CATEGORIES = {"governance", "workflow-permissions", "workflow-and-permissions",
                         "quality-gates"}
# Rule C of core's docs/specs/risk-paths.md: the globs that match the list and
# the caller are L1 or L2.
REQUIRED_GLOBS = {".github/risk-paths.json"}


def risk_list():
    return json.loads(RISK_PATHS.read_text())


def risk_entries():
    return risk_list()["paths"]


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


def test_the_list_is_version_2_with_a_level_on_every_entry():
    listed = risk_list()
    assert listed["schema_version"] == 2
    assert listed["default_level"] in LEVELS, listed["default_level"]
    bad = [entry for entry in risk_entries() if entry.get("level") not in LEVELS]
    assert not bad, f"each entry needs a level of L1 to L4: {bad}"


def test_no_entry_carries_a_v1_shape():
    bad = [entry for entry in risk_entries() if set(entry) != {"glob", "level", "why"}]
    assert not bad, f"each entry has exactly glob, level and why: {bad}"


def test_the_list_and_the_caller_are_l1_or_l2():
    # Rule C of core's docs/specs/risk-paths.md. `.github/workflows/**` matches
    # the caller, and the list names itself.
    globs = {entry["glob"]: entry["level"] for entry in risk_entries()}
    for glob in REQUIRED_GLOBS | {".github/workflows/**"}:
        assert glob in globs, glob
        assert globs[glob] in {"L1", "L2"}, (glob, globs[glob])


def test_governance_entries_are_at_least_l2():
    # Rule H: the governance categories are L2. A stricter L1 stays allowed.
    bad = [entry for entry in risk_entries()
           if entry["why"].split(":", 1)[0] in GOVERNANCE_CATEGORIES
           and entry["level"] not in {"L1", "L2"}]
    assert not bad, f"a governance entry must not be looser than L2: {bad}"


def test_the_caller_runs_core_pr_triage_at_a_pinned_commit():
    # Rule E of core's docs/specs/risk-paths.md, ADR-0021: a full commit SHA.
    workflow = yaml.safe_load(CALLER.read_text())
    jobs = workflow["jobs"]
    assert list(jobs) == ["pr-triage"], list(jobs)
    assert jobs["pr-triage"]["permissions"] == {"contents": "read", "pull-requests": "write"}
    uses = jobs["pr-triage"]["uses"]
    name, _, pin = uses.partition("@")
    assert name == "frostyard/core/.github/workflows/pr-triage.yml", uses
    assert len(pin) == 40 and set(pin) <= set("0123456789abcdef"), uses
    assert workflow["permissions"] == {}
    # PyYAML reads the bare `on:` key as the boolean True.
    assert "labeled" in workflow[True]["pull_request_target"]["types"]
