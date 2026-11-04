from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
VALID_DIGEST = "sha256:" + ("a" * 64)
SCRIPT_TEMPLATE_COUNT = 3


def load_template(path: Path | None = None) -> dict:
    yaml_path = path or Path(os.environ.get("IMAGE_POLLER_YAML", ROOT / "argo/workflow-templates/image-poller.yaml"))
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))


def script_templates(path: Path | None = None) -> dict[str, dict]:
    poller = load_template(path)
    templates = {
        template["name"]: template
        for template in poller["spec"]["templates"]
        if "script" in template
    }
    return templates


@pytest.fixture
def script_env(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl_log = tmp_path / "kubectl.log"
    skopeo_log = tmp_path / "skopeo.log"

    (bin_dir / "kubectl").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${KUBECTL_LOG}"
if [[ "$1" == "get" ]]; then
  printf '%s' "${KUBECTL_GET_OUTPUT:-}"
  exit "${KUBECTL_GET_EXIT:-0}"
fi
exit 0
""",
        encoding="utf-8",
    )
    (bin_dir / "skopeo").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${SKOPEO_LOG}"
case "${SKOPEO_MODE:-valid}" in
  fail) exit 1 ;;
  junk) printf 'not-a-digest\n' ;;
  valid) printf '%s\n' "${SKOPEO_DIGEST}" ;;
  *) echo "unknown SKOPEO_MODE" >&2; exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    (bin_dir / "timeout").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
shift
exec "$@"
""",
        encoding="utf-8",
    )
    (bin_dir / "sleep").write_text(
        """#!/usr/bin/env bash
exit 0
""",
        encoding="utf-8",
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "KUBECTL_LOG": str(kubectl_log),
            "SKOPEO_LOG": str(skopeo_log),
            "KUBECTL_GET_EXIT": "0",
            "KUBECTL_GET_OUTPUT": "",
            "SKOPEO_MODE": "valid",
            "SKOPEO_DIGEST": VALID_DIGEST,
        }
    )
    return env, kubectl_log, skopeo_log


def prepare_source(name: str, tmp_path: Path, **values: str) -> str:
    template = script_templates()[name]
    source = template["script"]["source"]
    replacements = {
        "image": "ghcr.io/frostyard/snow",
        "image-tag": "latest",
        "state-key": "snow-latest",
        "remote-digest": VALID_DIGEST,
        "expected-digest": VALID_DIGEST,
    }
    replacements.update({key.replace("_", "-"): value for key, value in values.items()})
    for key, value in replacements.items():
        source = source.replace("{{inputs.parameters." + key + "}}", value)
    source = source.replace("/tmp/", str(tmp_path) + "/")
    return source


