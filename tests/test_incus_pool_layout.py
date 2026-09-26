"""Guard installer VM pool selection against host-specific Incus layouts."""

import json
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1] / "argo" / "workflow-templates"
INSTALLERS = (
    "run-firn-install-tests.yaml",
    "run-incus-install-tests.yaml",
    "run-incus-bootc-install-tests.yaml",
)


def source(name: str) -> str:
    manifest = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    return manifest["spec"]["templates"][0]["script"]["source"]


def test_workflow_templates_do_not_assume_lab_pool():
    paths = sorted(ROOT.glob("*.yaml"))
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "storage-pools/lab/" not in text, path.name
        assert "pool=lab" not in text, path.name
        assert not re.search(r"storage volume (?:create|delete) lab\b", text), path.name


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_resolves_and_validates_instance_root_pool(name):
    script = source(name)
    init = script.index('incus init "${VM}" --empty --vm')
    after_init = script[init:]
    assert " -s " not in after_init.splitlines()[0]
    assert 'POOL="$(incus query "/1.0/instances/${VM}" | python3 -c ' in after_init
    assert 'json.load(sys.stdin)["expanded_devices"]["root"]["pool"]' in after_init
    assert '[[ "${POOL}" =~ ^[A-Za-z0-9._-]+$ ]]' in after_init
    assert 'echo "Invalid Incus root pool:' in after_init
    assert 'set -euo pipefail' in script[:init]
    assert re.search(r"apt-get install .*\bpython3\b", script[:init])


@pytest.mark.parametrize("name", INSTALLERS)
@pytest.mark.parametrize("pool", ["default", "", "../escape", "bad/pool", "pool name", None, True, 123])
def test_installer_pool_resolution_rejects_empty_or_unsafe_values(name, pool):
    script = source(name)
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('POOL="$(incus query '))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith('incus config device add '))
    resolution = "\n".join(lines[start:end])
    # Run the actual manifest shell fragment, replacing only the external daemon.
    command = (
        'set -euo pipefail\nVM=test-vm\n'
        'incus() { [[ "$1" == query && "$2" == "/1.0/instances/${VM}" ]] || return 1; '
        'printf "%s\\n" "$INSTANCE_JSON"; }\n'
        f'{resolution}\nprintf "resolved=%s\\n" "$POOL"\n'
    )
    instance = {"expanded_devices": {"root": {"pool": pool}}}
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "INSTANCE_JSON": json.dumps(instance)},
        text=True,
        capture_output=True,
        check=False,
    )
    if pool == "default":
        assert result.returncode == 0, result.stderr
        assert result.stdout == "resolved=default\n"
    else:
        assert result.returncode != 0
        assert "Incus root pool" in result.stderr
        assert "resolved=" not in result.stdout


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_pool_resolution_fails_when_root_device_is_missing(name):
    script = source(name)
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('POOL="$(incus query '))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith('incus config device add '))
    command = (
        'set -euo pipefail\nVM=test-vm\n'
        'incus() { printf "%s\\n" \'{"expanded_devices": {}}\'; }\n'
        + "\n".join(lines[start:end])
        + '\nprintf "unexpected continuation\\n"\n'
    )
    result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "Could not resolve Incus root pool" in result.stderr
    assert "unexpected continuation" not in result.stdout


