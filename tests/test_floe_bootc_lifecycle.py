"""Offline contract tests for the Floe product of the lifecycle lane.

Floe is a headless server image that runs next to Snow through the same lane.
The manifest field `product` selects it; there is no workflow input, so the
product is bound to the canonical, hashed manifest. These tests reuse the Snow
helpers and patch only their product-bound globals, so every Snow assertion
runs again with Floe values. The Snow test files stay unchanged.
"""
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

import test_snow_bootc_lifecycle as snow

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml"
TEMPLATE = ROOT / "argo/workflow-templates/run-snow-bootc-lifecycle.yaml"
CHECKS_SH = ROOT / "scripts/snow-lifecycle-preflight/checks.sh"
DEV_SUBMIT = ROOT / "scripts/snow-lifecycle-dev-submit.sh"
RUN_RECORD = ROOT / "docs/plans/floe-bootc-lifecycle-run-2026-10-28/manifest.json"
FLOE = "ghcr.io/frostyard/floe"
SNOW = "ghcr.io/frostyard/snow"
N, NEXT = snow.N, snow.NEXT
KEY = "/usr/lib/snosi/cosign.pub"

# shared/bootc-secure/tree/usr/lib/snosi/containers/bootc-policy.json of
# frostyard/snosi since ADR-0025, byte-equal to the policy that
# shared/bootc-secure/tree/etc/containers/policy.json held before it
# (origin/main, 2026-10-28): it names five repositories, Snow and Floe among them.
SHIPPED_POLICY_TEXT = """{
  "default": [
    {
      "type": "reject"
    }
  ],
  "transports": {
    "docker": {
      "ghcr.io/frostyard/cayo": [
        {
          "type": "sigstoreSigned",
          "keyPath": "/usr/lib/snosi/cosign.pub",
          "signedIdentity": {
            "type": "matchRepository"
          }
        }
      ],
      "ghcr.io/frostyard/floe": [
        {
          "type": "sigstoreSigned",
          "keyPath": "/usr/lib/snosi/cosign.pub",
          "signedIdentity": {
            "type": "matchRepository"
          }
        }
      ],
      "ghcr.io/frostyard/snow": [
        {
          "type": "sigstoreSigned",
          "keyPath": "/usr/lib/snosi/cosign.pub",
          "signedIdentity": {
            "type": "matchRepository"
          }
        }
      ],
      "ghcr.io/frostyard/snowfield": [
        {
          "type": "sigstoreSigned",
          "keyPath": "/usr/lib/snosi/cosign.pub",
          "signedIdentity": {
            "type": "matchRepository"
          }
        }
      ],
      "ghcr.io/frostyard/sundog": [
        {
          "type": "sigstoreSigned",
          "keyPath": "/usr/lib/snosi/cosign.pub",
          "signedIdentity": {
            "type": "matchRepository"
          }
        }
      ]
    },
    "containers-storage": {
      "": [
        {
          "type": "insecureAcceptAnything"
        }
      ]
    },
    "tarball": {
      "": [
        {
          "type": "insecureAcceptAnything"
        }
      ]
    },
    "docker-archive": {
      "": [
        {
          "type": "insecureAcceptAnything"
        }
      ]
    },
    "oci-archive": {
      "": [
        {
          "type": "insecureAcceptAnything"
        }
      ]
    },
    "dir": {
      "": [
        {
          "type": "insecureAcceptAnything"
        }
      ]
    },
    "oci": {
      "": [
        {
          "type": "insecureAcceptAnything"
        }
      ]
    }
  }
}
"""


SNOW_MANIFEST = snow.manifest   # the Snow builder, kept before floe_world patches the module


def floe_manifest():
    m = SNOW_MANIFEST()
    m.update(family="floe", product="floe", image_n=f"{FLOE}@sha256:{N}",
             version_tag_n=f"{FLOE}:{m['version_n']}", image_n_plus_1=f"{FLOE}@sha256:{NEXT}",
             version_tag_n_plus_1=f"{FLOE}:{m['version_n_plus_1']}", target_ref=f"{FLOE}:qa-controlled")
    return m


