"""Contract tests for scripts/snow-lifecycle-dev-submit.sh (T237f).

A dev submit renders the committed WorkflowTemplate inline, swaps its ConfigMap
scripts volume for raw input artifacts, and submits one `dev-` labeled
Workflow. Everything here is offline: renders use --print or a fake SUBMIT, so
no test reaches a cluster, a registry or a tag.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import os
import re
import subprocess
import sys
import tarfile
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


def controller_can_create_configmaps():
    rbac = list(yaml.safe_load_all((ROOT / "manifests/argo-rbac.yaml").read_text(encoding="utf-8")))
    controller = ("ServiceAccount", "argo-workflows-workflow-controller", "argo")
    roles = {
        doc["metadata"]["name"]
        for doc in rbac
        if doc["kind"] == "RoleBinding"
        and doc["metadata"]["namespace"] == "argo"
        and controller in {(s["kind"], s["name"], s.get("namespace")) for s in doc["subjects"]}
    }
    return any(
        doc["kind"] == "Role"
        and doc["metadata"]["name"] in roles
        and any(
            "" in rule["apiGroups"] and "configmaps" in rule["resources"] and "create" in rule["verbs"]
            for rule in doc["rules"]
        )
        for doc in rbac
    )


def test_template_offload_needs_the_controller_configmap_grant():
    # A template above MaxEnvVarLen depends on this grant; the size check in
    # the render test calls it only for such a template.
    assert controller_can_create_configmaps()


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

    # inputs.artifacts: 1 gzipped tar of the whole ConfigMap data under the packed
    # path; the entry command restores every key byte-equal at the mount path
    # with the ConfigMap volume's mode. 1 tar, not 1 artifact per key, keeps the
    # rendered spec small.
    artifacts = entry_template(spec)["inputs"]["artifacts"]
    packed = MOUNT_PATH + ".packed"
    assert len(artifacts) == 1, artifacts
    assert artifacts[0]["name"] == "scripts"
    assert artifacts[0]["path"] == packed + "/scripts.tar.gz.b64"
    assert volume["configMap"]["defaultMode"] == 0o555
    root = tmp_path / "root"
    for artifact in artifacts:
        target = root / artifact["path"].lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact["raw"]["data"], encoding="ascii")
    unpack = command[2].replace(MOUNT_PATH, str(root) + MOUNT_PATH)
    subprocess.run(["/bin/bash", "-c", unpack, "dev-unpack", "true"], check=True, timeout=60)
    assert set(data) == set(p.name for p in (root / MOUNT_PATH.lstrip("/")).iterdir())
    for key, text in data.items():
        restored = root / MOUNT_PATH.lstrip("/") / key
        assert restored.read_bytes() == text.encode("utf-8"), key
        assert restored.stat().st_mode & 0o7777 == volume["configMap"]["defaultMode"], key
    # The tar holds the largest key first (name breaks a tie): that order packs
    # about 1 KiB smaller than name order and changes no unpacked byte.
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(artifacts[0]["raw"]["data"])), mode="r:gz") as tar:
        members = tar.getnames()
    assert members == sorted(data, key=lambda key: (-len(data[key].encode("utf-8")), key))
    # Argo offloads a template above MaxEnvVarLen (128 KiB) to a ConfigMap that
    # the workflow controller creates (Role argo-controller-template-offload in
    # manifests/argo-rbac.yaml). Kubernetes caps ConfigMap data at 1 MiB; keep
    # 64 KiB of that for the key and Go's JSON encoding of the same template.
    size = len(json.dumps(entry_template(spec)))
    assert size < 1048576 - 65536
    if size >= 131072:
        assert controller_can_create_configmaps()

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
