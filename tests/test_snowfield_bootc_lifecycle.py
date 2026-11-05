"""Offline contract tests for the Snowfield product of the lifecycle lane.

Snowfield (the GNOME image for Microsoft Surface hardware) is one entry in
`PRODUCTS`; the manifest field `product` selects it. These tests cover the
Snowfield-specific manifest, repository, Firn catalog entry and policy rule.
"""
import hashlib
import json
import re
import subprocess
import sys

import pytest

from test_floe_bootc_lifecycle import (  # noqa: F401  (qa is a fixture)
    CHECKS_SH, DEV_SUBMIT, FLOE, KEY, N, NEXT, SNOW, SNOW_MANIFEST, ROOT, SOURCE,
    floe_catalog, qa, shipped, swap)

SNOWFIELD = "ghcr.io/frostyard/snowfield"
SUNDOG = "ghcr.io/frostyard/sundog"
RUN_RECORD = ROOT / "docs/plans/snowfield-bootc-lifecycle-run-2026-11-03/manifest.json"
RERUN_RECORD = ROOT / "docs/plans/snowfield-bootc-lifecycle-run-2026-11-04/manifest.json"


def snowfield_manifest():
    m = SNOW_MANIFEST()
    m.update(
        family="snowfield",
        product="snowfield",
        image_n=f"{SNOWFIELD}@sha256:b40995ef197bfccf34d8c205bf633fa56262bd145db3dad4581a7e2c4665dea4",
        version_n="20261026191603",
        version_tag_n=f"{SNOWFIELD}:20261026191603",
        image_n_plus_1=f"{SNOWFIELD}@sha256:88e8136f79bed1271913ffe2cc5fba363bd818bf7d764992a4785fbd555e8ab3",
        version_n_plus_1="20261103152032",
        version_tag_n_plus_1=f"{SNOWFIELD}:20261103152032",
        target_ref=f"{SNOWFIELD}:qa-controlled",
    )
    return m


def test_snowfield_manifest_normalizes_and_validates(qa, tmp_path, capsys):
    m = snowfield_manifest()
    assert qa.PRODUCTS["snowfield"] == SNOWFIELD
    assert qa.normalize(dict(m)) == m
    raw, out = tmp_path / "raw.json", tmp_path / "normalized.json"
    raw.write_text(json.dumps(m))
    assert qa.main(["validate", str(raw), str(out)]) == 0
    canonical = out.read_bytes()
    assert capsys.readouterr().out.splitlines()[-1] == hashlib.sha256(canonical).hexdigest()
    assert b'"product":"snowfield"' in canonical


@pytest.mark.parametrize("names", [
    ("image_n",), ("image_n_plus_1",), ("version_tag_n",),
    ("version_tag_n_plus_1",), ("target_ref",),
])
@pytest.mark.parametrize("other", [SNOW, FLOE, SUNDOG])
def test_snowfield_rejects_a_reference_in_another_product_repository(qa, names, other):
    m = snowfield_manifest()
    swap(m, SNOWFIELD, other, *names)
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


@pytest.mark.parametrize("change", [
    lambda m: m.update(family="floe", product="floe"),
    lambda m: m.update(family="snow", product="snow"),
    lambda m: m.update(family="sundog", product="sundog"),
    lambda m: m.update(family="snowfield-ab", product="snowfield-ab"),
    lambda m: m.update(product="Snowfield", family="Snowfield"),
    lambda m: m.update(target_ref=f"{SNOWFIELD}:latest"),
])
def test_snowfield_rejects_a_mixed_product_or_an_uncontrolled_target(qa, change):
    m = snowfield_manifest()
    change(m)
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


@pytest.mark.parametrize("product, repo", [
    ("floe", FLOE), ("snow", SNOW), ("sundog", SUNDOG),
])
def test_other_products_reject_snowfield_references(qa, product, repo):
    m = SNOW_MANIFEST()
    if product == "floe":
        m.update(family="floe", product="floe")
        swap(m, SNOW, FLOE, "image_n", "image_n_plus_1", "version_tag_n", "version_tag_n_plus_1", "target_ref")
    elif product == "sundog":
        m.update(family="sundog", product="sundog")
        swap(m, SNOW, SUNDOG, "image_n", "image_n_plus_1", "version_tag_n", "version_tag_n_plus_1", "target_ref")
    swap(m, repo, SNOWFIELD, "target_ref")
    with pytest.raises(qa.GateError, match="^target_ref$"):
        qa.normalize(m)