def floe_catalog():
    """The real snosi catalog shape: four bootc entries, each with a cosign key."""
    return [{"family": "bootc", "name": name, "description": name + " bootc image",
             "ref": f"ghcr.io/frostyard/{name}:latest", "cosign_pub_key": KEY}
            for name in ("snow", "snowfield", "floe", "sundog")]


@pytest.fixture
def qa(tmp_path):
    match = re.search(r"(?m)^  qa.py: \|\n((?:^    .*\n|^\n)*)", SOURCE.read_text())
    assert match, "ConfigMap qa.py payload missing"
    path = tmp_path / "qa.py"
    path.write_text(textwrap.dedent(match.group(1)))
    spec = importlib.util.spec_from_file_location("floe_qa", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def floe_world(monkeypatch):
    """Run the Snow helpers with Floe values (manifest, repository, staged file)."""
    monkeypatch.setattr(snow, "REPO", FLOE)
    monkeypatch.setattr(snow, "manifest", floe_manifest)
    monkeypatch.setattr(snow, "STAGED_FILE",
                        f"image={FLOE}:qa-controlled\ndigest=sha256:{NEXT}\nstaged_at=2026-09-24T00:00:00+00:00\n")


# 1. A Floe manifest normalizes and validates.

def test_floe_manifest_normalizes_and_validates(qa, tmp_path, capsys):
    m = floe_manifest()
    assert qa.normalize(dict(m)) == m
    raw, out = tmp_path / "raw.json", tmp_path / "normalized.json"
    raw.write_text(json.dumps(m))
    assert qa.main(["validate", str(raw), str(out)]) == 0
    canonical = out.read_bytes()
    assert canonical == qa.canonical(m)
    assert capsys.readouterr().out.splitlines()[-1] == hashlib.sha256(canonical).hexdigest()
    # The product is part of the hashed bytes: Floe and Snow never share evidence.
    assert b'"product":"floe"' in canonical
    assert hashlib.sha256(canonical).hexdigest() != hashlib.sha256(qa.canonical(SNOW_MANIFEST())).hexdigest()


def test_snow_manifest_still_normalizes_and_repo_constant_is_snow(qa):
    assert qa.REPO == SNOW
    assert qa.PRODUCTS == {"snow": SNOW, "floe": FLOE}
    assert qa.normalize(SNOW_MANIFEST()) == SNOW_MANIFEST()


# 2. Rejections.

def swap(m, old, new, *names):
    for name in names:
        m[name] = m[name].replace(old, new)


FLOE_SNOW_REF = [
    lambda m: swap(m, FLOE, SNOW, "image_n"),
    lambda m: swap(m, FLOE, SNOW, "image_n_plus_1"),
    lambda m: swap(m, FLOE, SNOW, "version_tag_n"),
    lambda m: swap(m, FLOE, SNOW, "version_tag_n_plus_1"),
    lambda m: swap(m, FLOE, SNOW, "target_ref"),
    lambda m: swap(m, FLOE, SNOW, "image_n", "image_n_plus_1", "version_tag_n", "version_tag_n_plus_1", "target_ref"),
]
SNOW_FLOE_REF = [
    lambda m: swap(m, SNOW, FLOE, "image_n"),
    lambda m: swap(m, SNOW, FLOE, "image_n_plus_1"),
    lambda m: swap(m, SNOW, FLOE, "version_tag_n"),
    lambda m: swap(m, SNOW, FLOE, "version_tag_n_plus_1"),
    lambda m: swap(m, SNOW, FLOE, "target_ref"),
]


@pytest.mark.parametrize("change", [
    lambda m: m.update(product="cayo", family="cayo"),
    lambda m: m.update(product="sundog", family="sundog"),
    lambda m: m.update(product="", family=""),
    lambda m: m.update(product=None),
    lambda m: m.update(product=["floe"], family=["floe"]),
    lambda m: m.update(product="Floe", family="Floe"),
    lambda m: m.update(family="snow"),
    lambda m: m.update(family="snow", product="floe"),
    lambda m: m.update(product="snow"),
    lambda m: m.update(family="floe-ab", product="floe-ab"),
    lambda m: m.update(family="floe-ab"),
    lambda m: m.update(family="snow-ab", product="snow-ab"),
    lambda m: m.update(target_ref=f"{FLOE}:latest"),
    lambda m: m.update(target_ref=f"{FLOE}@sha256:{NEXT}"),
    lambda m: m.update(target_ref=m["version_tag_n"]),
    lambda m: m.update(target_ref="ghcr.io/other/floe:controlled"),
    lambda m: m.update(image_n=f"{FLOE}:latest"),
    lambda m: m.update(version_tag_n=f"{FLOE}:other"),
    *FLOE_SNOW_REF,
])
def test_floe_schema_rejects_unsafe_inputs(qa, change):
    m = floe_manifest()
    change(m)
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


@pytest.mark.parametrize("change", [
    lambda m: m.update(product="snow-ab", family="snow-ab"),
    lambda m: m.update(family="floe"),
    lambda m: m.update(product="floe"),
    lambda m: m.update(target_ref=f"{SNOW}:latest"),
    *SNOW_FLOE_REF,
])
def test_snow_manifest_rejects_floe_references_and_mixed_product(qa, change):
    m = SNOW_MANIFEST()
    change(m)
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


def test_product_mismatch_uses_the_product_error_code(qa):
    m = floe_manifest()
    m["family"] = "snow"
    with pytest.raises(qa.GateError, match="^product$"):
        qa.normalize(m)
    m = floe_manifest()
    swap(m, FLOE, SNOW, "target_ref")
    with pytest.raises(qa.GateError, match="^target_ref$"):
        qa.normalize(m)


def test_floe_sysext_manifest_uses_the_floe_repository(qa):
    m = floe_manifest()
    m["sysext_probe"] = json.loads(json.dumps(snow.SYSEXT_PROBE))
    assert qa.normalize(dict(m)) == m
    swap(m, FLOE, SNOW, "target_ref")
    with pytest.raises(qa.GateError, match="^target_ref$"):
        qa.normalize(m)


# 3. The ISO catalog.

def test_catalog_with_real_shape_passes_for_floe_and_the_others(qa):
    catalog = floe_catalog()
    assert qa.snow_catalog(catalog, "floe")
    assert qa.snow_catalog(catalog, "snow")
    assert qa.snow_catalog(catalog)   # the Snow default stays
    catalog[2]["ref"] = f"{FLOE}@sha256:{N}"
    assert qa.snow_catalog(catalog, "floe")


@pytest.mark.parametrize("damage", ["missing", "duplicate", "snow_ref", "floe_ab_ref", "no_key", "wrong_key",
                                    "ab_only", "not_list", "ref_missing", "prefix_only"])
def test_catalog_rejects_a_floe_entry_that_is_not_unique_and_pinned(qa, damage):
    catalog = floe_catalog()
    floe = catalog[2]
    if damage == "missing":
        catalog.pop(2)
    elif damage == "duplicate":
        catalog.append(dict(floe))
    elif damage == "snow_ref":
        floe["ref"] = f"{SNOW}:latest"
    elif damage == "floe_ab_ref":
        floe["ref"] = "ghcr.io/frostyard/floe-ab:latest"
    elif damage == "no_key":
        floe.pop("cosign_pub_key")
    elif damage == "wrong_key":
        floe["cosign_pub_key"] = "/tmp/other-key"
    elif damage == "ab_only":
        catalog = [dict(family="ab", name="floe", ref=f"{FLOE}:latest", cosign_pub_key=KEY)]
    elif damage == "ref_missing":
        floe.pop("ref")
    elif damage == "prefix_only":
        floe["ref"] = "ghcr.io/frostyard/floefoo:latest"
    else:
        catalog = {"floe": floe}
    assert not qa.snow_catalog(catalog, "floe")


def test_catalog_selection_follows_the_product_not_the_other_entry(qa):
    catalog = floe_catalog()
    catalog[2].pop("cosign_pub_key")      # Floe broken, Snow intact
    assert not qa.snow_catalog(catalog, "floe")
    assert qa.snow_catalog(catalog, "snow")
    catalog = floe_catalog()
    catalog[0]["ref"] = f"{FLOE}:latest"  # Snow entry names the Floe image
    assert not qa.snow_catalog(catalog, "snow")
    assert qa.snow_catalog(catalog, "floe")
    assert not qa.snow_catalog(floe_catalog(), "cayo")
    assert not qa.snow_catalog(floe_catalog(), None)


# 4. The signature policy.

def shipped():
    return json.loads(SHIPPED_POLICY_TEXT)


def test_shipped_policy_names_snow_and_floe_and_passes_for_both(qa):
    policy = shipped()
    assert {FLOE, SNOW} <= set(policy["transports"]["docker"])
    assert qa.policy_ok(policy)
    assert qa.policy_ok(policy, SNOW)
    assert qa.policy_ok(policy, FLOE)


def test_policy_without_floe_fails_for_floe_and_still_passes_for_snow(qa):
    policy = shipped()
    del policy["transports"]["docker"][FLOE]
    assert not qa.policy_ok(policy, FLOE)
    assert qa.policy_ok(policy, SNOW)
    assert qa.policy_ok(policy)


@pytest.mark.parametrize("change", [
    lambda p: p["transports"]["docker"][FLOE][0].__setitem__("keyPath", "/etc/other.pub"),
    lambda p: p["transports"]["docker"][FLOE][0].pop("signedIdentity"),
    lambda p: p["transports"]["docker"].__setitem__(FLOE, [{"type": "insecureAcceptAnything"}]),
    lambda p: p["transports"]["docker"].__setitem__(FLOE, p["transports"]["docker"][FLOE] * 2),
    lambda p: p["transports"]["docker"].__setitem__("", [{"type": "insecureAcceptAnything"}]),
    lambda p: p["transports"]["docker"].__setitem__("docker.io/library/debian", p["transports"]["docker"][FLOE]),
    lambda p: p.__setitem__("default", [{"type": "insecureAcceptAnything"}]),
])
def test_policy_rejects_weakened_floe_policies(qa, change):
    policy = shipped()
    change(policy)
    assert not qa.policy_ok(policy, FLOE)


def test_weakened_snow_entry_does_not_fail_floe_but_fails_the_whole_policy(qa):
    # Every named repository must still be signed exactly: a bad Snow entry
    # fails the policy for both products.
    policy = shipped()
    policy["transports"]["docker"][SNOW] = [{"type": "insecureAcceptAnything"}]
    assert not qa.policy_ok(policy, FLOE)
    assert not qa.policy_ok(policy, SNOW)


def test_floe_guest_pulls_with_the_strict_update_policy_of_a_new_layout_image(
        qa, floe_world, tmp_path, monkeypatch):
    # snosi ADR-0025 ships the strict policy in /usr and a permissive system
    # policy in /etc; the Floe guest pulls with the strict one.
    strict = shipped()
    creds, calls = snow.policy_world(qa, tmp_path, monkeypatch, update=strict,
                                     system=snow.permissive_system_policy(strict))
    with pytest.raises(qa.GateError, match="^pull_digest$"):
        qa.guest(creds)
    assert ["podman", "pull", "--signature-policy", str(tmp_path / "root/bootc-policy.json"),
            f"{FLOE}:qa-controlled"] in calls


def test_floe_guest_rejects_a_system_policy_with_a_weakened_floe_scope(
        qa, floe_world, tmp_path, monkeypatch):
    strict = shipped()
    system = snow.permissive_system_policy(strict)
    system["transports"]["docker"][FLOE] = [{"type": "insecureAcceptAnything"}]
    creds, calls = snow.policy_world(qa, tmp_path, monkeypatch, update=strict, system=system)
    with pytest.raises(qa.GateError, match="^policy$"):
        qa.guest(creds)
    assert not any(command[:2] == ["podman", "pull"] for command in calls)


# 5. Preflight selects the Floe catalog entry.

def floe_skopeo(args, m, failure):
    assert args[-1].startswith("docker://" + FLOE), args
    ref = args[-1].removeprefix("docker://")
    digest = N if ref in (m["image_n"], m["version_tag_n"]) else NEXT
    labels = {"org.opencontainers.image.version": m["version_n"] if digest == N else m["version_n_plus_1"],
              "io.snosi.bootc.secureboot-assembly": "bootc-1.16.8-storage-digest-v1",
              "io.snosi.bootc.secureboot-capable": "true"}
    return subprocess.CompletedProcess(args, 0, json.dumps({"Digest": "sha256:" + digest, "Labels": labels}).encode(), b"")


def floe_inspector(m, catalog, calls=None):
    def inspector(args, **kwargs):
        if calls is not None:
            calls.append(args)
        if args[:2] == ["skopeo", "inspect"]:
            return floe_skopeo(args, m, None)
        return snow.fake_inspector(args, m, None, catalog_data=catalog, **kwargs)
    return inspector


def floe_preflight_manifest():
    m = floe_manifest()
    m.update(index_key_sha256=hashlib.sha256(b"index-key").hexdigest(),
             cosign_key_sha256=hashlib.sha256(b"cosign-key").hexdigest(),
             mok_cert_sha256=hashlib.sha256(b"mok-cert").hexdigest(),
             iso_sha256=hashlib.sha256(snow.ISO_BYTES).hexdigest())
    return m


def test_preflight_for_floe_uses_the_floe_catalog_entry(qa, tmp_path, monkeypatch):
    m = floe_preflight_manifest()
    catalog = floe_catalog()
    catalog[0].pop("cosign_pub_key")      # The Snow entry is unusable; only Floe can pass.
    catalog[0]["ref"] = "unrelated"
    calls = []
    monkeypatch.setattr(qa.subprocess, "run", floe_inspector(m, catalog, calls))
    checks = tmp_path / "checks.json"
    qa.preflight(m, checks)
    data = json.loads(checks.read_text())
    assert data["manifest_sha256"] == qa.sha(qa.canonical(m))
    assert data["digest_n"] == N and data["digest_n_plus_1"] == NEXT
    assert data["version_n"] == m["version_n"] and data["version_n_plus_1"] == m["version_n_plus_1"]
    qa.check_state(m, data)
    inspected = [args[-1] for args in calls if args[:2] == ["skopeo", "inspect"]]
    assert inspected and all(ref.startswith("docker://" + FLOE) for ref in inspected)
    assert f"docker://{m['target_ref']}" in inspected


@pytest.mark.parametrize("damage", ["floe_missing", "floe_no_key", "floe_snow_ref", "snow_only_valid"])
def test_preflight_for_floe_fails_without_a_valid_floe_catalog_entry(qa, tmp_path, monkeypatch, damage):
    m = floe_preflight_manifest()
    catalog = floe_catalog()
    if damage == "floe_missing":
        catalog.pop(2)
    elif damage == "floe_no_key":
        catalog[2].pop("cosign_pub_key")
    elif damage == "floe_snow_ref":
        catalog[2]["ref"] = f"{SNOW}:latest"
    else:
        catalog = [catalog[0]]
    monkeypatch.setattr(qa.subprocess, "run", floe_inspector(m, catalog))
    checks = tmp_path / "checks.json"
    with pytest.raises(qa.GateError, match="^iso_catalog$"):
        qa.preflight(m, checks)
    assert not checks.exists()


def test_preflight_for_snow_does_not_accept_a_floe_only_catalog(qa, tmp_path, monkeypatch):
    m = snow.preflight_manifest()
    catalog = [entry for entry in floe_catalog() if entry["name"] != "snow"]
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kw: snow.fake_inspector(args, m, None, catalog_data=catalog, **kw))
    with pytest.raises(qa.GateError, match="^iso_catalog$"):
        qa.preflight(m, tmp_path / "checks.json")


