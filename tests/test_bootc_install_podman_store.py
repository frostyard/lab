"""frostyard/lab#161: the install unit orders after the ISO podman units and guards the lane store."""

from pathlib import Path
import re
import subprocess

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "argo/workflow-templates/run-incus-bootc-install-tests.yaml"
STORE = "/var/lib/containers/storage"


@pytest.fixture
def source():
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    return template["spec"]["templates"][0]["script"]["source"]


@pytest.fixture
def install(source):
    return source.split("INSTALL_SH=\"#!/bin/bash\n", 1)[1].split("$(cat <<'SCRIPT'\n", 1)[1].split("\nSCRIPT\n", 1)[0]


def _guard(install):
    start = install.index("if [ -e /var/lib/containers/storage/db.sql ]")
    return install[start:install.index("\nfi\n", start) + 4]


def test_unit_orders_after_podman_units(source):
    unit = source.split('UNIT="[Unit]\n', 1)[1].split("[Service]", 1)[0]
    after = [l for l in unit.splitlines() if l.strip().startswith("After=")]
    assert len(after) == 1
    for name in ("network-online.target", "podman-restart.service", "podman-auto-update.service"):
        assert name in after[0].split("=", 1)[1].split()


def test_db_guard_between_mount_and_first_podman(install):
    guard = install.index("if [ -e /var/lib/containers/storage/db.sql ]")
    mount = install.index('mount "${SCRATCH}" /var/lib/containers')
    # First podman invocation on a non-comment line (comments mention podman).
    first_podman = next(m.start() for m in re.finditer(r"^[^#\n]*\bpodman ", install[mount:], re.M)) + mount
    assert mount < guard < first_podman
    text = _guard(install)
    assert "db.sql" in text and "libpod/bolt_state.db" in text
    assert "${M_FAIL}" in text and "exit 1" in text


def test_err_trap_after_set_and_before_commands(install):
    lines = [l.strip() for l in install.splitlines() if l.strip() and not l.strip().startswith("#")]
    trap = next(i for i, l in enumerate(lines) if l.startswith("trap ") and "ERR" in l)
    assert lines.index("set -eux") < lines.index("exec > >(tee /dev/console) 2>&1") < trap
    assert trap < next(i for i, l in enumerate(lines) if l.startswith("for i in"))
    assert "echo \"${M_FAIL}" in lines[trap]
    assert lines[trap].startswith("trap '")  # single quotes: no marker in the set -x trace


@pytest.mark.parametrize("present,code", [(None, 0), ("db.sql", 1), ("libpod/bolt_state.db", 1)])
def test_guard_behavior(install, tmp_path, present, code):
    store = tmp_path / "storage"
    (store / "libpod").mkdir(parents=True)
    if present:
        (store / present).write_text("x")
    guard = _guard(install).replace(STORE, str(store))
    script = f"M_FAIL=MARK_FAIL\nsystemctl() {{ echo units; }}\n{guard}\necho PASSED\n"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == code
    if code:
        assert "MARK_FAIL" in r.stdout and "PASSED" not in r.stdout
    else:
        assert "PASSED" in r.stdout and "MARK_FAIL" not in r.stdout
