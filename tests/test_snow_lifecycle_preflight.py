"""Contract tests for the read-only Snow lifecycle operator preflight."""

import json
import re
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/snow-lifecycle-preflight"
CONFIGMAP = ROOT / "argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml"
RUN_DIR = ROOT / "docs/plans/snow-bootc-lifecycle-run-2026-10-26"


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
