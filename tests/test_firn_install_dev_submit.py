"""Contract tests for scripts/firn-install-dev-submit.sh (T237f).

A dev submit renders the lane's WorkflowTemplate spec inline, as one `dev-`
labeled Workflow with one cell's parameters. Everything here is offline:
renders use --print or a fake SUBMIT, so no test reaches a cluster.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/firn-install-dev-submit.sh"
TEMPLATE = ROOT / "argo/workflow-templates/run-firn-install-tests.yaml"
ISO_URL = (
    "https://repository.frostyard.org/isos/native/v1/"
    "snosi-installer_20261010000000_x86-64.iso"
)
ISO_SHA256 = "a" * 64
IMAGE_DIGEST = "sha256:" + "b" * 64
REQUIRED = (
    ("iso-url", ISO_URL),
    ("iso-sha256", ISO_SHA256),
    ("image-digest", IMAGE_DIGEST),
)
DEV_LABEL = "snosi.io/trigger"


def head_commit(repo=ROOT):
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def pairs(values):
    return [f"{name}={value}" for name, value in values]


def run_script(*args, env=None):
    environ = dict(os.environ)
    # The script needs PyYAML; use the interpreter running this test.
    environ["PYTHON"] = sys.executable
    environ.pop("SUBMIT", None)
    environ.update(env or {})
    return subprocess.run(
        [str(SCRIPT), *map(str, args)],
        capture_output=True, text=True, env=environ, timeout=120,
    )


def render(values=(), env=None):
    result = run_script("--repo", ROOT, "--print", *pairs(values), env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def template_spec():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))["spec"]


def test_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_print_renders_one_dev_workflow_with_the_branch_templates():
    workflow = render(REQUIRED)

    assert workflow["apiVersion"] == "argoproj.io/v1alpha1"
    assert workflow["kind"] == "Workflow"
    metadata = workflow["metadata"]
    assert set(metadata) == {"generateName", "namespace", "labels"}
    assert metadata["generateName"] == "dev-firn-install-"
    assert metadata["namespace"] == "argo"
    assert metadata["labels"] == {
        DEV_LABEL: "dev",
        "snosi.io/dev-commit": head_commit()[:40],
        "app.kubernetes.io/part-of": "lab",
    }

    spec = workflow["spec"]
    assert spec["serviceAccountName"] == "argo"
    assert spec["entrypoint"] == "run-firn-install-tests"
    # The branch's templates, unchanged: this is what a dev run puts under test.
    assert spec["templates"] == template_spec()["templates"]
    assert spec["activeDeadlineSeconds"] == 10800
    assert spec["arguments"] == {"parameters": [
        {"name": name, "value": value} for name, value in REQUIRED
    ]}
    assert set(spec) == {"serviceAccountName", "entrypoint", "templates",
                         "activeDeadlineSeconds", "arguments"}


def test_defaulted_inputs_can_be_overridden_for_the_dev_cell():
    cell = (
        ("family", "bootc"),
        ("image", "floe"),
        ("encryption", "luks-passphrase"),
        ("secureboot", "false"),
        ("passphrase-prompt-timeout-seconds", "60"),
    )
    workflow = render(REQUIRED + cell)
    assert workflow["spec"]["arguments"]["parameters"] == [
        {"name": name, "value": value} for name, value in REQUIRED + cell
    ]


def test_print_submits_nothing(tmp_path):
    marker = tmp_path / "submitted"
    workflow = render(REQUIRED, env={"SUBMIT": f"touch {marker}"})
    assert workflow["kind"] == "Workflow"
    assert not marker.exists()


def test_without_print_the_json_goes_to_submit_on_stdin(tmp_path):
    expected = render(REQUIRED)
    submitted = tmp_path / "submitted.json"
    result = run_script("--repo", ROOT, *pairs(REQUIRED), env={"SUBMIT": f"tee {submitted}"})
    assert result.returncode == 0, result.stderr
    assert json.loads(submitted.read_text(encoding="utf-8")) == expected


def test_the_dev_label_is_what_the_collector_skips():
    # A dev run is an experiment on the lane, never evidence about an image.
    collector = (ROOT / "scripts/collect_runs.py").read_text(encoding="utf-8")
    assert 'DEV_TRIGGER = "dev"' in collector
    assert 'labels.get("snosi.io/trigger") == DEV_TRIGGER' in collector


def test_usage_error_without_any_parameter():
    result = run_script("--repo", ROOT, "--print")
    assert result.returncode == 64
    assert result.stdout == ""
    assert "usage:" in result.stderr


def test_usage_error_without_a_value():
    result = run_script("--print", *pairs(REQUIRED)[:-1], "image-digest")
    assert result.returncode == 64
    assert "not a NAME=VALUE argument: image-digest" in result.stderr


def test_unknown_parameter_is_refused():
    result = run_script("--repo", ROOT, "--print", *pairs(REQUIRED), "nope=1")
    assert result.returncode == 64
    assert result.stdout == ""
    assert "unknown input parameter: nope" in result.stderr


def test_missing_required_parameter_is_refused():
    result = run_script("--repo", ROOT, "--print", *pairs(REQUIRED[:-1]))
    assert result.returncode == 64
    assert result.stdout == ""
    assert "missing input parameter(s) with no default: image-digest" in result.stderr


def test_an_input_without_a_default_must_be_given_for_each():
    declared = {p["name"] for p in template_spec()["templates"][0]["inputs"]["parameters"]
                if "value" not in p}
    assert declared == {"iso-url", "iso-sha256", "image-digest"}


@pytest.mark.parametrize("name,value,message", [
    ("image-digest", "sha256:XYZ", "image-digest must be sha256:<64 lowercase hex>"),
    ("image-digest", "sha256:" + "A" * 64, "image-digest must be sha256:<64 lowercase hex>"),
    ("image-digest", "b" * 64, "image-digest must be sha256:<64 lowercase hex>"),
    ("iso-sha256", "abc", "iso-sha256 must be <64 lowercase hex>"),
    ("iso-sha256", "A" * 64, "iso-sha256 must be <64 lowercase hex>"),
    ("iso-url", ISO_URL.replace("20261010000000", "latest"), "never a latest tag"),
    ("iso-url", ISO_URL.replace("20261010000000", "LATEST"), "never a latest tag"),
])
def test_bad_iso_or_digest_parameter_is_refused(name, value, message):
    values = [(n, value if n == name else v) for n, v in REQUIRED]
    result = run_script("--repo", ROOT, "--print", *pairs(values))
    assert result.returncode == 64
    assert result.stdout == ""
    assert message in result.stderr


def test_the_submit_file_placeholder_is_refused():
    # A REPLACE_ placeholder is not evidence: it never reaches a run.
    values = [("iso-url", ISO_URL), ("iso-sha256", ISO_SHA256),
              ("image-digest", "REPLACE_FLOE_DIGEST")]
    result = run_script("--repo", ROOT, "--print", *pairs(values))
    assert result.returncode == 64
    assert result.stdout == ""
    assert "image-digest must be sha256:<64 lowercase hex>" in result.stderr


def test_no_tag_registry_write_and_no_kubectl_apply():
    text = SCRIPT.read_text(encoding="utf-8")
    for phrase in ("skopeo copy", "crane", "oras", "podman push", "cosign sign", "kubectl apply"):
        assert phrase not in text, phrase
    # Wider net: any other way to publish or to mutate the cluster by hand.
    writers = re.compile(
        r"\b(skopeo\s+(copy|delete|sync)|crane|oras|regctl|"
        r"(podman|docker|buildah)\s+push|cosign\s+(sign|attach|copy|clean)|"
        r"kubectl\s+(apply|patch|replace|delete|edit)|argo\s+submit|gh\s)\b"
    )
    assert not writers.search(text)
