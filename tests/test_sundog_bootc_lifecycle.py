"""Offline contract tests for the Sundog product of the lifecycle lane.

Sundog (the KDE Plasma desktop image) is one more entry in `PRODUCTS`; the
manifest field `product` selects it, as for Floe. The generic product logic is
covered in test_floe_bootc_lifecycle.py; these tests cover what is specific to
Sundog: its manifest, its repository, its catalog entry and its policy rule.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import test_snow_bootc_lifecycle as snow
from test_floe_bootc_lifecycle import (  # noqa: F401  (qa is a fixture)
    CHECKS_SH, DEV_SUBMIT, FLOE, KEY, N, NEXT, SNOW, SNOW_MANIFEST, ROOT, SOURCE,
    floe_catalog, qa, shipped, swap)

SUNDOG = "ghcr.io/frostyard/sundog"
RUN_RECORD = ROOT / "docs/plans/sundog-bootc-lifecycle-run-2026-10-30/manifest.json"


def sundog_manifest():
    m = SNOW_MANIFEST()
    m.update(family="sundog", product="sundog", image_n=f"{SUNDOG}@sha256:{N}",
             version_tag_n=f"{SUNDOG}:{m['version_n']}", image_n_plus_1=f"{SUNDOG}@sha256:{NEXT}",
             version_tag_n_plus_1=f"{SUNDOG}:{m['version_n_plus_1']}", target_ref=f"{SUNDOG}:qa-controlled")
    return m


def test_sundog_manifest_normalizes_and_validates(qa, tmp_path, capsys):
    m = sundog_manifest()
    assert qa.PRODUCTS["sundog"] == SUNDOG
    assert qa.normalize(dict(m)) == m
    raw, out = tmp_path / "raw.json", tmp_path / "normalized.json"
    raw.write_text(json.dumps(m))
    assert qa.main(["validate", str(raw), str(out)]) == 0
    canonical = out.read_bytes()
    assert capsys.readouterr().out.splitlines()[-1] == hashlib.sha256(canonical).hexdigest()
    assert b'"product":"sundog"' in canonical


@pytest.mark.parametrize("names", [
    ("image_n",), ("image_n_plus_1",), ("version_tag_n",), ("version_tag_n_plus_1",), ("target_ref",),
])
@pytest.mark.parametrize("other", [SNOW, FLOE])
def test_sundog_rejects_a_reference_in_another_product_repository(qa, names, other):
    m = sundog_manifest()
    swap(m, SUNDOG, other, *names)
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


@pytest.mark.parametrize("change", [
    lambda m: m.update(family="floe"),
    lambda m: m.update(family="snow"),
    lambda m: m.update(product="floe"),
    lambda m: m.update(family="sundog-ab", product="sundog-ab"),
    lambda m: m.update(product="Sundog", family="Sundog"),
    lambda m: m.update(target_ref=f"{SUNDOG}:latest"),
])
def test_sundog_rejects_a_mixed_product_or_an_uncontrolled_target(qa, change):
    m = sundog_manifest()
    change(m)
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


@pytest.mark.parametrize("product, repo", [("floe", FLOE), ("snow", SNOW)])
def test_other_products_reject_sundog_references(qa, product, repo):
    m = SNOW_MANIFEST()
    if product == "floe":
        m.update(family="floe", product="floe")
        swap(m, SNOW, FLOE, "image_n", "image_n_plus_1", "version_tag_n", "version_tag_n_plus_1", "target_ref")
    swap(m, repo, SUNDOG, "target_ref")
    with pytest.raises(qa.GateError, match="^target_ref$"):
        qa.normalize(m)


def test_catalog_selects_the_sundog_entry(qa):
    catalog = floe_catalog()
    assert qa.snow_catalog(catalog, "sundog")
    sundog = next(e for e in catalog if e["name"] == "sundog")
    for damage in ("pop_key", "snow_ref", "missing", "duplicate"):
        broken = json.loads(json.dumps(catalog))
        entry = next(e for e in broken if e["name"] == "sundog")
        if damage == "pop_key":
            entry.pop("cosign_pub_key")
        elif damage == "snow_ref":
            entry["ref"] = f"{SNOW}:latest"
        elif damage == "missing":
            broken.remove(entry)
        else:
            broken.append(dict(entry))
        assert not qa.snow_catalog(broken, "sundog"), damage
    # Sundog does not depend on the other entries, and they not on Sundog.
    broken = [e for e in catalog if e is not sundog]
    assert qa.snow_catalog(broken, "snow") and qa.snow_catalog(broken, "floe")
    assert not qa.snow_catalog(broken, "sundog")


def test_policy_check_needs_the_sundog_repository_rule(qa):
    policy = shipped()
    assert qa.policy_ok(policy, SUNDOG)
    del policy["transports"]["docker"][SUNDOG]
    assert not qa.policy_ok(policy, SUNDOG)
    assert qa.policy_ok(policy, SNOW) and qa.policy_ok(policy, FLOE)
    weak = shipped()
    weak["transports"]["docker"][SUNDOG] = [{"type": "insecureAcceptAnything"}]
    assert not qa.policy_ok(weak, SUNDOG)


def test_committed_sundog_manifest_validates(qa):
    manifest = json.loads(RUN_RECORD.read_text())
    match = re.search(r'FIELDS = set\("([a-z0-9_ ]+)"\.split\(\)\)', SOURCE.read_text())
    assert match and set(manifest) == set(match.group(1).split())
    assert manifest["family"] == manifest["product"] == "sundog"
    assert manifest["target_ref"] == f"{SUNDOG}:qa-controlled"
    text = RUN_RECORD.read_text()
    assert SNOW + "@" not in text and SNOW + ":" not in text and FLOE not in text
    assert qa.normalize(dict(manifest)) == manifest


@pytest.mark.skipif(subprocess.run(["which", "jq"], capture_output=True).returncode != 0, reason="jq not installed")
def test_preflight_checks_select_the_sundog_catalog_entry(tmp_path):
    line = next(l for l in CHECKS_SH.read_text().splitlines() if "--arg p" in l)
    filt = re.search(r"jq -c --arg p \"\$\(j \.product\)\" '([^']+)'", line)
    assert filt
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(floe_catalog()))
    out = subprocess.run(["jq", "-c", "--arg", "p", "sundog", filt.group(1), str(catalog)],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"n": 1, "ref": f"{SUNDOG}:latest", "key": KEY}


def test_dev_submit_renders_the_sundog_manifest_unchanged(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(RUN_RECORD.read_text())
    environ = {"PATH": __import__("os").environ["PATH"], "PYTHON": sys.executable}
    result = subprocess.run([str(DEV_SUBMIT), "--repo", str(ROOT), "--print", str(manifest)],
                            capture_output=True, text=True, env=environ, timeout=120)
    assert result.returncode == 0, result.stderr
    parameters = {p["name"]: p["value"] for p in json.loads(result.stdout)["spec"]["arguments"]["parameters"]}
    assert json.loads(parameters["manifest-json"])["product"] == "sundog"
