"""Contract tests for scripts/snow-lifecycle-dev-submit.sh (T237f).

A dev submit renders the committed WorkflowTemplate inline, swaps its ConfigMap
scripts volume for raw input artifacts, and submits one `dev-` labeled
Workflow. Everything here is offline: renders use --print or a fake SUBMIT, so
no test reaches a cluster, a registry or a tag.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/snow-lifecycle-dev-submit.sh"
TEMPLATE = ROOT / "argo/workflow-templates/run-snow-bootc-lifecycle.yaml"
CONFIGMAP = ROOT / "argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml"
SCRIPTS_VOLUME = "scripts"
MOUNT_PATH = "/opt/snow-qa"
DEV_LABEL = "snosi.io/trigger"


def entry_template(spec):
    return next(t for t in spec["templates"] if t["name"] == spec["entrypoint"])


def head_commit(repo=ROOT):
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def manifest_file(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"family": "snow", "product": "snow"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


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


def render(tmp_path, *args, env=None):
    manifest = manifest_file(tmp_path)
    result = run_script("--repo", ROOT, *args, manifest, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), manifest


def test_scripts_parse():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_print_renders_the_template_spec_with_script_artifacts(tmp_path):
    workflow, manifest = render(tmp_path, "--print")
    spec = workflow["spec"]
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    configmap = yaml.safe_load(CONFIGMAP.read_text(encoding="utf-8"))
    data = configmap["data"]

    # Build what the template spec must look like with the scripts volume gone.
    expected = copy.deepcopy(template["spec"])
    volume = next(v for v in expected["volumes"] if v["name"] == SCRIPTS_VOLUME)
    assert volume["configMap"]["name"] == configmap["metadata"]["name"]
    expected["volumes"] = [v for v in expected["volumes"] if v["name"] != SCRIPTS_VOLUME]
    expected_entry = entry_template(expected)
    removed = next(
        m for m in expected_entry["container"]["volumeMounts"] if m["name"] == SCRIPTS_VOLUME
    )
    assert removed["mountPath"] == MOUNT_PATH
    expected_entry["container"]["volumeMounts"] = [
        m for m in expected_entry["container"]["volumeMounts"] if m["name"] != SCRIPTS_VOLUME
    ]

    # Every field but the documented changes is the template's spec: the
    # arguments, the artifacts and the unpacking entry command.
    actual = copy.deepcopy(spec)
    del actual["arguments"]
    del entry_template(actual)["inputs"]["artifacts"]
    command = entry_template(actual)["container"].pop("command")
    original = expected_entry["container"].pop("command")
    assert actual == expected
    assert original == ["/bin/bash", MOUNT_PATH + "/run.sh"]
    assert command[:2] == ["/bin/bash", "-c"] and command[3:] == ["dev-unpack", *original]
    assert all(v["name"] != SCRIPTS_VOLUME for v in spec["volumes"])
    assert all(
        m["name"] != SCRIPTS_VOLUME
        for m in entry_template(spec)["container"]["volumeMounts"]
    )

    # inputs.artifacts: exactly the ConfigMap data keys, gzip+base64 under the
    # packed path; the entry command restores them byte-equal at the mount
    # path with the ConfigMap volume's mode.
    artifacts = entry_template(spec)["inputs"]["artifacts"]
    packed = MOUNT_PATH + ".packed"
    assert len(artifacts) == len(data) == len({a["path"] for a in artifacts})
    assert len({a["name"] for a in artifacts}) == len(artifacts)
    assert {a["path"] for a in artifacts} == {packed + "/" + key + ".gz.b64" for key in data}
    assert volume["configMap"]["defaultMode"] == 0o555
    root = tmp_path / "root"
    for artifact in artifacts:
        target = root / artifact["path"].lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact["raw"]["data"], encoding="ascii")
    unpack = command[2].replace(MOUNT_PATH, str(root) + MOUNT_PATH)
    subprocess.run(["/bin/bash", "-c", unpack, "dev-unpack", "true"], check=True, timeout=60)
    for key, text in data.items():
        restored = root / MOUNT_PATH.lstrip("/") / key
        assert restored.read_bytes() == text.encode("utf-8"), key
        assert restored.stat().st_mode & 0o7777 == volume["configMap"]["defaultMode"], key
    # Argo offloads a template above MaxEnvVarLen (128 KiB) to a ConfigMap.
    assert len(json.dumps(entry_template(spec))) < 131072 // 2

    # spec.arguments carries the manifest text and never keeps the VM.
    assert spec["arguments"] == {
        "parameters": [
            {"name": "manifest-json", "value": manifest.read_text(encoding="utf-8")},
            {"name": "keep-vm-on-failure", "value": "false"},
        ]
    }


def test_print_names_the_workflow_as_a_dev_run(tmp_path):
    workflow, _ = render(tmp_path, "--print")

    assert workflow["apiVersion"] == "argoproj.io/v1alpha1"
    assert workflow["kind"] == "Workflow"
    metadata = workflow["metadata"]
    assert set(metadata) == {"generateName", "namespace", "labels"}
    assert metadata["generateName"] == "dev-snow-bootc-lifecycle-"
    assert metadata["namespace"] == "argo"
    assert metadata["labels"] == {DEV_LABEL: "dev", "snosi.io/dev-commit": head_commit()}


def test_print_submits_nothing(tmp_path):
    marker = tmp_path / "submitted"

    workflow, _ = render(tmp_path, "--print", env={"SUBMIT": f"touch {marker}"})

    assert workflow["kind"] == "Workflow"
    assert not marker.exists()


def test_without_print_the_json_goes_to_submit_on_stdin(tmp_path):
    expected, _ = render(tmp_path, "--print")
    submitted = tmp_path / "submitted.json"

    result, _ = render(tmp_path, env={"SUBMIT": f"tee {submitted}"})

    assert json.loads(submitted.read_text(encoding="utf-8")) == expected
    assert result == expected


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