def run_script(source: str, tmp_path: Path, env: dict[str, str]):
    return subprocess.run(
        ["bash", "-c", source],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_static_script_contract():
    templates = script_templates()
    assert len(templates) == SCRIPT_TEMPLATE_COUNT
    checked = 0
    for name, template in templates.items():
        script = template["script"]
        assert script["command"] == ["bash"], f"{name}: command is not bash"
        first_line = script["source"].splitlines()[0]
        assert first_line == "set -euo pipefail", f"{name}: first line is {first_line!r}"
        checked += 1
    print(f"Checked {checked} script template(s): bash command and first-line shell guard")


def test_compare_kubectl_get_fails(tmp_path: Path, script_env):
    env, kubectl_log, _ = script_env
    env["KUBECTL_GET_EXIT"] = "1"
    source = prepare_source("compare-local", tmp_path)
    result = run_script(source, tmp_path, env)
    assert result.returncode != 0
    assert "Failed to read stored digest" in result.stderr
    print("Checked 1 kubectl get failure: step failed without changed=true")


def test_compare_empty_stored_digest_means_changed(tmp_path: Path, script_env):
    env, _, _ = script_env
    source = prepare_source("compare-local", tmp_path, remote_digest=VALID_DIGEST)
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    assert (tmp_path / "changed").read_text(encoding="utf-8").strip() == "true"
    assert (tmp_path / "stored-digest").read_text(encoding="utf-8") == ""
    print("Checked 1 empty stored digest: compare changed=true")


def test_compare_same_digest_means_unchanged(tmp_path: Path, script_env):
    env, _, _ = script_env
    env["KUBECTL_GET_OUTPUT"] = VALID_DIGEST
    source = prepare_source("compare-local", tmp_path, remote_digest=VALID_DIGEST)
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    assert (tmp_path / "changed").read_text(encoding="utf-8").strip() == "false"
    print("Checked 1 matching digest: compare changed=false")


def test_compare_none_means_unchanged(tmp_path: Path, script_env):
    env, _, _ = script_env
    source = prepare_source("compare-local", tmp_path, remote_digest="none")
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    assert (tmp_path / "changed").read_text(encoding="utf-8").strip() == "false"
    print("Checked 1 remote=none: compare changed=false")


def test_update_kubectl_get_fails_no_patch(tmp_path: Path, script_env):
    env, kubectl_log, _ = script_env
    env["KUBECTL_GET_EXIT"] = "1"
    source = prepare_source("update-local", tmp_path, expected_digest="")
    result = run_script(source, tmp_path, env)
    assert result.returncode != 0
    assert "Failed to read stored digest" in result.stderr
    calls = kubectl_log.read_text(encoding="utf-8")
    assert "patch configmap" not in calls
    print("Checked 1 failed kubectl get: 0 patch calls")


def test_update_stored_equals_expected_patches(tmp_path: Path, script_env):
    env, kubectl_log, _ = script_env
    env["KUBECTL_GET_OUTPUT"] = VALID_DIGEST
    source = prepare_source("update-local", tmp_path, expected_digest=VALID_DIGEST)
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    calls = kubectl_log.read_text(encoding="utf-8")
    assert "patch configmap image-polling-digests" in calls
    print("Checked 1 stored digest equals expected: 1 patch call")


def test_update_two_empty_strings_allow_patch(tmp_path: Path, script_env):
    env, kubectl_log, _ = script_env
    env["KUBECTL_GET_OUTPUT"] = ""
    source = prepare_source("update-local", tmp_path, expected_digest="")
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    calls = kubectl_log.read_text(encoding="utf-8")
    assert "patch configmap image-polling-digests" in calls
    print("Checked 1 empty stored and expected pair: 1 patch call")


def test_update_stored_differs_expected_no_patch(tmp_path: Path, script_env):
    env, kubectl_log, _ = script_env
    env["KUBECTL_GET_OUTPUT"] = "sha256:" + ("b" * 64)
    source = prepare_source("update-local", tmp_path, expected_digest=VALID_DIGEST)
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    calls = kubectl_log.read_text(encoding="utf-8")
    assert "patch configmap" not in calls
    print("Checked 1 moved state: 0 patch calls")


def test_fetch_skopeo_fails_three_times(tmp_path: Path, script_env):
    env, _, skopeo_log = script_env
    env["SKOPEO_MODE"] = "fail"
    source = prepare_source("fetch-remote", tmp_path)
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    assert (tmp_path / "digest").read_text(encoding="utf-8") == "none\n"
    calls = skopeo_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3
    print("Checked 3 skopeo failures: 1 sentinel output")


def test_fetch_skopeo_junk_means_none(tmp_path: Path, script_env):
    env, _, skopeo_log = script_env
    env["SKOPEO_MODE"] = "junk"
    source = prepare_source("fetch-remote", tmp_path)
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    assert (tmp_path / "digest").read_text(encoding="utf-8") == "none\n"
    assert len(skopeo_log.read_text(encoding="utf-8").splitlines()) == 3
    print("Checked 3 junk-output attempts: 1 sentinel output")


def test_fetch_valid_digest(tmp_path: Path, script_env):
    env, _, skopeo_log = script_env
    env["SKOPEO_MODE"] = "valid"
    source = prepare_source("fetch-remote", tmp_path)
    result = run_script(source, tmp_path, env)
    assert result.returncode == 0
    assert (tmp_path / "digest").read_text(encoding="utf-8") == VALID_DIGEST + "\n"
    assert len(skopeo_log.read_text(encoding="utf-8").splitlines()) == 1
    print("Checked 1 valid digest: 1 digest output")