def test_catalog_selects_the_snowfield_entry(qa):
    assert qa.PRODUCTS["snowfield"] == SNOWFIELD
    catalog = floe_catalog()
    assert qa.snow_catalog(catalog, "snowfield")
    snowfield = next(e for e in catalog if e["name"] == "snowfield")
    for damage in ("pop_key", "snow_ref", "missing", "duplicate"):
        broken = json.loads(json.dumps(catalog))
        entry = next(e for e in broken if e["name"] == "snowfield")
        if damage == "pop_key":
            entry.pop("cosign_pub_key")
        elif damage == "snow_ref":
            entry["ref"] = f"{SNOW}:latest"
        elif damage == "missing":
            broken.remove(entry)
        else:
            broken.append(dict(entry))
        assert not qa.snow_catalog(broken, "snowfield"), damage
    broken = [e for e in catalog if e is not snowfield]
    assert qa.snow_catalog(broken, "snow") and qa.snow_catalog(broken, "floe")
    assert not qa.snow_catalog(broken, "snowfield")


def test_policy_check_needs_the_snowfield_repository_rule(qa):
    assert qa.PRODUCTS["snowfield"] == SNOWFIELD
    policy = shipped()
    assert qa.policy_ok(policy, SNOWFIELD)
    del policy["transports"]["docker"][SNOWFIELD]
    assert not qa.policy_ok(policy, SNOWFIELD)
    assert qa.policy_ok(policy, SNOW) and qa.policy_ok(policy, FLOE)
    weak = shipped()
    weak["transports"]["docker"][SNOWFIELD] = [{"type": "insecureAcceptAnything"}]
    assert not qa.policy_ok(weak, SNOWFIELD)


def test_committed_snowfield_manifest_validates(qa):
    manifest = json.loads(RUN_RECORD.read_text())
    match = re.search(r'FIELDS = set\("([a-z0-9_ ]+)"\.split\(\)\)', SOURCE.read_text())
    assert match and set(manifest) == set(match.group(1).split())
    assert manifest["family"] == manifest["product"] == "snowfield"
    assert manifest["target_ref"] == f"{SNOWFIELD}:qa-controlled"
    assert manifest["image_n"] == f"{SNOWFIELD}@sha256:b40995ef197bfccf34d8c205bf633fa56262bd145db3dad4581a7e2c4665dea4"
    assert manifest["image_n_plus_1"] == f"{SNOWFIELD}@sha256:88e8136f79bed1271913ffe2cc5fba363bd818bf7d764992a4785fbd555e8ab3"
    text = RUN_RECORD.read_text()
    assert SNOW + "@" not in text and FLOE not in text and SUNDOG not in text
    assert qa.normalize(dict(manifest)) == manifest


def test_committed_snowfield_rerun_manifest_validates(qa):
    manifest = json.loads(RERUN_RECORD.read_text())
    match = re.search(r'FIELDS = set\("([a-z0-9_ ]+)"\.split\(\)\)', SOURCE.read_text())
    assert match and set(manifest) == set(match.group(1).split())
    assert manifest["family"] == manifest["product"] == "snowfield"
    assert manifest["target_ref"] == f"{SNOWFIELD}:qa-controlled"
    assert manifest["image_n"] == f"{SNOWFIELD}@sha256:4c92f9aa345de1a82df3b5b9e8ab878dd67ac8c6013a2ba64cd5b800769a3db0"
    assert manifest["image_n_plus_1"] == f"{SNOWFIELD}@sha256:e7b09eb5a75cae19feee363f6ea36607ebd276a03ef19e9d1c24eb693c1fb4e4"
    assert manifest["version_n"] == "20261104172603"
    assert manifest["version_n_plus_1"] == "20261104190408"
    text = RERUN_RECORD.read_text()
    assert SNOW + "@" not in text and FLOE not in text and SUNDOG not in text
    assert qa.normalize(dict(manifest)) == manifest


@pytest.mark.skipif(subprocess.run(["which", "jq"], capture_output=True).returncode != 0, reason="jq not installed")
def test_preflight_checks_select_the_snowfield_catalog_entry(tmp_path):
    line = next(l for l in CHECKS_SH.read_text().splitlines() if "--arg p" in l)
    assert "snowfield" in CHECKS_SH.read_text()
    filt = re.search(r"jq -c --arg p \"\$\(j \.product\)\" '([^']+)'", line)
    assert filt
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(floe_catalog()))
    out = subprocess.run(["jq", "-c", "--arg", "p", "snowfield", filt.group(1), str(catalog)],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"n": 1, "ref": f"{SNOWFIELD}:latest", "key": KEY}


def test_dev_submit_renders_the_snowfield_manifest_unchanged(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(RUN_RECORD.read_text())
    environ = {"PATH": __import__("os").environ["PATH"], "PYTHON": sys.executable}
    result = subprocess.run([str(DEV_SUBMIT), "--repo", str(ROOT), "--print", str(manifest)],
                            capture_output=True, text=True, env=environ, timeout=120)
    assert result.returncode == 0, result.stderr
    parameters = {p["name"]: p["value"] for p in json.loads(result.stdout)["spec"]["arguments"]["parameters"]}
    assert json.loads(parameters["manifest-json"])["product"] == "snowfield"
    assert "Snowfield" in (ROOT / "argo/workflow-templates/run-snow-bootc-lifecycle.yaml").read_text()