def test_inspect_iso_passes_the_manifest_product_to_the_catalog_check(qa, tmp_path, monkeypatch):
    image = tmp_path / "installer.iso"
    image.write_bytes(snow.ISO_BYTES)
    m = floe_manifest()
    seen = []
    monkeypatch.setattr(qa, "snow_catalog", lambda catalog, product="snow": seen.append(product) or True)
    monkeypatch.setattr(qa.subprocess, "run", floe_inspector(m, floe_catalog()))
    qa.inspect_iso(image, tmp_path, m, b"index-key", b"cosign-key", b"mok-cert")
    assert seen == ["floe"]


# 6. The verdict and the phase attestation with Floe records.

@pytest.mark.parametrize("phase", snow.PHASES)
def test_floe_record_is_attested_in_every_phase(floe_world, qa, tmp_path, capsys, phase):
    record = snow.phase_record(phase)
    assert record["spec_image"] == f"{FLOE}:qa-controlled"
    snow.test_phase_accepts_one_attested_fresh_boot(qa, tmp_path, capsys, phase)


@pytest.mark.parametrize("phase", snow.PHASES)
def test_floe_verdict_binds_digests_versions_and_target_ref(floe_world, qa, phase):
    m = floe_manifest()
    record = snow.phase_record(phase)
    record["boot_id"] = "12345678-1234-4234-8234-123456789abc"
    # Snow-world digests are the Floe digests: N and N+1 come from the manifest.
    assert qa.verdict(m, phase, snow.NONCE, "none", dict(record)) == record["boot_id"]
    for field, value, code in (("spec_image", f"{SNOW}:qa-controlled", "target_spec"),
                               ("spec_image", f"{FLOE}:latest", "target_spec"),
                               ("spec_image", f"{FLOE}@sha256:{N}", "target_spec"),
                               ("booted_digest", "c" * 64, "booted_image"),
                               ("booted_version", "20260101000000", "booted_image")):
        damaged = dict(record, **{field: value})
        with pytest.raises(qa.GateError, match="^" + code + "$"):
            qa.verdict(m, phase, snow.NONCE, "none", damaged)


