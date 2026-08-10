#!/usr/bin/env python3
"""Validate Lab's machine-readable agent governance policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


EXPECTED_TOP_LEVEL = {
    "version",
    "defaultDecision",
    "agentActions",
    "changeControls",
    "protectedBoundaries",
    "exceptions",
}
DENIED_ACTIONS = {
    "approveOwnException",
    "forceReconciliation",
    "mergePullRequest",
    "modifyProtectedEnvironment",
    "mutateCluster",
    "publishResults",
    "rotateCredentials",
    "submitArgoWorkflow",
}
REQUIRED_CONTROLS = {
    "gitOpsRequired",
    "humanReviewRequired",
    "pullRequestRequired",
    "riskClassificationRequired",
    "validationEvidenceRequired",
}
REQUIRED_BOUNDARIES = {
    "argo-cd-ownership",
    "cluster-credentials",
    "evidence-publication",
    "image-digest-selection",
    "kubernetes-rbac",
    "quality-gates",
    "secret-references",
    "workflow-permissions",
}
REQUIRED_EXCEPTION_CONTROLS = {
    "compensatingControlsRequired",
    "maintainerApprovalRequired",
    "restorationPlanRequired",
}


def _require_true_map(value: Any, expected: set[str], field: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{field} must be an object"]
    errors = []
    if set(value) != expected:
        errors.append(f"{field} keys must be exactly {', '.join(sorted(expected))}")
    for key in expected:
        if value.get(key) is not True:
            errors.append(f"{field}.{key} must be true")
    return errors


def validate_policy(document: Any) -> list[str]:
    """Return every policy violation without mutating the document."""
    if not isinstance(document, dict):
        return ["policy must be a JSON object"]

    errors = []
    if set(document) != EXPECTED_TOP_LEVEL:
        errors.append(
            "top-level keys must be exactly "
            + ", ".join(sorted(EXPECTED_TOP_LEVEL))
        )
    if type(document.get("version")) is not int or document.get("version") != 1:
        errors.append("version must be integer 1")
    if document.get("defaultDecision") != "deny":
        errors.append("defaultDecision must be deny")

    actions = document.get("agentActions")
    if not isinstance(actions, dict):
        errors.append("agentActions must be an object")
    else:
        if set(actions) != DENIED_ACTIONS:
            errors.append(
                "agentActions keys must be exactly "
                + ", ".join(sorted(DENIED_ACTIONS))
            )
        for action in DENIED_ACTIONS:
            if actions.get(action) != "deny":
                errors.append(f"agentActions.{action} must be deny")

    errors.extend(
        _require_true_map(
            document.get("changeControls"), REQUIRED_CONTROLS, "changeControls"
        )
    )
    errors.extend(
        _require_true_map(
            document.get("exceptions"),
            REQUIRED_EXCEPTION_CONTROLS,
            "exceptions",
        )
    )

    boundaries = document.get("protectedBoundaries")
    if not isinstance(boundaries, list) or not all(
        isinstance(boundary, str) and boundary for boundary in boundaries
    ):
        errors.append("protectedBoundaries must be a list of non-empty strings")
    else:
        if boundaries != sorted(set(boundaries)):
            errors.append("protectedBoundaries must be sorted and unique")
        missing = REQUIRED_BOUNDARIES - set(boundaries)
        if missing:
            errors.append(
                "protectedBoundaries is missing " + ", ".join(sorted(missing))
            )
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "policy",
        nargs="?",
        type=Path,
        default=root / "policies" / "agent-governance.json",
    )
    args = parser.parse_args(argv)

    try:
        document = json.loads(args.policy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Policy configuration error: {error}")
        return 2

    errors = validate_policy(document)
    if errors:
        for error in errors:
            print(f"Policy violation: {error}")
        return 1
    print("Agent governance policy passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