@pytest.mark.parametrize(
    ("vm_exists", "query_result", "query_pool", "profile_pool", "expected_calls", "expected_error"),
    [
        (False, "ok", "ignored", "default", ["info test-vm", "profile device get default root pool", "delete --force test-vm", "storage volume delete default test-vm-scratch"], ""),
        (True, "fail", "", "default", ["info test-vm", "query /1.0/instances/test-vm", "delete --force test-vm"], "Could not resolve Incus root pool"),
        (True, "ok", "custom", "default", ["info test-vm", "query /1.0/instances/test-vm", "delete --force test-vm", "storage volume delete custom test-vm-scratch"], ""),
        (True, "ok", "../escape", "default", ["info test-vm", "query /1.0/instances/test-vm", "delete --force test-vm"], "Invalid Incus root pool"),
    ],
)
def test_bootc_cleanup_deletes_vm_even_if_pool_cannot_be_resolved(
    tmp_path, vm_exists, query_result, query_pool, profile_pool, expected_calls, expected_error
):
    script = source("run-incus-bootc-install-tests.yaml")
    cleanup = script[script.index("cleanup() {"):script.index("trap cleanup EXIT")]
    stub = tmp_path / "incus"
    stub.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$1" in\n'
        '  info) [[ "$VM_EXISTS" == true ]] ;;\n'
        '  query) [[ "$QUERY_RESULT" == ok ]] || exit 1; '
        'printf \'{"expanded_devices":{"root":{"pool":"%s"}}}\\n\' "$QUERY_POOL" ;;\n'
        '  profile) printf "%s\\n" "$PROFILE_POOL" ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    calls = tmp_path / "calls"
    command = (
        'set -euo pipefail\nVM=test-vm\nSCRATCH_VOL=test-vm-scratch\n'
        'KEEP_ON_FAILURE=false\n'
        f'{cleanup}\ntrap cleanup EXIT\nexit 23\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "PATH": f'{tmp_path}:{os.environ["PATH"]}',
             "CALLS": str(calls), "VM_EXISTS": str(vm_exists).lower(),
             "QUERY_RESULT": query_result, "QUERY_POOL": query_pool,
             "PROFILE_POOL": profile_pool},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 23, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == expected_calls
    assert expected_error in result.stderr


@pytest.mark.parametrize("pool", [None, True, 123])
def test_bootc_cleanup_rejects_non_string_instance_pool_and_deletes_vm(tmp_path, pool):
    script = source("run-incus-bootc-install-tests.yaml")
    cleanup = script[script.index("cleanup() {"):script.index("trap cleanup EXIT")]
    stub = tmp_path / "incus"
    stub.write_text(
        '#!/bin/bash\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'if [[ "$1" == query ]]; then printf "%s\\n" "$INSTANCE_JSON"; fi\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    calls = tmp_path / "calls"
    command = (
        'set -euo pipefail\nVM=test-vm\nSCRATCH_VOL=test-vm-scratch\n'
        'KEEP_ON_FAILURE=false\n'
        f'{cleanup}\ntrap cleanup EXIT\nexit 23\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "PATH": f'{tmp_path}:{os.environ["PATH"]}',
             "CALLS": str(calls),
             "INSTANCE_JSON": json.dumps({"expanded_devices": {"root": {"pool": pool}}})},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 23, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "info test-vm", "query /1.0/instances/test-vm", "delete --force test-vm"
    ]
    assert "Could not resolve Incus root pool" in result.stderr


def test_bootc_scratch_creation_and_hint_use_pool():
    script = source("run-incus-bootc-install-tests.yaml")
    assert 'incus storage volume create "${POOL}"' in script
    assert 'pool="${POOL}"' in script


def test_bootc_keep_on_failure_hint_uses_validated_pool(tmp_path):
    script = source("run-incus-bootc-install-tests.yaml")
    cleanup = script[script.index("cleanup() {"):script.index("trap cleanup EXIT")]
    stub = tmp_path / "incus"
    stub.write_text(
        '#!/bin/bash\n'
        'if [[ "$1" == query ]]; then printf \'{"expanded_devices":{"root":{"pool":"custom"}}}\\n\'; fi\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    command = (
        'set -euo pipefail\nVM=test-vm\nSCRATCH_VOL=test-vm-scratch\n'
        'KEEP_ON_FAILURE=true\n'
        f'{cleanup}\ntrap cleanup EXIT\nexit 23\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "PATH": f'{tmp_path}:{os.environ["PATH"]}'},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 23
    assert "incus storage volume delete custom test-vm-scratch" in result.stderr


def test_bootc_success_with_unresolvable_pool_still_deletes_vm(tmp_path):
    script = source("run-incus-bootc-install-tests.yaml")
    cleanup = script[script.index("cleanup() {"):script.index("trap cleanup EXIT")]
    stub = tmp_path / "incus"
    stub.write_text(
        '#!/bin/bash\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'if [[ "$1" == query ]]; then exit 1; fi\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    calls = tmp_path / "calls"
    command = (
        'set -euo pipefail\nVM=test-vm\nSCRATCH_VOL=test-vm-scratch\n'
        'KEEP_ON_FAILURE=true\n'
        f'{cleanup}\ntrap cleanup EXIT\nexit 0\n'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "PATH": f'{tmp_path}:{os.environ["PATH"]}', "CALLS": str(calls)},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "info test-vm", "query /1.0/instances/test-vm", "delete --force test-vm"
    ]
    assert "Could not resolve Incus root pool" in result.stderr
