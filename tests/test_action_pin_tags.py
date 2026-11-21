"""Wiring of the online action pin check (core ADR-0088, ADR-0090).

scripts/check-action-pin-tags.sh is the script of core's ADR-0090, byte for
byte. It resolves each `# vX.Y[.Z]` pin comment to its tag upstream and checks
each branch or frostyard/core pin with GitHub's compare API, so it needs the
network: .github/workflows/action-pin-tags.yml runs it on pull requests that
change a workflow, and nightly-compliance.yml calls that workflow every night.
The offline gate (the Makefile and ci.yml) never runs it.
"""

from hashlib import sha256
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "check-action-pin-tags.sh"
SCRIPT_SOURCE = (
    "frostyard/core@e5d16bb034cdd6c5a21a1cc041fb991dfde79793:"
    "docs/adr/0090-scope-action-pins-and-check-them-repository-wide.md (its bash block)"
)
SCRIPT_SHA256 = "3815a1d93f9e86a26d7376c0bf724501f8d4536bdeed0583d953916428414786"
RUN = "./scripts/check-action-pin-tags.sh"
CALL = "./.github/workflows/action-pin-tags.yml"


def wiring_errors(script, workflow, nightly, offline):
    """Errors for the script bytes, the 2 workflow texts and {name: text} of the offline gate."""
    errors = []
    if sha256(script).hexdigest() != SCRIPT_SHA256:
        errors.append(f"the script is a copy of {SCRIPT_SOURCE}; copy it again and update SCRIPT_SHA256, never edit it")

    parsed = yaml.safe_load(workflow)
    # PyYAML reads the bare `on:` key as the boolean True.
    on = parsed.get(True) or {}
    paths = (on.get("pull_request") or {}).get("paths") or []
    for wanted in (".github/workflows/**", ".github/actions/**"):
        if wanted not in paths:
            errors.append(f"action-pin-tags.yml: pull_request paths lack {wanted}")
    if "workflow_call" not in on:
        errors.append("action-pin-tags.yml: no workflow_call trigger for nightly-compliance.yml")
    if parsed.get("permissions") != {}:
        errors.append("action-pin-tags.yml: top-level permissions must be {}")
    jobs = parsed.get("jobs") or {}
    runs = [
        (job, step)
        for job in jobs.values()
        for step in job.get("steps") or []
        if step.get("run") == RUN
    ]
    if len(runs) != 1:
        errors.append(f"action-pin-tags.yml: {len(runs)} steps run {RUN}, want 1")
    else:
        job, step = runs[0]
        if job.get("permissions") != {"contents": "read"}:
            errors.append(f"action-pin-tags.yml: the job permissions are {job.get('permissions')!r}, want contents: read")
        if (step.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
            errors.append("action-pin-tags.yml: the step needs GH_TOKEN: ${{ github.token }} for the compare API")

    calls = [job for job in (yaml.safe_load(nightly).get("jobs") or {}).values() if job.get("uses") == CALL]
    if len(calls) != 1:
        errors.append(f"nightly-compliance.yml: {len(calls)} jobs call {CALL}, want 1")
    elif calls[0].get("permissions") != {"contents": "read"}:
        errors.append("nightly-compliance.yml: the calling job needs permissions contents: read")

    for name, text in offline.items():
        if "check-action-pin-tags" in text:
            errors.append(f"{name} runs the online check; it needs the network and stays out of make ci")
    return errors


def real_files():
    return {
        "script": SCRIPT.read_bytes(),
        "workflow": (WORKFLOWS / "action-pin-tags.yml").read_text(encoding="utf-8"),
        "nightly": (WORKFLOWS / "nightly-compliance.yml").read_text(encoding="utf-8"),
        "offline": {
            "Makefile": (ROOT / "Makefile").read_text(encoding="utf-8"),
            "ci.yml": (WORKFLOWS / "ci.yml").read_text(encoding="utf-8"),
        },
    }


def test_the_online_check_is_wired():
    assert wiring_errors(**real_files()) == []


def test_only_action_pin_tags_yml_runs_the_script():
    runners = sorted(
        path.name
        for path in list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))
        if "check-action-pin-tags" in path.read_text(encoding="utf-8")
    )
    assert runners == ["action-pin-tags.yml"]


def edit(name, old, new):
    def apply(files):
        assert old in files[name], (name, old)
        files[name] = files[name].replace(old, new)
    return apply


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda files: files.update(script=files["script"] + b"\n"), "never edit it"),
        (edit("workflow", "  pull_request:\n", "  push:\n"), "pull_request paths lack .github/workflows/**"),
        (edit("workflow", "      - '.github/workflows/**'\n", ""), "pull_request paths lack .github/workflows/**"),
        (edit("workflow", "      - '.github/actions/**'\n", ""), "pull_request paths lack .github/actions/**"),
        (edit("workflow", "  workflow_call:\n", ""), "no workflow_call trigger"),
        (edit("workflow", "permissions: {}\n", "permissions:\n  contents: read\n"), "top-level permissions must be {}"),
        (edit("workflow", f"run: {RUN}", "run: 'true'"), "0 steps run"),
        (edit("workflow", "          GH_TOKEN: ${{ github.token }}\n", ""), "needs GH_TOKEN"),
        (edit("workflow", "      contents: read\n", "      contents: write\n"), "want contents: read"),
        (edit("nightly", f"uses: {CALL}", "uses: ./.github/workflows/other.yml"), "0 jobs call"),
        (edit("nightly", f"      contents: read\n    uses: {CALL}", f"      contents: write\n    uses: {CALL}"), "calling job needs"),
        (lambda files: files["offline"].update(Makefile=f"verify:\n\t{RUN}\n"), "Makefile runs the online check"),
    ],
    ids=[
        "script-edited",
        "no-pull-request",
        "no-workflows-path",
        "no-actions-path",
        "no-workflow-call",
        "top-level-permissions",
        "script-not-run",
        "no-token",
        "job-write",
        "not-nightly",
        "nightly-write",
        "in-make-ci",
    ],
)
def test_self_test_a_broken_wiring_fails(change, error):
    files = real_files()
    change(files)
    errors = wiring_errors(**files)
    assert any(error in message for message in errors), errors
