"""Contract tests for the read-only Snow lifecycle operator preflight."""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/snow-lifecycle-preflight"
CONFIGMAP = ROOT / "argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml"
RUN_DIR = ROOT / "tests/fixtures/run-records/snow-bootc-lifecycle-run-2026-10-26"


def test_scripts_parse():
    for name in ("run.sh", "checks.sh"):
        subprocess.run(["bash", "-n", str(TOOL / name)], check=True)


def test_preflight_never_writes_to_a_registry_or_cluster():
    # Moving the controlled tag is a publication; the preflight only reads.
    writers = re.compile(r"\b(skopeo\s+(copy|delete|sync)|crane|oras|regctl|(podman|docker|buildah)\s+push|"
                         r"cosign\s+(sign|attach|copy|clean)|kubectl|argo\s+submit|gh\s)\b")
    for name in ("run.sh", "checks.sh"):
        assert not writers.search((TOOL / name).read_text()), name


def test_run_extracts_the_committed_configmap_qa_py():
    # run.sh must use the same payload extraction as the lane's offline tests.
    run = (TOOL / "run.sh").read_text()
    pattern = r'(?m)^  qa.py: \|\n((?:^    .*\n|^\n)*)'
    assert pattern in run
    match = re.search(pattern, CONFIGMAP.read_text())
    assert match and "def preflight(" in textwrap.dedent(match.group(1))


def test_checks_cover_every_trust_field_and_the_lane_gates():
    checks = (TOOL / "checks.sh").read_text()
    for field in ("snosi_commit", "index_key_sha256", "cosign_key_sha256", "mok_cert_sha256",
                  "trust_fingerprint", "iso_url", "iso_sha256", "iso_version", "image_", "version_",
                  "version_tag_", "target_ref"):
        assert field in checks, field
    assert "qa.py validate" in checks and "qa.py preflight" in checks
    # Only the target-tag resolve may be deferred, and only while the tag is not ready.
    assert 'if ref != m["target_ref"]:' in checks
    assert "(( fails == 0 ))" in checks


def test_run_record_manifest_has_the_lane_field_set():
    manifest = json.loads((RUN_DIR / "manifest.json").read_text())
    match = re.search(r'FIELDS = set\("([a-z0-9_ ]+)"\.split\(\)\)', CONFIGMAP.read_text())
    assert match and set(manifest) == set(match.group(1).split())
    assert manifest["target_ref"] == "ghcr.io/frostyard/snow:qa-controlled"
    assert "latest" not in manifest["iso_url"]


def checks_assemblies():
    match = re.search(r"jq -r --arg v \"\$ver\" '(\.v == \$v and \(([^)]*)\) and \.c == \"true\")'",
                      (TOOL / "checks.sh").read_text())
    assert match, "oci_labels predicate"
    return match.group(1), re.findall(r'\.a == "([^"]+)"', match.group(2))


def test_checks_assembly_list_is_the_qa_py_list():
    # checks.sh repeats qa.py ASSEMBLIES; the two lists must stay the same.
    match = re.search(r"(?m)^    ASSEMBLIES = \(([^)]*)\)", CONFIGMAP.read_text())
    assert match
    assert checks_assemblies()[1] == re.findall(r'"([^"]+)"', match.group(1))
    assert "bootc-1.17.4-storage-digest-v1" in checks_assemblies()[1]


def checks_version_rule():
    match = re.search(r"(?m)^rule=\$\(jq -r '([^']+)' \"\$M\"\)$", (TOOL / "checks.sh").read_text())
    assert match, "version rule"
    return match.group(1)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
@pytest.mark.parametrize("probe, rule", [
    # The same split as qa.py validate: version_distinct or version_order.
    ({}, "order"),
    ({"sysext_probe": {"features": ["incus"]}}, "distinct"),
    ({"sysext_probe": {"features": ["incus"], "suite_switch": True}}, "distinct"),
    ({"first_login_probe": {}}, "distinct"),
    ({"report_probe": {}}, "distinct"),
    ({"problems_probe": {"path": "fresh"}}, "distinct"),
    ({"problems_probe": {"path": "update"}}, "order"),
])
def test_checks_version_rule_is_the_qa_py_rule(probe, rule):
    manifest = {"version_n": "20261114224011", "version_n_plus_1": "20261114163823", **probe}
    out = subprocess.run(["jq", "-r", checks_version_rule()], input=json.dumps(manifest),
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == rule


def test_checks_version_rule_names_every_qa_py_distinct_probe():
    # Each probe that qa.py validate gives version_distinct must pick that rule here too.
    expr = checks_version_rule()
    for key in ("sysext_probe", "first_login_probe", "report_probe", "problems_probe"):
        assert key in expr, key
    assert "version_distinct" in CONFIGMAP.read_text()


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
@pytest.mark.parametrize("assembly, ok", [
    ("bootc-1.17.3-storage-digest-v1", True),
    ("bootc-1.17.4-storage-digest-v1", True),
    ("bootc-1.17.5-storage-digest-v1", False),
    ("bootc-1.17.4-storage-digest-v2", False),
    ("bootc-1.17.4-storage-digest-v1-forky", False),
    (None, False),
])
def test_checks_oci_labels_accepts_only_listed_assemblies(assembly, ok):
    labels = {"v": "20261113000000", "a": assembly, "c": "true"}
    out = subprocess.run(["jq", "-r", "--arg", "v", "20261113000000", checks_assemblies()[0]],
                         input=json.dumps(labels), capture_output=True, text=True, check=True).stdout.strip()
    assert out == ("true" if ok else "false")
