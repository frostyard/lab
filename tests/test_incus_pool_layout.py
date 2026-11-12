"""Guard installer VM pool selection against host-specific Incus layouts."""

import json
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1] / "argo" / "workflow-templates"
INSTALLERS = ("run-firn-install-tests.yaml",)


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
        'fail() { exit 1; }\n'
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
        'fail() { exit 1; }\n'
        'incus() { printf "%s\\n" \'{"expanded_devices": {}}\'; }\n'
        + "\n".join(lines[start:end])
        + '\nprintf "unexpected continuation\\n"\n'
    )
    result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "Could not resolve Incus root pool" in result.stderr
    assert "unexpected continuation" not in result.stdout