def test_a_snow_record_does_not_attest_a_floe_manifest(qa):
    record = snow.phase_record("stage")
    record["boot_id"] = "12345678-1234-4234-8234-123456789abc"
    assert qa.verdict(SNOW_MANIFEST(), "stage", snow.NONCE, "none", dict(record)) == record["boot_id"]
    with pytest.raises(qa.GateError, match="^target_spec$"):
        qa.verdict(floe_manifest(), "stage", snow.NONCE, "none", dict(record))


def test_floe_five_phase_chain_rejects_reused_boot_identity(floe_world, qa, tmp_path):
    snow.test_five_phase_chain_rejects_reused_boot_identity(qa, tmp_path)


@pytest.mark.parametrize("phase", ["stage", "rollback"])
def test_floe_guest_probe_emits_a_floe_record_and_checks_the_floe_policy(floe_world, qa, tmp_path, monkeypatch, phase):
    # The guest runs the same probe: the policy check takes the Floe repository.
    snow.test_guest_stage_probes_and_emits_allowlisted_single_record(
        qa, tmp_path, monkeypatch, phase, snow.CLEAN_JOURNAL, 0)


def test_guest_refuses_a_policy_that_does_not_sign_floe(floe_world, qa, tmp_path, monkeypatch):
    policy = shipped()
    del policy["transports"]["docker"][FLOE]
    monkeypatch.setattr(snow, "SHIPPED_POLICY", policy)
    with pytest.raises(qa.GateError, match="^policy$"):
        snow.test_guest_stage_probes_and_emits_allowlisted_single_record(
            qa, tmp_path, monkeypatch, "stage", snow.CLEAN_JOURNAL, 0)


