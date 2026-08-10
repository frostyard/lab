import copy
import json
from pathlib import Path

from policies.check_agent_governance import (
    DENIED_ACTIONS,
    REQUIRED_BOUNDARIES,
    REQUIRED_CONTROLS,
    REQUIRED_EXCEPTION_CONTROLS,
    main,
    validate_policy,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "policies/agent-governance.json"


def load_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def test_committed_agent_governance_policy_is_valid():
    assert validate_policy(load_policy()) == []
    assert main([str(POLICY)]) == 0


def test_every_prohibited_agent_action_fails_open_or_missing():
    policy = load_policy()
    for action in DENIED_ACTIONS:
        changed = copy.deepcopy(policy)
        changed["agentActions"][action] = "allow"
        assert f"agentActions.{action} must be deny" in validate_policy(changed)

        missing = copy.deepcopy(policy)
        del missing["agentActions"][action]
        assert validate_policy(missing), action


def test_every_required_change_and_exception_control_fails_when_disabled():
    policy = load_policy()
    for field, controls in (
        ("changeControls", REQUIRED_CONTROLS),
        ("exceptions", REQUIRED_EXCEPTION_CONTROLS),
    ):
        for control in controls:
            changed = copy.deepcopy(policy)
            changed[field][control] = False
            assert f"{field}.{control} must be true" in validate_policy(changed)


def test_every_protected_boundary_is_required():
    policy = load_policy()
    for boundary in REQUIRED_BOUNDARIES:
        changed = copy.deepcopy(policy)
        changed["protectedBoundaries"].remove(boundary)
        assert any(boundary in error for error in validate_policy(changed))


def test_policy_fails_closed_on_unknown_schema_and_invalid_json(tmp_path, capsys):
    policy = load_policy()
    policy["unreviewedEscape"] = True
    assert any("top-level keys" in error for error in validate_policy(policy))

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    assert main([str(invalid)]) == 2
    assert "Policy configuration error" in capsys.readouterr().out