# 7. One full mocked run.sh pass.

def test_run_script_passes_five_fresh_boots_with_a_floe_manifest(floe_world, qa, tmp_path, monkeypatch):
    # The Snow runner simulation mocks qa.py, incus and the network tools; the
    # manifest it feeds to run.sh is the Floe one. The real qa.py accepts that
    # exact manifest, so the mocked pass is not a pass on an invalid manifest.
    submitted = []
    real_run = subprocess.run

    def recording_run(args, *a, **kw):
        if isinstance(args, list) and args[:1] == ["bash"] and "MANIFEST_JSON" in (kw.get("env") or {}):
            submitted.append(json.loads(kw["env"]["MANIFEST_JSON"]))
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", recording_run)
    snow.test_runner_five_boots_and_post_init_failures_are_offline(tmp_path, "pass")
    assert len(submitted) == 1
    m = submitted[0]
    assert m["family"] == m["product"] == "floe"
    assert all(m[name].startswith(FLOE + ":") or m[name].startswith(FLOE + "@")
               for name in ("image_n", "image_n_plus_1", "version_tag_n", "version_tag_n_plus_1", "target_ref"))
    assert qa.normalize(dict(m)) == m
    assert (tmp_path / "results/result-summary.txt").read_text() == "PASS: verified_five_fresh_boots\n"


def test_run_script_has_no_product_input_and_template_keeps_its_inputs():
    template = yaml.safe_load(TEMPLATE.read_text())
    assert "arguments" not in template["spec"]
    assert "Floe" in template["metadata"]["annotations"]["description"]
    for entry in template["spec"]["templates"]:
        names = [item["name"] for item in entry.get("inputs", {}).get("parameters", [])]
        assert "product" not in names
        if entry["name"] == template["spec"]["entrypoint"]:
            assert names == ["manifest-json", "keep-vm-on-failure"]


# 8. The guest is headless-safe.

DESKTOP = re.compile(r"gdm|gnome|graphical\.target|flatpak|first-setup|display-manager", re.I)


def function_source(text, name):
    match = re.search(r"(?ms)^def " + name + r"\(.*?(?=^\S)", text)
    assert match, name
    return match.group()


def test_guest_probe_and_unit_have_no_desktop_dependency():
    data = yaml.safe_load(SOURCE.read_text())["data"]
    guest = function_source(data["qa.py"], "guest")
    assert "def guest(" in guest and "policy_ok(" in guest
    assert not DESKTOP.search(guest), DESKTOP.search(guest)
    run = data["run.sh"]
    unit = re.search(r'(?ms)^  guestunit="\[Unit\].*?^  gb=', run)
    assert unit
    assert not DESKTOP.search(unit.group()), DESKTOP.search(unit.group())
    assert "multi-user.target" in run   # booted to multi-user: no graphical stack is needed
    assert not DESKTOP.search(data["guest.sh"])
    assert "headless" in guest


def test_guest_console_comment_covers_headless_ttys0():
    text = yaml.safe_load(SOURCE.read_text())["data"]["qa.py"]
    comment = text[text.index("# On Snow, /dev/console"):text.index("GUEST_CONSOLES =")]
    assert "Floe" in comment and "ttyS0" in comment and "identical copies" in comment


def test_the_same_line_twice_on_ttys0_is_one_record(floe_world, qa, tmp_path, capsys):
    # On a headless guest /dev/console can be ttyS0: the record appears twice.
    normalized, checks = snow.phase_files(tmp_path, qa)
    line = snow.serial_record(snow.phase_record("stage"))
    serial = tmp_path / "serial"
    serial.write_bytes((line + line).encode())
    assert qa.main(["phase", str(normalized), "stage", snow.NONCE, "none", str(serial), str(checks)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "12345678-1234-4234-8234-123456789abc"


# 9. The preflight tool and the run record.

def test_checks_select_the_catalog_entry_by_the_manifest_product():
    text = CHECKS_SH.read_text()
    assert '--arg p "$(j .product)"' in text
    assert '.name == $p' in text
    assert '.name == "snow"' not in text
    assert "same iso_catalog" in text
    subprocess.run(["bash", "-n", str(CHECKS_SH)], check=True)


@pytest.mark.skipif(subprocess.run(["which", "jq"], capture_output=True).returncode != 0, reason="jq not installed")
@pytest.mark.parametrize("product", ["floe", "snow", "sundog"])
def test_checks_jq_filter_selects_the_product_entry(tmp_path, product):
    line = next(l for l in CHECKS_SH.read_text().splitlines() if "--arg p" in l)
    filt = re.search(r"jq -c --arg p \"\$\(j \.product\)\" '([^']+)'", line)
    assert filt
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(floe_catalog()))
    out = subprocess.run(["jq", "-c", "--arg", "p", product, filt.group(1), str(catalog)],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"n": 1, "ref": f"ghcr.io/frostyard/{product}:latest", "key": KEY}


def iso_catalog_predicate():
    text = CHECKS_SH.read_text()
    match = re.search(r"same iso_catalog \"\$\(jq -r --arg r \"ghcr\.io/frostyard/\$\(j \.product\)\" \\\n\s*'([^']+)'", text)
    assert match, "iso_catalog predicate"
    return match.group(1)


@pytest.mark.skipif(subprocess.run(["which", "jq"], capture_output=True).returncode != 0, reason="jq not installed")
@pytest.mark.parametrize("product, ref, ok", [
    ("floe", "ghcr.io/frostyard/floe:latest", True),
    ("floe", "ghcr.io/frostyard/floe@sha256:" + "a" * 64, True),
    ("snow", "ghcr.io/frostyard/snow:latest", True),
    ("floe", "ghcr.io/frostyard/snow:latest", False),
    ("snow", "ghcr.io/frostyard/floe:latest", False),
    ("floe", "ghcr.io/frostyard/floe-ab:latest", False),
    ("floe", "ghcr.io/frostyard/floex:latest", False),
    ("floe", "ghcr.io/frostyard/floe", False),
    ("floe", None, False),
    ("floe", 7, False),
])
def test_checks_iso_catalog_requires_a_ref_in_the_product_repository(product, ref, ok):
    entry = {"n": 1, "ref": ref, "key": KEY}
    out = subprocess.run(["jq", "-r", "--arg", "r", "ghcr.io/frostyard/" + product, iso_catalog_predicate()],
                         input=json.dumps(entry), capture_output=True, text=True, check=True).stdout.strip()
    assert out == ("true" if ok else "false")
    for damaged in ({**entry, "n": 0}, {**entry, "n": 2}, {**entry, "key": "/etc/cosign.pub"}):
        out = subprocess.run(["jq", "-r", "--arg", "r", "ghcr.io/frostyard/" + product, iso_catalog_predicate()],
                             input=json.dumps(damaged), capture_output=True, text=True, check=True).stdout.strip()
        assert out == "false"


def test_run_record_manifest_is_a_valid_floe_manifest(qa):
    manifest = json.loads(RUN_RECORD.read_text())
    match = re.search(r'FIELDS = set\("([a-z0-9_ ]+)"\.split\(\)\)', SOURCE.read_text())
    assert match and set(manifest) == set(match.group(1).split())
    assert manifest["family"] == manifest["product"] == "floe"
    assert manifest["target_ref"] == f"{FLOE}:qa-controlled"
    assert "latest" not in manifest["iso_url"]
    assert SNOW not in RUN_RECORD.read_text()
    assert qa.normalize(dict(manifest)) == manifest


# 10. The dev submit renders a Floe manifest without change.

def test_dev_submit_renders_a_floe_manifest_unchanged(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(RUN_RECORD.read_text())
    environ = {"PATH": __import__("os").environ["PATH"], "PYTHON": sys.executable}
    result = subprocess.run([str(DEV_SUBMIT), "--repo", str(ROOT), "--print", str(manifest)],
                            capture_output=True, text=True, env=environ, timeout=120)
    assert result.returncode == 0, result.stderr
    workflow = json.loads(result.stdout)
    parameters = {p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]}
    assert set(parameters) == {"manifest-json", "keep-vm-on-failure"}
    assert parameters["manifest-json"] == RUN_RECORD.read_text()
    assert json.loads(parameters["manifest-json"])["product"] == "floe"
    assert parameters["keep-vm-on-failure"] == "false"
