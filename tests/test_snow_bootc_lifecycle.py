"""Offline contract tests for the manual Snow lifecycle trust gate."""
import hashlib
import base64
import importlib.util
import json
import re
import subprocess
import struct
import sys
import textwrap
import os
import shlex
import io
import shutil
import zlib
from pathlib import Path

import pytest
import yaml


SOURCE = Path(__file__).resolve().parents[1] / "argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml"
N = "a" * 64
NEXT = "b" * 64
ISO = "c" * 64
COMMIT = "39cf19887547f83200866e94e5753888706b49fa"
REPO = "ghcr.io/frostyard/snow"


def iso_bytes():
    """Synthetic 512-byte-sector GPT with ESP partition 2 at LBA 8."""
    raw = bytearray(16 * 512)
    entries = bytearray(2 * 128)
    entries[128:144] = bytes.fromhex("28732ac11ff8d211ba4b00a0c93ec93b")
    struct.pack_into("<QQ", entries, 128 + 32, 8, 15)
    raw[1024:1280] = entries
    header = bytearray(92)
    header[:8] = b"EFI PART"
    struct.pack_into("<I", header, 8, 0x10000)
    struct.pack_into("<I", header, 12, 92)
    struct.pack_into("<Q", header, 72, 2)
    struct.pack_into("<II", header, 80, 2, 128)
    struct.pack_into("<I", header, 88, zlib.crc32(entries))
    struct.pack_into("<I", header, 16, zlib.crc32(header))
    raw[512:604] = header
    return bytes(raw)


ISO_BYTES = iso_bytes()


def iso_bytes_with_entry_count(count):
    """GPT with a valid ESP in entry 2 and enough ISO space for 1025 entries."""
    raw = bytearray(302 * 512)
    entries = bytearray(count * 128)
    entries[128:144] = bytes.fromhex("28732ac11ff8d211ba4b00a0c93ec93b")
    struct.pack_into("<QQ", entries, 128 + 32, 204, 300)
    raw[1024:1024 + len(entries)] = entries
    header = bytearray(92)
    header[:8] = b"EFI PART"
    struct.pack_into("<I", header, 8, 0x10000)
    struct.pack_into("<I", header, 12, 92)
    struct.pack_into("<Q", header, 72, 2)
    struct.pack_into("<II", header, 80, count, 128)
    struct.pack_into("<I", header, 88, zlib.crc32(entries))
    struct.pack_into("<I", header, 16, zlib.crc32(header))
    raw[512:604] = header
    return raw


def manifest():
    return dict(family="snow", product="snow", iso_url="https://repository.frostyard.org/isos/native/v1/snosi-installer_20260924000000_x86-64.iso",
                iso_sha256=ISO, iso_version="20260924000000", image_n=f"{REPO}@sha256:{N}", version_n="20260923000000",
                version_tag_n=f"{REPO}:20260923000000", image_n_plus_1=f"{REPO}@sha256:{NEXT}",
                version_n_plus_1="20260924000000", version_tag_n_plus_1=f"{REPO}:20260924000000",
                target_ref=f"{REPO}:qa-controlled", snosi_commit=COMMIT, index_key_sha256="1" * 64,
                cosign_key_sha256="2" * 64, mok_cert_sha256="3" * 64,
                trust_fingerprint="F37282A35CB6BDFEBFC8FE775A2EAC5C8216FD68", secureboot=True,
                encryption="tpm2-luks-passphrase", timeouts=dict(iso_boot=600, install=2400, installed_boot=900, stage=1200, reboot=900))


@pytest.fixture
def qa(tmp_path):
    # Run the actual ConfigMap payload, not a separately maintained Python copy.
    yaml = SOURCE.read_text()
    match = re.search(r"(?m)^  qa.py: \|\n((?:^    .*\n|^\n)*)", yaml)
    assert match, "ConfigMap qa.py payload missing"
    path = tmp_path / "qa.py"
    path.write_text(textwrap.dedent(match.group(1)))
    spec = importlib.util.spec_from_file_location("snow_qa", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("change", [
    lambda m: m.pop("family"), lambda m: m.update(extra="x"), lambda m: m.update(family="snow-ab"),
    lambda m: m.update(product="floe"), lambda m: m.update(image_n=f"{REPO}:latest"),
    lambda m: m.update(image_n=f"{REPO}@sha256:{N.upper()}"),
    lambda m: m.update(image_n_plus_1=m["image_n"]),
    lambda m: m.update(target_ref=f"{REPO}@sha256:{NEXT}"),
    lambda m: m.update(target_ref=f"{REPO}:latest"),
    lambda m: m.update(target_ref="ghcr.io/other/snow:controlled"),
    lambda m: m.update(target_ref=m["version_tag_n"]),
    lambda m: m.update(secureboot=False), lambda m: m.update(encryption="none"),
    lambda m: m.update(iso_url=m["iso_url"].replace("20260924000000", "latest")),
    lambda m: m.update(iso_url=m["iso_url"].replace("x86-64", "x86_64")),
    lambda m: m.update(iso_sha256="z" * 64),
    lambda m: m.update(timeouts={"install": 100}),
    lambda m: m["timeouts"].update(install=0),
    lambda m: m["timeouts"].update(install=999999),
    lambda m: m["timeouts"].update(install=True),
    lambda m: m.update(version_n="2026092300000x"),
    lambda m: m.update(version_tag_n=f"{REPO}:other"),
    lambda m: m.update(version_n_plus_1=m["version_n"]),
    lambda m: m.update(version_n="20260925000000", version_tag_n=f"{REPO}:20260925000000"),
    lambda m: m.update(snosi_commit="main"),
    lambda m: m.update(snosi_commit="d" * 40),
    lambda m: m.update(trust_fingerprint="0" * 40),
])
def test_schema_rejects_unsafe_inputs_without_leaking(qa, change):
    m = manifest()
    change(m)
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


@pytest.mark.parametrize("key", ["iso_boot", "install", "installed_boot", "stage", "reboot"])
def test_schema_rejects_short_phase_budget(qa, key):
    m = manifest()
    m["timeouts"][key] = 299
    with pytest.raises(qa.GateError, match="^timeout_value$"):
        qa.normalize(m)
    m["timeouts"][key] = 300
    assert qa.normalize(m) == m


def test_duplicate_key_and_noncanonical_normalized_rejected(qa, tmp_path, capsys):
    raw = tmp_path / "raw.json"
    out = tmp_path / "normalized.json"
    raw.write_text('{"family":"snow","family":"snow"}')
    assert qa.main(["validate", str(raw), str(out)]) == 1
    assert not out.exists()
    raw.write_text(json.dumps(manifest()))
    assert qa.main(["validate", str(raw), str(out)]) == 0
    canonical = out.read_bytes()
    assert capsys.readouterr().out.splitlines()[-1] == hashlib.sha256(canonical).hexdigest()
    out.write_text(json.dumps(manifest(), indent=2))
    assert qa.main(["preflight", str(out), str(tmp_path / "checks.json")]) == 1


REAL_SHAPE_CATALOG = [
    {"family": "bootc", "name": "snow", "description": "Snow bootc image",
     "ref": "ghcr.io/frostyard/snow:latest", "cosign_pub_key": "/usr/lib/snosi/cosign.pub",
     "default_groups": ["wheel"]},
    {"family": "bootc", "name": "snowfield", "ref": "ghcr.io/frostyard/snowfield:latest"},
    {"family": "bootc", "name": "floe", "ref": "ghcr.io/frostyard/floe:latest"},
    {"family": "bootc", "name": "sundog", "ref": "ghcr.io/frostyard/sundog:latest"},
    {"family": "ab", "product": "snow-ab", "name": "snow", "ref": "ghcr.io/frostyard/snow-ab:latest",
     "cosign_pub_key": "/usr/lib/snosi/cosign.pub"},
    {"family": "ab", "product": "floe-ab", "name": "floe"},
]


EMBEDDED_FILES = {
    "usr/bin/firn": b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00" + b"binary",
    "etc/firn/catalog.json": json.dumps(REAL_SHAPE_CATALOG).encode(),
    "usr/lib/snosi/cosign.pub": b"cosign-key",
    "usr/lib/snosi/os-update-pubring.gpg": b"index-key",
    "usr/lib/snosi/mok.crt": b"mok-cert",
    "usr/bin/cosign": b"cosign-binary",
    "etc/snosi-installer-release": b"SNOSI_VERSION=20260924000000\n",
}


def fake_inspector(args, m, failure, member_prefix="./", duplicate_members=False, catalog_data=None,
                   assembly_n="bootc-1.16.8-storage-digest-v1",
                   assembly_n_plus_1="bootc-1.16.8-storage-digest-v1", **kwargs):
    """Only externally observable inspector responses; fail on unexpected commands."""
    assert isinstance(args, list)
    if args[0] == "curl":
        url = args[-1]
        assert url in (
            m["iso_url"],
            "https://repository.frostyard.org/isos/native/v1/SHA256SUMS",
            "https://repository.frostyard.org/isos/native/v1/SHA256SUMS.gpg",
            *[f"https://raw.githubusercontent.com/frostyard/snosi/{m['snosi_commit']}/{suffix}"
              for suffix in ("shared/native-ab/keys/import-pubring.gpg", "cosign.pub",
                             "shared/native-ab/keys/mok-2026.crt")],
        )
        if url.endswith("SHA256SUMS"):
            name = m["iso_url"].rsplit("/", 1)[-1]
            sha = "0" * 64 if failure == "index" else m["iso_sha256"]
            data = f"{sha}  {name}\n".encode()
            if failure == "duplicate_index":
                data += f"{'0' * 64}  {name}\n".encode()
        elif url.endswith("SHA256SUMS.gpg"):
            data = b"signature"
        elif url == m["iso_url"]:
            data = ISO_BYTES
        elif url.endswith("/shared/native-ab/keys/import-pubring.gpg"):
            assert m["snosi_commit"] in url
            data = b"index-key"
        elif url.endswith("/cosign.pub"):
            assert m["snosi_commit"] in url
            data = b"cosign-key"
        elif url.endswith("/shared/native-ab/keys/mok-2026.crt"):
            assert m["snosi_commit"] in url
            data = b"mok-cert"
        else:
            pytest.fail(f"unexpected URL {url}")
        if failure == "trust_material_missing" and url.endswith("/cosign.pub"):
            return subprocess.CompletedProcess(args, 22, b"", b"404")
        if "-o" in args:
            if failure == "iso":
                data = b"wrong-bytes"
            Path(args[args.index("-o") + 1]).write_bytes(data)
            data = b""
        return subprocess.CompletedProcess(args, 0, data, b"")
    if args[:2] == ["gpg", "--show-keys"]:
        fp = "0" * 40 if failure == "fingerprint" else m["trust_fingerprint"]
        # A primary, its subkey and an unrelated rotated primary are all legal in the pubring.
        records = f"pub:::::::::\nfpr:::::::::{fp}:\nsub:::::::::\nfpr:::::::::{'a' * 40}:\npub:::::::::\nfpr:::::::::{'b' * 40}:\n"
        return subprocess.CompletedProcess(args, 0, records.encode(), b"")
    if args[0] == "gpgv":
        signer = "0" * 40 if failure == "wrong_key" else m["trust_fingerprint"]
        if failure == "subkey_signer":
            return subprocess.CompletedProcess(args, 0, b"", f"[GNUPG:] VALIDSIG {'a' * 40} 2026 0 0 0 0 0 0 {signer}\n".encode())
        return subprocess.CompletedProcess(args, 0, b"", f"[GNUPG:] VALIDSIG {signer} 2026 0 0 0 0 0 0\n".encode())
    if args[0] == "mcopy":
        assert args[1] == "-i" and args[2].endswith("@@4096")
        assert args[3] == "::firn-installer/initrd.img"
        assert kwargs.get("preexec_fn") is not None  # Bound output while copying, not after.
        Path(args[4]).write_bytes(b"mock-zstd-cpio")
        return subprocess.CompletedProcess(args, 0, b"", b"")
    if args[0] == "zstd":
        assert args[1:3] == ["-dc", "--"] and args[-1].endswith("initrd.img")
        kwargs["stdout"].write(b"mock-cpio")
        return subprocess.CompletedProcess(args, 0, b"", b"")
    if args[0] == "cpio":
        entries = {member_prefix + name: data for name, data in EMBEDDED_FILES.items()}
        if args[1:] == ["-it"]:
            names = list(entries)
            if duplicate_members:
                names.append(("" if member_prefix else "./") + "usr/bin/firn")
            kwargs["stdout"].write(("\n".join(names) + "\n").encode())
            return subprocess.CompletedProcess(args, 0, b"", b"")
        assert args[1:3] == ["-i", "--to-stdout"]
        entry = args[-1]
        assert entry in entries
        data = entries[entry]
        if catalog_data is not None and entry.endswith("etc/firn/catalog.json"):
            data = json.dumps(catalog_data).encode()
        if failure == "embedded_key" and entry.endswith("usr/lib/snosi/cosign.pub"):
            data = b"different-key"
        if failure == "embedded_mok" and entry.endswith("usr/lib/snosi/mok.crt"):
            data = b"different-cert"
        if failure == "embedded_catalog" and entry.endswith("etc/firn/catalog.json"):
            data = json.dumps([{**REAL_SHAPE_CATALOG[0], "cosign_pub_key": "/tmp/other-key"}]).encode()
        if failure == "catalog_duplicate" and entry.endswith("etc/firn/catalog.json"):
            data = b'[{"family":"bootc","name":"snow","cosign_pub_key":"/usr/lib/snosi/cosign.pub","cosign_pub_key":"/usr/lib/snosi/cosign.pub"}]'
        if failure == "catalog_nonfinite" and entry.endswith("etc/firn/catalog.json"):
            data = b'[{"family":"bootc","name":"snow","cosign_pub_key":"/usr/lib/snosi/cosign.pub","value":NaN}]'
        if failure == "firn_missing" and entry.endswith("usr/bin/firn"):
            return subprocess.CompletedProcess(args, 1, b"", b"missing")
        kwargs["stdout"].write(data)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    if args[:2] == ["cosign", "verify"]:
        assert len(args) == 5 and args[2] == "--key" and args[-1] in (m["image_n"], m["image_n_plus_1"])
        return subprocess.CompletedProcess(args, 1 if failure == "cosign" else 0, b"[]", b"")
    if args[:2] == ["skopeo", "inspect"]:
        assert args[-1].startswith("docker://" + REPO)
        ref = args[-1].removeprefix("docker://")
        digest = N if ref in (m["image_n"], m["version_tag_n"]) else NEXT
        if ref == m["target_ref"] and failure == "target":
            digest = N
        if ref == m["version_tag_n"] and failure == "version_tag":
            digest = NEXT
        labels = {"org.opencontainers.image.version": m["version_n"] if digest == N else m["version_n_plus_1"],
                  "io.snosi.bootc.secureboot-assembly": assembly_n if digest == N else assembly_n_plus_1,
                  "io.snosi.bootc.secureboot-capable": "true"}
        if failure == "labels":
            labels["org.opencontainers.image.version"] = "bad"
        if failure == "assembly":
            labels["io.snosi.bootc.secureboot-assembly"] = "bad"
        if failure == "capability":
            labels["io.snosi.bootc.secureboot-capable"] = "false"
        return subprocess.CompletedProcess(args, 0, json.dumps({"Digest": "sha256:" + digest, "Labels": labels}).encode(), b"")
    pytest.fail(f"unexpected command {args[0]}")


@pytest.mark.parametrize("ref", ["ghcr.io/frostyard/snow:latest", "ghcr.io/frostyard/snow@sha256:" + N])
def test_iso_accepts_real_shape_snow_bootc_catalog(qa, tmp_path, monkeypatch, ref):
    image = tmp_path / "installer.iso"
    image.write_bytes(ISO_BYTES)
    catalog = [dict(item) for item in REAL_SHAPE_CATALOG]
    catalog[0]["ref"] = ref
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kw: fake_inspector(args, manifest(), None, catalog_data=catalog, **kw))
    assert qa.inspect_iso(image, tmp_path, manifest(), b"index-key", b"cosign-key", b"mok-cert") == hashlib.sha256(EMBEDDED_FILES["usr/bin/firn"]).hexdigest()


@pytest.mark.parametrize("damage", ["wrong_key", "missing", "duplicate", "ab_only", "wrong_ref", "not_list"])
def test_iso_rejects_catalog_without_unique_pinned_snow_bootc(qa, tmp_path, monkeypatch, damage):
    image = tmp_path / "installer.iso"
    image.write_bytes(ISO_BYTES)
    catalog = [dict(item) for item in REAL_SHAPE_CATALOG]
    if damage == "wrong_key":
        catalog[0]["cosign_pub_key"] = "/tmp/other-key"
    elif damage == "missing":
        catalog.pop(0)
    elif damage == "duplicate":
        catalog.append(dict(catalog[0]))
    elif damage == "ab_only":
        catalog = [item for item in catalog if item["family"] == "ab"]
    elif damage == "wrong_ref":
        catalog[0]["ref"] = "ghcr.io/frostyard/snow-ab:latest"
    else:
        catalog = {"snow": catalog[0]}
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kw: fake_inspector(args, manifest(), None, catalog_data=catalog, **kw))
    with pytest.raises(qa.GateError, match="^iso_catalog$"):
        qa.inspect_iso(image, tmp_path, manifest(), b"index-key", b"cosign-key", b"mok-cert")


@pytest.mark.parametrize("prefix", ["", "./"])
def test_iso_extracts_exact_listed_member(qa, tmp_path, monkeypatch, prefix):
    image = tmp_path / "installer.iso"
    image.write_bytes(ISO_BYTES)
    m = manifest()
    calls = []

    def inspector(args, **kwargs):
        calls.append(args)
        return fake_inspector(args, m, None, member_prefix=prefix, **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    assert qa.inspect_iso(image, tmp_path, m, b"index-key", b"cosign-key", b"mok-cert") == hashlib.sha256(EMBEDDED_FILES["usr/bin/firn"]).hexdigest()
    assert sum(args == ["cpio", "-it"] for args in calls) == 1
    assert [args[-1] for args in calls if args[:3] == ["cpio", "-i", "--to-stdout"]] == [
        prefix + name for name in EMBEDDED_FILES]


def test_iso_rejects_both_member_spellings(qa, tmp_path, monkeypatch):
    image = tmp_path / "installer.iso"
    image.write_bytes(ISO_BYTES)
    m = manifest()
    calls = []

    def inspector(args, **kwargs):
        calls.append(args)
        return fake_inspector(args, m, None, duplicate_members=True, **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    with pytest.raises(qa.GateError, match="^iso_embedded$"):
        qa.inspect_iso(image, tmp_path, m, b"index-key", b"cosign-key", b"mok-cert")
    assert not any(args[:3] == ["cpio", "-i", "--to-stdout"] for args in calls)


@pytest.mark.skipif(shutil.which("cpio") is None, reason="cpio not installed")
def test_iso_extracts_real_newc_without_dot_prefix(qa, tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name, data in EMBEDDED_FILES.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    real_run = subprocess.run
    archive = real_run(["cpio", "-o", "-H", "newc"], input=("\n".join(EMBEDDED_FILES) + "\n").encode(),
                       cwd=source, capture_output=True, check=True).stdout
    image = tmp_path / "installer.iso"
    image.write_bytes(ISO_BYTES)
    m = manifest()

    def inspector(args, **kwargs):
        if args[:2] == ["zstd", "-dc"]:
            kwargs["stdout"].write(archive)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[0] == "cpio":
            return real_run(args, **kwargs)
        return fake_inspector(args, m, None, **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    assert qa.inspect_iso(image, tmp_path, m, b"index-key", b"cosign-key", b"mok-cert") == hashlib.sha256(EMBEDDED_FILES["usr/bin/firn"]).hexdigest()


@pytest.mark.parametrize("failure", ["index", "duplicate_index", "iso", "fingerprint", "wrong_key", "cosign", "labels", "assembly", "capability", "version_tag", "target", "embedded_key", "embedded_mok", "embedded_catalog", "catalog_duplicate", "catalog_nonfinite", "firn_missing"])
def test_preflight_fails_closed_on_untrusted_inspection(qa, tmp_path, monkeypatch, failure):
    m = manifest()
    # The fake byte streams have hand-derived checksums, never the manifest's untrusted hashes.
    m.update(index_key_sha256=hashlib.sha256(b"index-key").hexdigest(),
             cosign_key_sha256=hashlib.sha256(b"cosign-key").hexdigest(),
             mok_cert_sha256=hashlib.sha256(b"mok-cert").hexdigest(),
              iso_sha256=hashlib.sha256(ISO_BYTES).hexdigest())
    checked = tmp_path / "checks.json"
    calls = []

    def inspector(args, **kwargs):
        assert isinstance(args, list) and not kwargs.get("shell")
        calls.append(args)
        # External inspectors are mocked at the process boundary; no live calls.
        return fake_inspector(args, m, failure, **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    reason = {
        "index": "iso_index", "duplicate_index": "iso_index", "iso": "iso_hash",
        "fingerprint": "key_fingerprint", "wrong_key": "index_signer",
        "cosign": "oci_signature", "labels": "oci_version", "assembly": "oci_assembly",
        "capability": "oci_capability", "version_tag": "tag_drift", "target": "tag_drift",
        "embedded_key": "iso_embedded_key", "embedded_mok": "iso_embedded_key",
        "embedded_catalog": "iso_catalog", "catalog_duplicate": "iso_catalog", "catalog_nonfinite": "iso_catalog", "firn_missing": "iso_embedded",
    }[failure]
    with pytest.raises(qa.GateError, match="^" + reason + "$"):
        qa.preflight(m, checked)
    assert not checked.exists()
    assert calls


def test_manifest_key_hash_mismatch_never_reaches_signature_verification(qa, tmp_path, monkeypatch):
    m = manifest()
    calls = []

    def inspector(args, **kwargs):
        calls.append(args)
        return fake_inspector(args, m, None, **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    with pytest.raises(qa.GateError, match="key_hash"):
        qa.preflight(m, tmp_path / "checks.json")
    assert all(args[0] == "curl" for args in calls)


def test_signed_iso_without_valid_gpt_esp_never_extracts(qa, tmp_path, monkeypatch):
    broken = bytearray(ISO_BYTES)
    broken[512:520] = b"NO GPT!!"
    m = manifest()
    m.update(index_key_sha256=hashlib.sha256(b"index-key").hexdigest(),
             cosign_key_sha256=hashlib.sha256(b"cosign-key").hexdigest(),
             mok_cert_sha256=hashlib.sha256(b"mok-cert").hexdigest(),
             iso_sha256=hashlib.sha256(broken).hexdigest())
    calls = []

    def inspector(args, **kwargs):
        calls.append(args)
        result = fake_inspector(args, m, None, **kwargs)
        if args[0] == "curl" and args[-1] == m["iso_url"]:
            Path(args[args.index("-o") + 1]).write_bytes(broken)
        return result

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    with pytest.raises(qa.GateError, match="^iso_gpt$"):
        qa.preflight(m, tmp_path / "checks.json")
    assert not any(args[0] == "mcopy" for args in calls)


@pytest.mark.parametrize(("damage", "code"), [
    ("header_crc", "iso_gpt"), ("entries_crc", "iso_gpt"),
    ("wrong_esp_guid", "iso_esp"), ("partition_outside_iso", "iso_esp"),
])
def test_gpt_partition_two_rejects_corrupt_checksums_type_and_bounds(qa, tmp_path, damage, code):
    raw = bytearray(ISO_BYTES)
    if damage == "header_crc":
        raw[512 + 16] ^= 1
    elif damage == "entries_crc":
        raw[512 + 88] ^= 1
    else:
        if damage == "wrong_esp_guid":
            raw[1024 + 128] ^= 1
        else:
            struct.pack_into("<Q", raw, 1024 + 128 + 40, 16)
        struct.pack_into("<I", raw, 512 + 88, zlib.crc32(raw[1024:1280]))
        header = bytearray(raw[512:604])
        header[16:20] = b"\0" * 4
        struct.pack_into("<I", raw, 512 + 16, zlib.crc32(header))
    image = tmp_path / "installer.iso"
    image.write_bytes(raw)
    with pytest.raises(qa.GateError, match="^" + code + "$"):
        qa.esp_offset(image)


def test_gpt_248_entries_accepts_esp_in_partition_two(qa, tmp_path):
    image = tmp_path / "installer.iso"
    image.write_bytes(iso_bytes_with_entry_count(248))
    assert qa.esp_offset(image) == 204 * 512


def test_gpt_more_than_1024_entries_rejected(qa, tmp_path):
    image = tmp_path / "installer.iso"
    image.write_bytes(iso_bytes_with_entry_count(1025))
    with pytest.raises(qa.GateError, match="^iso_gpt$"):
        qa.esp_offset(image)


def test_iso_archive_allows_four_gib_decompressed_cpio(qa, tmp_path, monkeypatch):
    image = tmp_path / "installer.iso"
    image.write_bytes(ISO_BYTES)
    m = manifest()
    archive_limits = []
    original_inspect_file = qa.inspect_file

    def inspect_file(args, code, destination, **kwargs):
        if args[:2] == ["zstd", "-dc"]:
            assert code == "iso_archive"
            archive_limits.append(kwargs["max_size"])
        return original_inspect_file(args, code, destination, **kwargs)

    monkeypatch.setattr(qa, "inspect_file", inspect_file)
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kw: fake_inspector(args, m, None, **kw))
    qa.inspect_iso(image, tmp_path, m, b"index-key", b"cosign-key", b"mok-cert")
    assert archive_limits == [4 * 1024**3]
    assert archive_limits[0] > 1_500_000_000


def test_preflight_records_vm_validator_contract_without_executing_firn(qa, tmp_path, monkeypatch):
    m = manifest()
    m.update(index_key_sha256=hashlib.sha256(b"index-key").hexdigest(),
             cosign_key_sha256=hashlib.sha256(b"cosign-key").hexdigest(),
             mok_cert_sha256=hashlib.sha256(b"mok-cert").hexdigest(),
             iso_sha256=hashlib.sha256(ISO_BYTES).hexdigest())
    checks = tmp_path / "checks.json"

    calls = []

    def inspector(args, **kwargs):
        calls.append(args[0])
        assert args[0] != "bwrap"
        return fake_inspector(args, m, None, **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    qa.preflight(m, checks)
    assert "cpio" in calls
    data = json.loads(checks.read_text())
    assert data["firn_sha256"] == hashlib.sha256(EMBEDDED_FILES["usr/bin/firn"]).hexdigest()
    assert data["firn_provenance"] == "iso-esp-p2/firn-installer/initrd.img"
    assert data["firn_compatibility"] == "installer-vm-v2-recipe-validator-only"
    assert data["assembly_n"] == "bootc-1.16.8-storage-digest-v1"
    assert data["assembly_n_plus_1"] == "bootc-1.16.8-storage-digest-v1"
    qa.check_state(m, data)


def preflight_manifest():
    m = manifest()
    m.update(index_key_sha256=hashlib.sha256(b"index-key").hexdigest(),
             cosign_key_sha256=hashlib.sha256(b"cosign-key").hexdigest(),
             mok_cert_sha256=hashlib.sha256(b"mok-cert").hexdigest(),
             iso_sha256=hashlib.sha256(ISO_BYTES).hexdigest())
    return m


def test_preflight_accepts_a_listed_assembly_per_image_and_records_both(qa, tmp_path, monkeypatch):
    # An N to N+1 update may cross a bootc version, so the two labels differ.
    m = preflight_manifest()
    checks = tmp_path / "checks.json"
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kwargs: fake_inspector(
        args, m, None, assembly_n="bootc-1.16.8-storage-digest-v1",
        assembly_n_plus_1="bootc-1.17.1-storage-digest-v1", **kwargs))
    qa.preflight(m, checks)
    data = json.loads(checks.read_text())
    assert data["assembly_n"] == "bootc-1.16.8-storage-digest-v1"
    assert data["assembly_n_plus_1"] == "bootc-1.17.1-storage-digest-v1"
    qa.check_state(m, data)
    qa.tag(m, "before-stage", checks)


@pytest.mark.parametrize("which", ["assembly_n", "assembly_n_plus_1"])
@pytest.mark.parametrize("assembly", ["bootc-1.17.2-storage-digest-v1", "bootc-1.16.8-storage-digest-v2",
                                      "bootc-1.17.1-storage-digest", "bootc-1.16.8-storage-digest-v1-extra"])
def test_preflight_rejects_an_unlisted_assembly_label(qa, tmp_path, monkeypatch, which, assembly):
    # The allowlist is exact: no pattern, no neighbouring bootc version.
    m = preflight_manifest()
    labels = {which: assembly}
    checked = tmp_path / "checks.json"
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kwargs: fake_inspector(
        args, m, None, **labels, **kwargs))
    with pytest.raises(qa.GateError, match="^oci_assembly$"):
        qa.preflight(m, checked)
    assert not checked.exists()


def test_signed_index_subkey_accepts_pinned_primary(qa, tmp_path, monkeypatch):
    m = manifest()
    m.update(index_key_sha256=hashlib.sha256(b"index-key").hexdigest(),
             cosign_key_sha256=hashlib.sha256(b"cosign-key").hexdigest(),
             mok_cert_sha256=hashlib.sha256(b"mok-cert").hexdigest(),
              iso_sha256=hashlib.sha256(ISO_BYTES).hexdigest())
    calls = []

    def inspector(args, **kwargs):
        calls.append(args)
        return fake_inspector(args, m, "subkey_signer", **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    qa.preflight(m, tmp_path / "checks.json")
    assert any(args[0] == "gpgv" for args in calls)


def test_missing_trust_material_has_distinct_bounded_reason(qa, tmp_path, monkeypatch, capsys):
    m = manifest()
    path = tmp_path / "normalized.json"
    path.write_bytes(qa.canonical(m))
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kwargs: fake_inspector(args, m, "trust_material_missing", **kwargs))
    assert qa.main(["preflight", str(path), str(tmp_path / "checks.json")]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:trust_material_missing"


@pytest.mark.parametrize("raw", [b"{" + b" " * 16384 + b"}", b'{"x":NaN}', b'{"x":1,"x":2}'])
def test_tag_rejects_oversize_nonfinite_or_duplicate_checks_without_registry(qa, tmp_path, monkeypatch, raw):
    path = tmp_path / "checks.json"
    path.write_bytes(raw)
    monkeypatch.setattr(qa.subprocess, "run", lambda *args, **kwargs: pytest.fail("registry reached"))
    with pytest.raises(qa.GateError):
        qa.tag(manifest(), "before-stage", path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("change", [
    lambda data, m: [data.pop(key) for key in list(data) if key not in ("manifest_sha256", "tags")],
    lambda data, m: data.pop("firn_sha256"),
    lambda data, m: data.update(firn_sha256="not-a-digest"),
    lambda data, m: data.update(firn_compatibility="unverified"),
    lambda data, m: data.update(firn_provenance="untrusted-source"),
    lambda data, m: data.update(assembly_n="bootc-1.17.2-storage-digest-v1"),
    lambda data, m: data.update(assembly_n_plus_1="bootc-1.16.8-storage-digest-v2"),
    lambda data, m: data.update(assembly_n="bootc-1.16.8-storage-digest-v1", assembly_n_plus_1="bad"),
    lambda data, m: data.pop("assembly_n"),
    lambda data, m: data.pop("assembly_n_plus_1"),
    lambda data, m: data.update(assembly_n=None),
    lambda data, m: data.update(iso_sha256="0" * 64),
    lambda data, m: data.update(snosi_commit="0" * 40),
    lambda data, m: data.update(digest_n="0" * 64),
    lambda data, m: data.update(digest_n_plus_1="0" * 64),
    lambda data, m: data.update(index_key_sha256="0" * 64),
    lambda data, m: data.update(cosign_key_sha256="0" * 64),
    lambda data, m: data.update(mok_cert_sha256="0" * 64),
    lambda data, m: data.update(version_n="00000000000000"),
    lambda data, m: data.update(version_n_plus_1="00000000000000"),
    lambda data, m: data.update(timestamp="not-a-time"),
    lambda data, m: data.update(tags={"before-stage": "not-a-time"}),
    lambda data, m: data.update(extra="forged"),
])
def test_tag_rejects_forged_or_incomplete_checks_before_registry(qa, tmp_path, monkeypatch, change):
    m = manifest()
    data = {"manifest_sha256": hashlib.sha256(qa.canonical(m)).hexdigest(),
            "timestamp": "2026-09-24T00:00:00+00:00", "version_n": m["version_n"],
            "version_n_plus_1": m["version_n_plus_1"],
            "digest_n": N, "digest_n_plus_1": NEXT, "snosi_commit": COMMIT,
            "iso_sha256": m["iso_sha256"], "firn_sha256": "f" * 64,
            "firn_provenance": "iso-esp-p2/firn-installer/initrd.img",
             "firn_compatibility": "installer-vm-v2-recipe-validator-only",
            "assembly_n": "bootc-1.16.8-storage-digest-v1",
            "assembly_n_plus_1": "bootc-1.16.8-storage-digest-v1",
            "index_key_sha256": m["index_key_sha256"],
            "cosign_key_sha256": m["cosign_key_sha256"],
            "mok_cert_sha256": m["mok_cert_sha256"], "tags": {}}
    change(data, m)
    path = tmp_path / "checks.json"
    original = qa.canonical(data)
    path.write_bytes(original)
    monkeypatch.setattr(qa.subprocess, "run", lambda *args, **kwargs: pytest.fail("registry reached"))
    with pytest.raises(qa.GateError, match="^checks_state$"):
        qa.tag(m, "after-stage", path)
    assert path.read_bytes() == original


def test_success_preflight_and_tag_recheck(qa, tmp_path, monkeypatch):
    m = manifest()
    m.update(index_key_sha256=hashlib.sha256(b"index-key").hexdigest(),
             cosign_key_sha256=hashlib.sha256(b"cosign-key").hexdigest(),
             mok_cert_sha256=hashlib.sha256(b"mok-cert").hexdigest(),
              iso_sha256=hashlib.sha256(ISO_BYTES).hexdigest())
    calls = []
    def inspector(args, **kwargs):
        assert isinstance(args, list) and not kwargs.get("shell")
        if args[0] == "curl" and args[-1] == m["iso_url"]:
            assert kwargs["timeout"] >= m["timeouts"]["install"]
        calls.append(args)
        return fake_inspector(args, m, None, **kwargs)

    monkeypatch.setattr(qa.subprocess, "run", inspector)
    checks = tmp_path / "checks.json"
    qa.preflight(m, checks)
    result = json.loads(checks.read_text())
    assert result["manifest_sha256"] == hashlib.sha256(qa.canonical(m)).hexdigest()
    assert result["digest_n_plus_1"] == NEXT
    assert result["firn_sha256"] == hashlib.sha256(b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00" + b"binary").hexdigest()
    assert result["snosi_commit"] == COMMIT
    assert result["timestamp"]
    assert all(args[0] != "bwrap" for args in calls)
    assert any(a[:2] == ["cosign", "verify"] and m["image_n"] in a for a in calls)
    qa.tag(m, "before-stage", checks)
    assert json.loads(checks.read_text())["tags"]["before-stage"]
    monkeypatch.setattr(qa.subprocess, "run", lambda args, **kwargs: fake_inspector(args, m, "target", **kwargs))
    with pytest.raises(qa.GateError):
        qa.tag(m, "after-stage", checks)
    assert "after-stage" not in json.loads(checks.read_text())["tags"]


PHASES = ("installed-n", "stage", "boot-n-plus-1", "rollback", "boot-n")
# As shipped by snosi (shared/bootc-secure/tree/etc/containers/policy.json) and
# observed on Snow 20261026105903.
SIGNED = [{"type": "sigstoreSigned", "keyPath": "/usr/lib/snosi/cosign.pub", "signedIdentity": {"type": "matchRepository"}}]
SHIPPED_POLICY = {"default": [{"type": "reject"}], "transports": {
    "docker": {f"ghcr.io/frostyard/{name}": SIGNED for name in ("cayo", "floe", "snow", "snowfield", "sundog")},
    **{name: {"": [{"type": "insecureAcceptAnything"}]} for name in
       ("containers-storage", "tarball", "docker-archive", "oci-archive", "dir", "oci")}}}
# systemd-cryptenroll's signed-policy token as observed on Snow (no secrets).
TPM_TOKEN = {"type": "systemd-tpm2", "tpm2-pcrs": [], "tpm2_pubkey_pcrs": [11], "tpm2_pubkey": "public"}
NONCE = "0123456789abcdef0123456789abcdef"
UKI_PATH = "/EFI/Linux/bootc/bootc_composefs-" + "d" * 128 + ".efi"
BLOCKS = {"blockdevices": [{"path": "/dev/vda3", "pkname": "/dev/vda", "partn": "3", "fstype": "crypto_LUKS"},
                           {"path": "/dev/vda2", "pkname": "/dev/vda", "partn": "2", "fstype": "vfat"}]}


KERNEL = "7.3.1+deb14-amd64"   # not on the known-defect list
KNOWN_DEFECT_KERNEL = "7.2.11+deb14-amd64"


# Firn installs with --target-imgref (registry); the Snow updater then switches the
# spec to containers-storage, which the staged and N+1 deployments keep reporting.
SPEC_TRANSPORT = {"installed-n": "registry", "stage": "containers-storage",
                  "boot-n-plus-1": "containers-storage", "rollback": "containers-storage",
                  "boot-n": "registry"}
# The three-line file the Snow updater writes (bootc-update-stage).
STAGED_FILE = f"image={REPO}:qa-controlled\ndigest=sha256:{NEXT}\nstaged_at=2026-09-24T00:00:00+00:00\n"


def phase_record(phase):
    newer = phase in ("boot-n-plus-1", "rollback")
    record = dict(phase=phase, nonce=NONCE, boot_id="12345678-1234-4234-8234-123456789abc",
                  spec_image=f"{REPO}:qa-controlled", spec_transport=SPEC_TRANSPORT[phase], booted_digest=NEXT if newer else N,
                  booted_version="20260924000000" if newer else "20260923000000",
                  staged_digest=NEXT if phase == "stage" else None,
                   rollback_digest=N if phase in ("boot-n-plus-1", "rollback") else NEXT if phase == "boot-n" else None,
                  policy_sha256="2" * 64, policy_ok=True, pull_ok=True,
                  pull_digest=NEXT, secure_boot=True, uki_type2=True, lockdown=True,
                   root_luks=True, root_btrfs=True, tpm_pcr11_tokens=1,
                   kernel=KERNEL, fsverity_errors=0, action=None, marker_digest=None)
    if phase == "stage":
        record.update(action="staged", marker_digest=NEXT)
    if phase == "rollback":
        record.update(action="rolled-back")
    return record


def serial_record(record):
    raw = json.dumps(record, separators=(",", ":")).encode()
    return "boot message unrelated\nSNOW_QA_V1 " + base64.urlsafe_b64encode(raw).decode().rstrip("=") + "\nother serial data\n"


def phase_files(tmp_path, qa):
    m = manifest()
    normalized = tmp_path / "normalized.json"
    normalized.write_bytes(qa.canonical(m))
    checks = tmp_path / "checks.json"
    checks.write_bytes(qa.canonical(dict(manifest_sha256=qa.sha(qa.canonical(m)),
        timestamp="2026-09-24T00:00:00+00:00", version_n=m["version_n"],
        version_n_plus_1=m["version_n_plus_1"], digest_n=N, digest_n_plus_1=NEXT,
        snosi_commit=COMMIT, iso_sha256=ISO, firn_sha256="f" * 64,
        firn_provenance="iso-esp-p2/firn-installer/initrd.img",
        firn_compatibility="installer-vm-v2-recipe-validator-only",
        assembly_n="bootc-1.16.8-storage-digest-v1",
        assembly_n_plus_1="bootc-1.16.8-storage-digest-v1", index_key_sha256="1" * 64,
        cosign_key_sha256="2" * 64, mok_cert_sha256="3" * 64, tags={})))
    return normalized, checks


@pytest.mark.parametrize("phase", PHASES)
def test_phase_accepts_one_attested_fresh_boot(qa, tmp_path, capsys, phase):
    normalized, checks = phase_files(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text(serial_record(phase_record(phase)))
    assert qa.main(["phase", str(normalized), phase, NONCE,
                    "none", str(serial), str(checks)]) == 0
    assert capsys.readouterr().out.splitlines() == ["12345678-1234-4234-8234-123456789abc",
                                                     "fsverity_errors=0", "kernel=" + KERNEL]


def test_phase_accepts_identical_copies_of_one_record(qa, tmp_path, capsys):
    # The guest writes its record to ttyS0 and again after serial-getty's TTY
    # setup; ONLCR may add a CR. Identical copies are one record.
    normalized, checks = phase_files(tmp_path, qa)
    record = serial_record(phase_record("stage"))
    serial = tmp_path / "serial"
    serial.write_bytes(record.encode() + record.replace("\n", "\r\n").encode())
    assert qa.main(["phase", str(normalized), "stage", NONCE,
                    "none", str(serial), str(checks)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "12345678-1234-4234-8234-123456789abc"


def test_guest_publishes_to_serial_and_replays_after_getty(qa, tmp_path, monkeypatch):
    # /dev/console is tty0 on Snow: the record must reach ttyS0, and again after
    # serial-getty's TTY setup (run-snow-bootc-lifecycle-2tnjh).
    ttys0, console = tmp_path / "ttyS0", tmp_path / "console"
    monkeypatch.setattr(qa, "GUEST_CONSOLES", (str(ttys0), str(console)))
    monkeypatch.setattr(qa, "PUBLISH_HOLD", 0)  # the hold has its own test
    states = iter([False, False, True])
    monkeypatch.setattr(qa, "serial_getty_ready", lambda **_: next(states))
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    monkeypatch.setattr(qa, "guest", lambda _: "SNOW_QA_V1 abc")
    assert qa.main(["guest", "/irrelevant"]) == 0
    assert ttys0.read_text() == "\nSNOW_QA_V1 abc\n" * 2
    assert console.read_text() == "\nSNOW_QA_V1 abc\n" * 2
    # A missing ttyS0 is not fatal, and the wait for agetty is bounded.
    monkeypatch.setattr(qa, "GUEST_CONSOLES", (str(tmp_path / "missing" / "ttyS0"),))
    monkeypatch.setattr(qa, "serial_getty_ready", lambda **_: False)
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(qa.time, "monotonic", lambda: next(clock))
    assert qa.main(["guest", "/irrelevant"]) == 0


@pytest.mark.parametrize("damage", [
    "missing", "duplicate", "malformed", "stale", "old_phase", "ambiguous", "no_reboot",
    "staged_missing", "staged_wrong", "signature_missing", "policy_missing", "wrong_spec",
    "updater_failed", "secure_boot_missing", "uki_missing", "lockdown_missing",
    "tpm_missing", "luks_missing", "btrfs_missing", "outcome_missing", "rollback_missing",
    "marker_missing", "secret", "extra_record", "invalid_nonce", "wrong_version",
    "policy_hash_wrong", "pull_digest_wrong", "transport_wrong", "unattended_missing",
])
def test_phase_rejects_untrustworthy_serial_or_state(qa, tmp_path, capsys, damage):
    normalized, checks = phase_files(tmp_path, qa)
    phase = "rollback" if damage == "rollback_missing" else "stage"
    record = phase_record(phase)
    prior = "none"
    if damage == "stale":
        record["nonce"] = "f" * 32
    elif damage == "old_phase":
        record["phase"] = "installed-n"
    elif damage == "ambiguous":
        record["boot_id"] = ""
    elif damage == "no_reboot":
        prior = record["boot_id"]
    elif damage == "staged_missing":
        record["staged_digest"] = None
    elif damage == "staged_wrong":
        record["staged_digest"] = N
    elif damage == "signature_missing":
        record["pull_ok"] = False
    elif damage == "policy_missing":
        record["policy_ok"] = False
    elif damage == "wrong_spec":
        record["spec_image"] = f"{REPO}:latest"
    elif damage == "updater_failed":
        record["action"] = "failed"
    elif damage in ("secure_boot_missing", "uki_missing", "lockdown_missing", "luks_missing", "btrfs_missing"):
        record[{"secure_boot_missing": "secure_boot", "uki_missing": "uki_type2",
                "lockdown_missing": "lockdown", "luks_missing": "root_luks",
                "btrfs_missing": "root_btrfs"}[damage]] = False
    elif damage == "tpm_missing":
        record["tpm_pcr11_tokens"] = 0
    elif damage == "outcome_missing":
        record["action"] = None
    elif damage == "rollback_missing":
        record["rollback_digest"] = None
    elif damage == "marker_missing":
        record["marker_digest"] = None
    elif damage == "secret":
        record["recovery_key"] = "must-not-be-retained"
    elif damage == "wrong_version":
        record["booted_version"] = "00000000000000"
    elif damage == "policy_hash_wrong":
        record["policy_sha256"] = "0" * 64
    elif damage == "pull_digest_wrong":
        record["pull_digest"] = N
    elif damage == "transport_wrong":
        record["spec_transport"] = "docker"
    elif damage == "unattended_missing":
        record["unattended"] = True
    serial = tmp_path / "serial"
    text = serial_record(record)
    if damage == "missing":
        text = "unrelated serial\n"
    elif damage == "duplicate":
        # Two different records for one phase (the guest repeats only an identical line).
        text += serial_record(dict(record, boot_id="87654321-1234-4234-8234-123456789abc"))
    elif damage == "malformed":
        text = "unrelated serial\nSNOW_QA_V1 not-base64!\n"
    elif damage == "extra_record":
        text += serial_record(phase_record("installed-n"))
    serial.write_text(text)
    nonce = "bad" if damage == "invalid_nonce" else NONCE
    assert qa.main(["phase", str(normalized), phase, nonce, prior, str(serial), str(checks)]) == 1
    output = capsys.readouterr()
    assert not output.out and "must-not-be-retained" not in output.err
    if damage in ("duplicate", "malformed", "extra_record"):
        assert output.err.strip() == "qa_failed:" + ("record_encoding" if damage == "malformed" else "record_count")


CLEAN_JOURNAL = b"kernel: Linux version 7.2.12\nsystemd[1]: Started something.\n"
DEFECT_JOURNAL = (CLEAN_JOURNAL
                  + b"kernel: fs-verity (dm-0, inode 5): FILE CORRUPTED! pos=0, level=-1\n"
                  + b"kernel: fsverity_msg: 5 callbacks suppressed\n"
                  + b"kernel: fs-verity (dm-0, inode 6): FILE CORRUPTED! pos=4096, level=-1\n")


NO_KERNEL_ENTRY = None  # expected value: the guest must refuse to attest a count


@pytest.mark.parametrize("journal,expected", [
    (CLEAN_JOURNAL, 0), (DEFECT_JOURNAL, 7),
    # journalctl exits 0 and prints a banner (or nothing) when it read no kernel entry.
    (b"", NO_KERNEL_ENTRY), (b"-- No entries --\n", NO_KERNEL_ENTRY),
    (b"  \n-- No entries --  \n", NO_KERNEL_ENTRY),
])
@pytest.mark.parametrize("phase", ["stage", "rollback"])
def test_guest_stage_probes_and_emits_allowlisted_single_record(qa, tmp_path, monkeypatch, phase, journal, expected):
    normalized, checks = phase_files(tmp_path, qa)
    m = manifest()
    m["cosign_key_sha256"] = hashlib.sha256(b"public-key").hexdigest()
    normalized.write_bytes(qa.canonical(m))
    state = json.loads(checks.read_text())
    state["manifest_sha256"] = qa.sha(qa.canonical(m))
    state["cosign_key_sha256"] = m["cosign_key_sha256"]
    checks.write_bytes(qa.canonical(state))
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    for key, value in {"phase": phase, "nonce": NONCE, "image_n": f"{REPO}@sha256:{N}",
                        "image_n_plus_1": f"{REPO}@sha256:{NEXT}", "target_ref": f"{REPO}:qa-controlled"}.items():
        (credentials / key).write_text(value + "\n")
    (credentials / "unit_started").write_text("900.00\n")
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized, raising=False)
    status = {"spec": {"image": {"image": f"{REPO}:qa-controlled", "transport": "registry" if phase == "stage" else "containers-storage"}},
              "status": {"booted": {"image": {"imageDigest": "sha256:" + (N if phase == "stage" else NEXT),
                                              "version": "20260923000000" if phase == "stage" else "20260924000000"}},
                         "staged": None, "rollback": None if phase == "stage" else {"image": {"imageDigest": "sha256:" + N}}}}
    root = tmp_path / "root"
    root.mkdir()
    (root / "boot_id").write_text("12345678-1234-4234-8234-123456789abc\n")
    (root / "policy.json").write_text(json.dumps(SHIPPED_POLICY))
    (root / "key").write_bytes(b"public-key")
    (root / "update-check").write_text("outcome=staged\n")
    (root / "update-staged").write_text(STAGED_FILE)
    (root / "tokens.json").write_text(json.dumps({"tokens": {"0": TPM_TOKEN}}))
    (root / "uptime").write_text("1000.00 500.00\n")
    (root / "osrelease").write_text(KERNEL + "\n")
    esp = root / "esp"
    (esp / "loader/entries").mkdir(parents=True)
    (esp / UKI_PATH.lstrip("/")).parent.mkdir(parents=True)
    (esp / UKI_PATH.lstrip("/")).write_bytes(b"mock-uki")
    (esp / "loader/entries/snow.conf").write_text("title Snow\nuki " + UKI_PATH + "\n")
    monkeypatch.setattr(qa, "GUEST_PATHS", {"boot_id": root / "boot_id", "policy": root / "policy.json",
        "key": root / "key", "update_check": root / "update-check", "update_staged": root / "update-staged",
        "esp": esp, "uptime": root / "uptime", "osrelease": root / "osrelease"})
    calls = []
    clock = [1000]

    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs.get("capture_output") and not kwargs.get("shell")
        phase_budget = m["timeouts"]["stage" if phase == "stage" else "reboot"]
        if args[:2] == ["podman", "pull"] or args in (["/usr/libexec/bootc-update-stage"], ["bootc", "rollback"]):
            assert kwargs["timeout"] == phase_budget - 120 - (clock[0] - 900)
        else:
            assert kwargs["timeout"] == 120
        if args[:2] == ["bootc", "status"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(status).encode(), b"")
        if args[:2] == ["podman", "pull"]:
            clock[0] += 180
            (root / "uptime").write_text(f"{clock[0]:.2f} 500.00\n")
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:2] == ["podman", "image"]:
            return subprocess.CompletedProcess(args, 0, ("sha256:" + NEXT).encode(), b"")
        if args[0] == "/usr/libexec/bootc-update-stage":
            status["spec"]["image"]["transport"] = "containers-storage"
            status["status"]["staged"] = {"image": {"imageDigest": "sha256:" + NEXT}}
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args == ["bootc", "rollback"]:
            status["status"]["rollback"] = {"image": {"imageDigest": "sha256:" + NEXT}}
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:2] == ["cryptsetup", "luksDump"]:
            return subprocess.CompletedProcess(args, 0, (root / "tokens.json").read_bytes(), b"")
        if args[0] == "lsblk":
            return subprocess.CompletedProcess(args, 0, json.dumps(BLOCKS).encode(), b"")
        if args[0] in ("mount", "umount"):
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args == ["journalctl", "-q", "-k", "-b", "--no-pager"]:
            return subprocess.CompletedProcess(args, 0, journal, b"")
        # bootctl 262 as observed on Snow: a Stub tree line and "uki: <esp>//EFI/...".
        responses = {"mokutil": b"SecureBoot enabled", "bootctl": ("  Secure Boot: enabled (user)\n  Measured UKI: yes\n           Stub: \u2514\u2500" + UKI_PATH + "\n  Current: $BOOT" + UKI_PATH + " (on the EFI System Partition)\n          uki: " + str(esp) + "/" + UKI_PATH + "\n").encode(),
                      "cryptsetup": b"/dev/mapper/root is active and is in use.\n  device:   /dev/vda3"}
        if args == ["findmnt", "-n", "-o", "FSTYPE", "/sysroot"]:
            # A composefs boot: / is overlay, the btrfs root is /sysroot.
            return subprocess.CompletedProcess(args, 0, b"btrfs\n", b"")
        if args[0] in responses:
            return subprocess.CompletedProcess(args, 0, responses[args[0]], b"")
        pytest.fail(f"unexpected guest tool {args}")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)
    monkeypatch.setattr(qa, "guest_lockdown", lambda: True)
    if expected is NO_KERNEL_ENTRY:
        with pytest.raises(qa.GateError, match="^fsverity_log$"):
            qa.guest(credentials)
        return
    line = qa.guest(credentials)
    assert line.startswith("SNOW_QA_V1 ")
    payload = json.loads(base64.urlsafe_b64decode(line.split()[1] + "=="))
    assert payload["action"] == ("staged" if phase == "stage" else "rolled-back")
    assert payload["booted_digest"] == (N if phase == "stage" else NEXT)
    assert payload["rollback_digest"] == (None if phase == "stage" else N)
    assert payload["spec_image"] == f"{REPO}:qa-controlled"
    assert payload["spec_transport"] == "containers-storage"   # observed, not normalized
    # snosi#1130: FILE CORRUPTED lines plus suppressed-callback counts, read after the action.
    assert payload["fsverity_errors"] == expected
    assert payload["kernel"] == KERNEL   # attested from /proc/sys/kernel/osrelease
    assert calls.index(["journalctl", "-q", "-k", "-b", "--no-pager"]) > calls.index(
        ["/usr/libexec/bootc-update-stage"] if phase == "stage" else ["bootc", "rollback"])
    assert "unattended" not in payload  # Host establishes freshness; guest cannot attest human absence.
    assert set(payload) == set(phase_record(phase))
    assert ["cryptsetup", "luksDump", "--dump-json-metadata", "/dev/vda3"] in calls
    assert ["bootctl", "--esp-path=" + str(esp), "--no-pager", "status"] in calls
    assert ["mount", "--no-mtab", "-t", "vfat", "-o", "ro,nosuid,nodev,noexec", "/dev/vda2", str(esp)] in calls
    assert ["umount", str(esp)] in calls
    action = ["/usr/libexec/bootc-update-stage"] if phase == "stage" else ["bootc", "rollback"]
    assert action in calls
    assert calls.index(["podman", "pull", f"{REPO}:qa-controlled"]) < calls.index(action)
    serial = tmp_path / "serial-snapshot"
    serial.write_text("unrelated serial line\n" + line + "\n")
    assert qa.phase_verdict(m, phase, NONCE, "aaaaaaaa-1234-4234-8234-123456789abc", serial, checks) == payload["boot_id"]
    with pytest.raises(qa.GateError, match="^boot_identity$"):
        qa.phase_verdict(m, phase, NONCE, payload["boot_id"], serial, checks)


def run_phase_verdict_error(qa, tmp_path, phase, record):
    normalized, checks = phase_files(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text(serial_record(record))
    with pytest.raises(qa.GateError) as error:
        qa.phase_attest(manifest(), phase, NONCE, "none", serial, checks)
    return str(error.value)


@pytest.mark.parametrize("phase", PHASES)
def test_record_spec_image_must_be_target_ref_in_every_phase(qa, tmp_path, phase):
    record = phase_record(phase)
    record["spec_image"] = f"{REPO}@sha256:{N}"   # image_n: what Firn does NOT record
    assert run_phase_verdict_error(qa, tmp_path, phase, record) == "target_spec"
    record["spec_image"] = f"{REPO}:latest"
    assert run_phase_verdict_error(qa, tmp_path, phase, record) == "target_spec"


@pytest.mark.parametrize(("phase", "allowed"), [
    ("installed-n", {"registry"}),
    ("stage", {"registry", "containers-storage"}),
    ("boot-n-plus-1", {"registry", "containers-storage"}),
    ("rollback", {"registry", "containers-storage"}),
    ("boot-n", {"registry", "containers-storage"}),
])
@pytest.mark.parametrize("transport", ["registry", "containers-storage", "docker", "oci", "dir", "", None])
def test_record_spec_transport_rules_per_phase(qa, tmp_path, phase, allowed, transport):
    record = phase_record(phase)
    record["spec_transport"] = transport
    normalized, checks = phase_files(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text(serial_record(record))
    if transport in allowed:
        assert qa.phase_attest(manifest(), phase, NONCE, "none", serial, checks)["spec_transport"] == transport
    else:
        with pytest.raises(qa.GateError, match="^target_transport$"):
            qa.phase_attest(manifest(), phase, NONCE, "none", serial, checks)


def test_installed_n_record_is_exactly_what_firn_wrote(qa):
    assert phase_record("installed-n")["spec_transport"] == "registry"
    assert phase_record("installed-n")["spec_image"] == manifest()["target_ref"]
    assert phase_record("stage")["spec_transport"] == "containers-storage"


def marker(**changes):
    fields = {"image": f"{REPO}:qa-controlled", "digest": "sha256:" + NEXT, "staged_at": "2026-09-24T00:00:00+00:00"}
    fields.update(changes)
    return "".join(f"{key}={value}\n" for key, value in fields.items() if value is not None).encode()


def test_staged_marker_accepts_the_updater_file(qa):
    qa.staged_marker(manifest(), STAGED_FILE.encode())
    qa.staged_marker(manifest(), marker(staged_at=None))          # staged_at is optional
    qa.staged_marker(manifest(), marker(staged_at="anything printable"))   # and ignored
    qa.staged_marker(manifest(), marker().rstrip(b"\n"))          # no final newline


@pytest.mark.parametrize("raw", [
    marker(digest="sha256:" + N),                                  # wrong digest
    marker(digest=NEXT),                                           # bare hash, not sha256:<hex>
    marker(digest="sha256:" + NEXT.upper()),
    marker(image=f"{REPO}@sha256:{N}"),                            # wrong image
    marker(image=f"{REPO}:latest"),
    marker(digest=None),                                           # missing digest
    marker(image=None),                                            # missing image
    marker() + b"digest=sha256:" + NEXT.encode() + b"\n",          # duplicate key
    marker() + b"image=" + f"{REPO}:qa-controlled".encode() + b"\n",
    marker() + b"staged_at=x\n",
    marker() + b"extra=1\n",                                       # unknown key
    marker() + b"no separator\n",
    ("sha256:" + NEXT + "\n").encode(),                            # the old, wrong format
    b"",
    marker(staged_at="x" * 4100),                                  # oversize
    marker(staged_at="x" * 257),                                   # oversize value
    marker(staged_at="a\x00b"),
    marker(staged_at="caf\u00e9").replace(b"caf\\u00e9", "caf\u00e9".encode()),
    b"\xff\xfe",
])
def test_staged_marker_rejects_malformed_or_foreign_file(qa, raw):
    with pytest.raises(qa.GateError, match="^stage_marker$"):
        qa.staged_marker(manifest(), raw)


def test_guest_reads_a_bounded_staged_marker(qa):
    script = yaml.safe_load(SOURCE.read_text())["data"]["qa.py"]
    assert 'source.read(4097)' in script


def run_script():
    return yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]


def phase_loop(script):
    # The boots come from the plan in the normalized manifest (five lifecycle
    # phases, or three sysext-probe boots); the loop body is one shared path.
    loop = re.search(r"(?ms)^for phase in \"\$\{phase_plan\[@\]\}\"; do\n.*?^done\n", script)
    assert loop
    return loop.group()


def test_every_phase_boot_disables_the_update_timer_via_dropin_credential():
    loop = phase_loop(run_script())
    match = re.search(r"td=\$\(printf '(\[Unit\]\\nConditionPathExists=[^']*)' \| base64 -w0\)", loop)
    assert match
    assert match[1].replace("\\n", "\n") == "[Unit]\nConditionPathExists=/run/snow-qa-timer-disabled"
    assert "systemd.unit-dropin.bootc-update-stage.service=$td" in loop
    # Same pattern as the multi-user.target drop-in, in the one raw.qemu of the phase.
    assert loop.count("systemd.unit-dropin.multi-user.target=$gd") == 1
    assert loop.count("raw.qemu=") == 1
    assert "${#td} -lt 60000" in loop
    # Nothing in the lane creates that path (the stage phase runs the updater directly).
    assert "snow-qa-timer-disabled" not in run_script().replace("ConditionPathExists=/run/snow-qa-timer-disabled", "")


def guest_payload(tmp_path):
    script = run_script()
    assignment = re.search(r'(?m)^  guestunit="\[Unit\].*?^  gb=', script, re.S)
    assert assignment
    values = dict(phase="stage", nonce=NONCE, n=f"{REPO}@sha256:{N}", next=f"{REPO}@sha256:{NEXT}",
                  target=f"{REPO}:qa-controlled", qa_b64=base64.b64encode(zlib.compress(b"x")).decode(),
                  manifest_b64="e30=")
    unit = subprocess.run(["bash", "-c", assignment.group().rsplit("  gb=", 1)[0] + '\nprintf "%s" "$guestunit"'],
                          env={**os.environ, **values}, capture_output=True, check=True, text=True).stdout
    command = next(line.removeprefix("ExecStart=") for line in unit.splitlines() if line.startswith("ExecStart="))
    expanded = re.sub(r"%(%|s)", lambda m: "%" if m[1] == "%" else "/bin/bash", command)
    payload = shlex.split(expanded)[2]
    for path in ("/opt/snow-qa", "/run/snow-credentials", "/run/snow-qa.z"):
        payload = payload.replace(path, str(tmp_path / path.lstrip("/")))
    return payload


@pytest.mark.parametrize("guest_rc", [0, 1])
def test_guest_unit_powers_off_only_after_a_successful_guest_run(tmp_path, guest_rc):
    payload = guest_payload(tmp_path)
    assert payload.rstrip().endswith("systemctl --no-block poweroff")
    assert payload.count("systemctl") == 1
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "systemctl").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
    (tools / "python3").write_text('#!/bin/sh\ncase "$*" in *" guest "*) exit "$GUEST_RC";; esac\nexec /usr/bin/python3 "$@"\n')
    for tool in tools.iterdir():
        tool.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(["bash", "-e", "-c", payload], capture_output=True,
                            env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "GUEST_RC": str(guest_rc), "CALLS": str(calls)})
    assert (result.returncode == 0) is (guest_rc == 0)
    assert (calls.read_text() if calls.exists() else "") == ("--no-block poweroff\n" if guest_rc == 0 else "")


def test_host_waits_bounded_for_guest_shutdown_and_never_force_stops_between_phases():
    script = run_script()
    loop = phase_loop(script)
    assert "--force" not in loop and "incus stop" not in loop
    assert 'fail guest_stop' not in script
    assert re.search(r'shutdown_budget=\$\(python3 -c .*\["timeouts"\]\["reboot"\]\).*\|\| fail manifest_read', loop)
    assert "shutdown_end=$((SECONDS + shutdown_budget + 150))" in loop
    assert re.search(r"until vm_stopped; do\n\s+\(\( SECONDS < shutdown_end \)\) \|\| fail guest_shutdown\n\s+sleep 5\n\s+done", loop)
    assert "while true" not in loop and "while :" not in loop
    # The wait follows the accepted record and precedes the next phase's baseline snapshot.
    assert loop.index("qa.py phase") < loop.index("until vm_stopped")
    assert loop.index("until vm_stopped") > loop.index("tag after-")
    # Force remains only for teardown and the installer ISO stop after SNOW_INSTALL_OK.
    stops = re.findall(r'(?m)^.*incus stop .*$', script)
    assert len(stops) == 1 and "iso_stop" in stops[0]
    assert script.index("SNOW_INSTALL_OK") < script.index(stops[0]) < script.index('for phase in "${phase_plan[@]}"')
    assert "incus info" in script and "Status: STOPPED" in script


def test_run_unit_survives_systemd_specifiers_and_delivers_guest_credentials(tmp_path):
    """Exercise the run.sh assignment, not a copied/fabricated unit string."""
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assignment = re.search(r'(?m)^  guestunit="\[Unit\].*?^  gb=', script, re.S)
    assert assignment
    values = dict(phase="stage", nonce=NONCE, n=f"{REPO}@sha256:{N}",
                  next=f"{REPO}@sha256:{NEXT}", target=f"{REPO}:qa-controlled",
                  qa_b64=base64.b64encode(zlib.compress(b"test payload")).decode(),
                  manifest_b64=base64.b64encode(b"{}").decode())
    # Execute precisely the shell assignment run.sh uses with safe sample values.
    extract = subprocess.run(["bash", "-c", assignment.group().rsplit("  gb=", 1)[0] +
                              '\nprintf "%s" "$guestunit"'], env={**os.environ, **values},
                             capture_output=True, check=True, text=True).stdout
    command = next(line.removeprefix("ExecStart=") for line in extract.splitlines()
                   if line.startswith("ExecStart="))
    # systemd's ExecStart specifier pass: %% is literal %, %s is the invoking
    # user's shell; fail on any other or dangling specifier instead of ignoring it.
    assert not re.search(r"%(?![%s])|%(?:$)", command)
    expanded = re.sub(r"%(%|s)", lambda m: "%" if m[1] == "%" else "/bin/bash", command)
    argv = shlex.split(expanded)
    assert argv[:2] == ["/bin/bash", "-ec"]
    payload = argv[2]
    for path in ("/opt/snow-qa", "/run/snow-credentials", "/run/snow-qa.z"):
        payload = payload.replace(path, str(tmp_path / path.lstrip("/")))
    payload = payload.replace("python3 " + str(tmp_path / "opt/snow-qa/qa.py") + " guest " +
                              str(tmp_path / "run/snow-credentials") + "; systemctl --no-block poweroff", ":")
    assert "systemctl" not in payload   # never power off the test host
    assert subprocess.run(["bash", "-e", "-c", payload], capture_output=True, check=True).returncode == 0
    assert (tmp_path / "opt/snow-qa/qa.py").read_bytes() == b"test payload"
    for key, expected in (("phase", values["phase"]), ("nonce", values["nonce"]),
                          ("image_n", values["n"]), ("image_n_plus_1", values["next"]),
                          ("target_ref", values["target"])):
        assert (tmp_path / "run/snow-credentials" / key).read_text() == expected


def test_guest_unit_records_uptime_before_bootstrap(tmp_path):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assignment = re.search(r'(?m)^  guestunit="\[Unit\].*?^  gb=', script, re.S)
    assert assignment
    values = dict(phase="stage", nonce=NONCE, n=f"{REPO}@sha256:{N}",
                  next=f"{REPO}@sha256:{NEXT}", target=f"{REPO}:qa-controlled",
                  qa_b64="not-base64", manifest_b64="e30=")
    unit = subprocess.run(["bash", "-c", assignment.group().rsplit("  gb=", 1)[0] +
                           '\nprintf "%s" "$guestunit"'], env={**os.environ, **values},
                          capture_output=True, check=True, text=True).stdout
    command = next(line.removeprefix("ExecStart=") for line in unit.splitlines()
                   if line.startswith("ExecStart="))
    assert not re.search(r"%(?![%s])|%(?:$)", command)
    expanded = re.sub(r"%(%|s)", lambda m: "%" if m[1] == "%" else "/bin/bash", command)
    payload = shlex.split(expanded)[2]
    for path in ("/opt/snow-qa", "/run/snow-credentials", "/run/snow-qa.z"):
        payload = payload.replace(path, str(tmp_path / path.lstrip("/")))
    uptime = tmp_path / "uptime"
    uptime.write_text("1234.56 789.00\n")
    payload = payload.replace("/proc/uptime", str(uptime))
    result = subprocess.run(["bash", "-e", "-c", payload], capture_output=True)
    assert result.returncode != 0  # Deliberately broken bootstrap.
    assert (tmp_path / "run/snow-credentials/unit_started").read_text() == "1234.56\n"
    assert (tmp_path / "run/snow-credentials").stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / "opt/snow-qa/qa.py").exists()


def run_extracted_finish(tmp_path, state, serial, *, initialized=False, capture=b'', persist_failure=False,
                         capture_failure=False, keep_vm='false', iso_host=False,
                         detach_failure=False, kept_write_failure=False,
                         persist_failure_target=None, output_write_failure=False,
                         delete_failure=False, iso_cleanup_failure=False,
                         installer_attached=True, work_removal_failure=False,
                         out_unwritable=False, serial_copy_cleanup_failure=False):
    """Run the real shell functions with only Incus and evidence persistence substituted."""
    script = yaml.safe_load(SOURCE.read_text())['data']['run.sh']
    functions = []
    for name in ('capture_serial', 'redact_serial', 'finish'):
        match = re.search(r'(?ms)^' + name + r'\(\) \{\n.*?^\}', script)
        if match:
            functions.append(match.group())
    assert any(part.startswith('finish()') for part in functions)
    work, evidence, out = (tmp_path / part for part in ('work', 'evidence', 'out'))
    for path in (work, evidence, out):
        path.mkdir()
    (out / 'checks.txt').write_text('status=not-verified\n')
    if serial is not None:
        (work / 'serial-full').write_bytes(serial)
    incus = tmp_path / 'incus'
    incus.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$INCUS_CALLS"\n'
                     'if [ "$*" = "config device remove test-vm installer" ]; then '
                     '[ -f "$ISO_HOST" ] && [ "$INSTALLER_ATTACHED" = 1 ] || exit 2; '
                     '[ "$DETACH_FAILURE" != 1 ] || exit 1; fi\n'
                     'if [ "$*" = "delete --force test-vm" ] && '
                     '[ "$DELETE_FAILURE" = 1 ]; then exit 1; fi\n'
                     'if [ "$1" = console ]; then printf "%s" "$CAPTURE"; '
                     '[ "$CAPTURE_FAILURE" != 1 ] || exit 1; fi\n')
    incus.chmod(0o755)
    if work_removal_failure or serial_copy_cleanup_failure:
        rm = tmp_path / 'rm'
        rm.write_text('#!/bin/sh\n'
                      'if [ "$WORK_REMOVAL_FAILURE" = 1 ] && [ "$1" = -rf ] && '
                      '[ "$3" = "$WORK" ]; then /bin/rm "$@"; exit 1; fi\n'
                      'if [ "$SERIAL_COPY_CLEANUP_FAILURE" = 1 ] && [ "$1" = -f ]; then '
                      'case "$3" in "$OUT"/.snow-serial.*) exit 1;; esac; fi\n'
                      'exec /bin/rm "$@"\n')
        rm.chmod(0o755)
    if serial_copy_cleanup_failure:
        cp = tmp_path / 'cp'
        cp.write_text('#!/bin/sh\ncase "$3" in "$OUT"/.snow-serial.*) exit 1;; esac\n'
                      'exec /bin/cp "$@"\n')
        cp.chmod(0o755)
    if iso_host:
        if iso_cleanup_failure:
            (tmp_path / 'installer.iso').mkdir()
        else:
            (tmp_path / 'installer.iso').write_bytes(b'iso')
    if kept_write_failure:
        (work / 'kept-vm.txt').mkdir()
    if output_write_failure or out_unwritable:
        (out / 'result-summary.txt').mkdir()
    if out_unwritable:
        mktemp = tmp_path / 'mktemp'
        mktemp.write_text('#!/bin/sh\ncase "$1" in "$OUT"/*) exit 1;; esac\nexec /usr/bin/mktemp "$@"\n')
        mktemp.chmod(0o755)
    env = {**os.environ, 'PATH': str(tmp_path) + ':' + os.environ['PATH'],
           'CAPTURE': capture.decode(), 'WORK': str(work), 'EVIDENCE': str(evidence),
           'OUT': str(out), 'STATE': state, 'REASON': 'original',
           'INITIALIZED': '1' if initialized else '0', 'VM': 'test-vm',
           'INSTALLER_ATTACHED': '1' if installer_attached else '0',
           'PERSIST_FAILURE': '1' if persist_failure else '0',
           'PERSIST_FAILURE_TARGET': persist_failure_target or '',
           'CAPTURE_FAILURE': '1' if capture_failure else '0',
           'DETACH_FAILURE': '1' if detach_failure else '0',
           'DELETE_FAILURE': '1' if delete_failure else '0',
           'WORK_REMOVAL_FAILURE': '1' if work_removal_failure else '0',
           'SERIAL_COPY_CLEANUP_FAILURE': '1' if serial_copy_cleanup_failure else '0',
           'KEEP_VM_ON_FAILURE': keep_vm, 'INCUS_CALLS': str(tmp_path / 'incus-calls'),
           'ISO_HOST': str(tmp_path / 'installer.iso') if iso_host else ''}
    shell = '\n'.join(functions) + '''
persist() {
  if [[ "$PERSIST_FAILURE" == 1 && "$2" == */serial-redacted.log ]]; then return 1; fi
  if [[ -n "$PERSIST_FAILURE_TARGET" && "$2" == */"$PERSIST_FAILURE_TARGET" ]]; then return 1; fi
  cp -- "$1" "$2"
  chmod 0600 "$2"
}
trap finish EXIT
exit $([[ "$STATE" == PASS ]] && printf 0 || printf 1)
'''
    result = subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', shell], env=env, capture_output=True)
    return result, work, evidence, out


def test_keep_vm_workflow_parameter_defaults_off_and_reaches_runner():
    template = yaml.safe_load((SOURCE.parent / 'run-snow-bootc-lifecycle.yaml').read_text())['spec']
    run = next(item for item in template['templates'] if item['name'] == 'run')
    parameters = {item['name']: item for item in run['inputs']['parameters']}
    assert parameters['keep-vm-on-failure']['value'] == 'false'
    env = {item['name']: item['value'] for item in run['container']['env']}
    assert env['KEEP_VM_ON_FAILURE'] == '{{inputs.parameters.keep-vm-on-failure}}'


@pytest.mark.parametrize(('state', 'keep_vm', 'kept'), [
    ('FAILED', 'true', True), ('BLOCKED', 'true', True),
    ('PASS', 'true', False), ('FAILED', 'false', False),
    ('FAILED', 'TRUE', False), ('FAILED', 'yes', False),
])
def test_finish_keeps_vm_only_on_exact_opt_in_and_nonpass(tmp_path, state, keep_vm, kept):
    result, work, evidence, out = run_extracted_finish(
        tmp_path, state, None, initialized=True, keep_vm=keep_vm, iso_host=True)
    calls = (tmp_path / 'incus-calls').read_text().splitlines()
    assert ('delete --force test-vm' in calls) is not kept
    assert ('config device remove test-vm installer' in calls) is kept
    assert not (tmp_path / 'installer.iso').exists()
    assert not work.exists()
    expected = f'{state}: original' + (';vm_kept=test-vm' if kept else '') + '\n'
    assert (out / 'result-summary.txt').read_text() == expected
    assert (evidence / 'result-summary.txt').read_text() == expected
    if kept:
        assert (evidence / 'kept-vm.txt').read_text() == (
            'vm=test-vm\ncleanup: incus delete --force test-vm (owner: the run submitter)\n')
        assert (evidence / 'kept-vm.txt').stat().st_mode & 0o777 == 0o600
        assert result.returncode != 0
    else:
        assert not (evidence / 'kept-vm.txt').exists()
        assert result.returncode == (0 if state == 'PASS' else 1)


def test_post_install_failure_keeps_vm_without_detaching_absent_installer(tmp_path):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', None, initialized=True, keep_vm='true', iso_host=True,
        installer_attached=False)
    assert result.returncode == 1
    calls = (tmp_path / 'incus-calls').read_text().splitlines()
    assert not any('config device remove' in call or 'delete --force' in call for call in calls)
    assert (out / 'result-summary.txt').read_text() == 'FAILED: original;vm_kept=test-vm\n'
    assert (evidence / 'kept-vm.txt').read_text().startswith('vm=test-vm\n')


def test_result_write_failure_after_pass_persists_redacted_serial(tmp_path):
    raw = b'boot key=' + b'a' * 64 + b'\n'
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'PASS', raw, initialized=True, capture=raw,
        iso_host=True, output_write_failure=True)
    assert result.returncode == 1
    assert (out / 'result-summary.txt').is_dir()
    assert (evidence / 'result-summary.txt').read_text() == 'FAILED: output_write\n'
    assert (evidence / 'serial-redacted.log').read_bytes() == b'boot key=[REDACTED]\n'


def test_evidence_write_failure_after_pass_persists_redacted_serial(tmp_path):
    raw = b'boot key=' + b'a' * 64 + b'\n'
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'PASS', raw, initialized=True, capture=raw,
        iso_host=True, persist_failure_target='checks.txt')
    assert result.returncode == 1
    assert (out / 'result-summary.txt').read_text() == 'FAILED: evidence_write\n'
    assert (evidence / 'serial-redacted.log').read_bytes() == b'boot key=[REDACTED]\n'


@pytest.mark.parametrize('failure', ['output', 'checks.txt'])
def test_write_failure_after_pass_keeps_opted_in_vm(tmp_path, failure):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'PASS', b'boot\n', initialized=True, iso_host=True, keep_vm='true',
        output_write_failure=failure == 'output',
        persist_failure_target=failure if failure != 'output' else None)
    assert result.returncode != 0
    calls = (tmp_path / 'incus-calls').read_text().splitlines()
    assert 'config device remove test-vm installer' in calls
    assert 'delete --force test-vm' not in calls
    assert (evidence / 'kept-vm.txt').read_text().startswith('vm=test-vm\n')
    expected = f'FAILED: {"output_write" if failure == "output" else "evidence_write"};vm_kept=test-vm\n'
    if failure == 'output':
        assert (out / 'result-summary.txt').is_dir()
    else:
        assert (out / 'result-summary.txt').read_text() == expected
    assert (evidence / 'result-summary.txt').read_text() == expected


@pytest.mark.parametrize('keep_vm', ['false', 'true'])
def test_unwritable_out_still_tears_down_or_keeps_and_removes_work(tmp_path, keep_vm):
    result, work, evidence, out = run_extracted_finish(
        tmp_path, 'PASS', b'boot\n', initialized=True, iso_host=True,
        keep_vm=keep_vm, out_unwritable=True)
    assert result.returncode != 0
    assert not work.exists()
    assert not (tmp_path / 'installer.iso').exists()
    calls = (tmp_path / 'incus-calls').read_text().splitlines()
    kept = keep_vm == 'true'
    assert ('delete --force test-vm' in calls) is not kept
    assert ('config device remove test-vm installer' in calls) is kept
    assert (out / 'result-summary.txt').is_dir()
    assert (evidence / 'result-summary.txt').read_text() == (
        'FAILED: output_write' + (';vm_kept=test-vm' if kept else '') + '\n')


def test_failed_serial_copy_cleanup_does_not_skip_work_removal(tmp_path):
    raw = b'boot key=' + b'a' * 64 + b'\n'
    result, work, evidence, out = run_extracted_finish(
        tmp_path, 'PASS', raw, initialized=True, iso_host=True, capture=raw,
        serial_copy_cleanup_failure=True)
    assert result.returncode == 0, result.stderr
    assert not work.exists()
    assert not (tmp_path / 'installer.iso').exists()
    assert 'delete --force test-vm' in (tmp_path / 'incus-calls').read_text()
    assert (out / 'result-summary.txt').read_text() == 'PASS: original\n'
    assert (evidence / 'result-summary.txt').read_text() == 'PASS: original\n'


def test_work_removal_failure_rewrites_pass_and_retains_serial(tmp_path):
    raw = b'boot key=' + b'a' * 64 + b'\n'
    result, work, evidence, out = run_extracted_finish(
        tmp_path, 'PASS', raw, initialized=True, iso_host=True,
        capture=raw, work_removal_failure=True)
    assert result.returncode != 0
    assert not work.exists()
    assert 'delete --force test-vm' in (tmp_path / 'incus-calls').read_text()
    assert (out / 'result-summary.txt').read_text() == 'FAILED: cleanup_failed\n'
    assert (evidence / 'result-summary.txt').read_text() == 'FAILED: cleanup_failed\n'
    assert (evidence / 'serial-redacted.log').read_bytes() == b'boot key=[REDACTED]\n'


def test_partial_line_install_diagnostic_payload_never_survives_serial_evidence(tmp_path):
    payload = base64.b64encode(b'PRIVATE-RECOVERY-KEY')
    raw = b'login: SNOW_INSTALL_DIAG ' + payload + b'\n'
    result, _, evidence, _ = run_extracted_finish(tmp_path, 'FAILED', raw)
    assert result.returncode == 1
    assert (evidence / 'serial-redacted.log').read_bytes() == (
        b'login: SNOW_INSTALL_DIAG [see install-diagnostic.txt]\n')
    assert payload not in (evidence / 'serial-redacted.log').read_bytes()


def test_partial_line_install_diagnostic_is_not_accepted_by_host_decoder(tmp_path):
    script = yaml.safe_load(SOURCE.read_text())['data']['run.sh']
    await_body = script.split('await() {', 1)[1].split('\n}', 1)[0]
    decoder = re.search(r'python3 - "\$WORK/serial" "\$WORK/install-diagnostic.txt" <<\'PY\'\n(.*?)\nPY',
                        await_body, re.S)
    assert decoder
    source = tmp_path / 'serial'
    source.write_bytes(b'login: SNOW_INSTALL_DIAG ' + base64.b64encode(b'PRIVATE-RECOVERY-KEY') + b'\n')
    output = tmp_path / 'install-diagnostic.txt'
    subprocess.run([sys.executable, '-', str(source), str(output)], input=decoder[1], text=True, check=True)
    assert output.read_bytes() == b'unparsed'


def test_keep_vm_detach_failure_deletes_vm_before_iso_removal(tmp_path):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', None, initialized=True, keep_vm='true', iso_host=True,
        detach_failure=True)
    assert result.returncode == 1
    assert (tmp_path / 'incus-calls').read_text().splitlines()[-2:] == [
        'config device remove test-vm installer', 'delete --force test-vm']
    assert not (tmp_path / 'installer.iso').exists()
    assert not (evidence / 'kept-vm.txt').exists()
    assert (out / 'result-summary.txt').read_text() == 'FAILED: original;vm_kept_failed\n'
    assert (evidence / 'result-summary.txt').read_text() == 'FAILED: original;vm_kept_failed\n'


def test_detach_failure_suffix_survives_evidence_write_failure(tmp_path):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', None, initialized=True, keep_vm='true', iso_host=True,
        detach_failure=True, persist_failure_target='checks.txt')
    assert result.returncode == 1
    assert (tmp_path / 'incus-calls').read_text().splitlines()[-1] == 'delete --force test-vm'
    assert not (tmp_path / 'installer.iso').exists()
    assert (out / 'result-summary.txt').read_text() == 'FAILED: evidence_write;vm_kept_failed\n'
    assert (evidence / 'result-summary.txt').read_text() == 'FAILED: evidence_write;vm_kept_failed\n'


def test_detach_and_delete_failure_reports_both_failures(tmp_path):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'BLOCKED', None, initialized=True, keep_vm='true', iso_host=True,
        detach_failure=True, delete_failure=True)
    assert result.returncode != 0
    assert (tmp_path / 'incus-calls').read_text().splitlines()[-2:] == [
        'config device remove test-vm installer', 'delete --force test-vm']
    assert (tmp_path / 'installer.iso').read_bytes() == b'iso'
    assert not (evidence / 'kept-vm.txt').exists()
    expected = 'FAILED: cleanup_after_blocked:original;teardown_failed;vm_kept_failed;vm_left=test-vm\n'
    assert (out / 'result-summary.txt').read_text() == expected
    assert (evidence / 'result-summary.txt').read_text() == expected


@pytest.mark.parametrize('state', ['PASS', 'FAILED'])
def test_delete_failure_keeps_iso_and_identifies_left_vm(tmp_path, state):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, state, None, initialized=True, iso_host=True, delete_failure=True)
    assert result.returncode != 0
    assert (tmp_path / 'incus-calls').read_text().splitlines()[-1] == 'delete --force test-vm'
    assert (tmp_path / 'installer.iso').read_bytes() == b'iso'
    reason = 'teardown_failed' if state == 'PASS' else 'original;teardown_failed'
    expected = f'FAILED: {reason};vm_left=test-vm\n'
    assert (out / 'result-summary.txt').read_text() == expected
    assert (evidence / 'result-summary.txt').read_text() == expected


@pytest.mark.parametrize('detach_failure', [False, True])
@pytest.mark.parametrize('failure', ['checks.txt', 'result-summary.txt', 'output'])
def test_delete_failure_survives_summary_rewrites(tmp_path, detach_failure, failure):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'BLOCKED', None, initialized=True, keep_vm='true' if detach_failure else 'false',
        iso_host=True, detach_failure=detach_failure, delete_failure=True,
        persist_failure_target=failure if failure != 'output' else None,
        output_write_failure=failure == 'output')
    assert result.returncode != 0
    assert (tmp_path / 'installer.iso').read_bytes() == b'iso'
    assert not (evidence / 'kept-vm.txt').exists()
    suffix = ';teardown_failed' + (';vm_kept_failed' if detach_failure else '') + ';vm_left=test-vm\n'
    expected = f'FAILED: {"output_write" if failure == "output" else "evidence_write"}{suffix}'
    if failure != 'output':
        assert (out / 'result-summary.txt').read_text() == expected
    if failure != 'result-summary.txt':
        assert (evidence / 'result-summary.txt').read_text() == expected


@pytest.mark.parametrize('failure', ['checks.txt', 'output'])
def test_iso_cleanup_failure_survives_summary_rewrites(tmp_path, failure):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', None, initialized=True, iso_host=True,
        iso_cleanup_failure=True, persist_failure_target='checks.txt' if failure == 'checks.txt' else None,
        output_write_failure=failure == 'output')
    assert result.returncode != 0
    assert (tmp_path / 'installer.iso').is_dir()
    expected = f'FAILED: {"evidence_write" if failure == "checks.txt" else "output_write"};cleanup_failed\n'
    if failure == 'checks.txt':
        assert (out / 'result-summary.txt').read_text() == expected
    assert (evidence / 'result-summary.txt').read_text() == expected


def test_keep_vm_write_failure_still_removes_iso_and_reports_vm_name(tmp_path):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', None, initialized=True, keep_vm='true', iso_host=True,
        kept_write_failure=True)
    assert result.returncode == 1
    assert not (tmp_path / 'installer.iso').exists()
    assert not (evidence / 'kept-vm.txt').exists()
    assert (tmp_path / 'incus-calls').read_text().splitlines()[-1] == 'config device remove test-vm installer'
    assert (out / 'result-summary.txt').read_text() == 'FAILED: original;vm_kept=test-vm\n'
    assert (evidence / 'result-summary.txt').read_text() == 'FAILED: original;vm_kept=test-vm\n'


@pytest.mark.parametrize('target', ['checks.txt', 'result-summary.txt'])
def test_kept_vm_name_survives_evidence_write_failure(tmp_path, target):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', None, initialized=True, keep_vm='true', iso_host=True,
        persist_failure_target=target)
    assert result.returncode == 1
    assert 'delete --force test-vm' not in (tmp_path / 'incus-calls').read_text()
    assert not (tmp_path / 'installer.iso').exists()
    assert (out / 'result-summary.txt').read_text() == 'FAILED: evidence_write;vm_kept=test-vm\n'
    if target == 'checks.txt':
        assert (evidence / 'result-summary.txt').read_text() == 'FAILED: evidence_write;vm_kept=test-vm\n'
    else:
        assert not (evidence / 'result-summary.txt').exists()


def test_kept_vm_name_survives_result_output_write_failure(tmp_path):
    result, _, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', None, initialized=True, keep_vm='true', iso_host=True,
        output_write_failure=True)
    assert result.returncode == 1
    assert (out / 'result-summary.txt').is_dir()
    assert (evidence / 'result-summary.txt').read_text() == 'FAILED: output_write;vm_kept=test-vm\n'
    assert 'delete --force test-vm' not in (tmp_path / 'incus-calls').read_text()


def test_finish_persists_redacted_latest_serial_before_work_removal(tmp_path):
    secret = b'a' * 64
    recovery = b'c' * 8 + (b'-' + b'd' * 8) * 7
    raw = b'boot ' + secret + b' ' + recovery + b'\x1b[0m SNOW_QA_ERR x\r\r\n'
    result, work, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', raw, initialized=True, capture=raw + b'last line\r\n')
    assert result.returncode == 1, result.stderr
    assert not work.exists()
    persisted = evidence / 'serial-redacted.log'
    assert persisted.read_bytes() == b'boot [REDACTED] [REDACTED] SNOW_QA_ERR x\nlast line\n'
    assert persisted.stat().st_mode & 0o777 == 0o600
    assert (out / 'result-summary.txt').read_text() == 'FAILED: original\n'
    assert (evidence / 'result-summary.txt').read_text() == 'FAILED: original\n'
    assert not result.stdout and not result.stderr


def test_finish_strips_controls_before_redacting_embedded_secret(tmp_path):
    secret = b'a' * 64
    raw = b'key=' + secret[:30] + b'\x1b[0m' + secret[30:] + b'\n'
    result, _, evidence, _ = run_extracted_finish(tmp_path, 'FAILED', raw)
    assert result.returncode == 1
    assert (evidence / 'serial-redacted.log').read_bytes() == b'key=[REDACTED]\n'


@pytest.mark.parametrize('control', [b'\x00', b'\x07'])
def test_finish_redacts_hex_interrupted_by_control(tmp_path, control):
    secret = b'1' * 64
    result, _, evidence, _ = run_extracted_finish(
        tmp_path, 'FAILED', b'key=' + secret[:30] + control + secret[30:] + b'\n')
    assert result.returncode == 1
    assert (evidence / 'serial-redacted.log').read_bytes() == b'key=[REDACTED]\n'


def test_finish_elides_every_install_diagnostic_payload(tmp_path):
    secret = b'PRIVATE-RECOVERY-KEY'
    payload = base64.b64encode(secret)
    raw = (b'boot\nSNOW_INSTALL_DIAG ' + payload + b'\r\n'
           + b'SNOW_INSTALL_DIAG ' + payload + b'\nlast line\n')
    result, _, evidence, _ = run_extracted_finish(tmp_path, 'FAILED', raw)
    assert result.returncode == 1
    serial = (evidence / 'serial-redacted.log').read_bytes()
    assert serial == (b'boot\nSNOW_INSTALL_DIAG [see install-diagnostic.txt]\n'
                      b'SNOW_INSTALL_DIAG [see install-diagnostic.txt]\nlast line\n')
    assert payload not in serial and secret not in serial


def test_finish_retains_previous_snapshot_when_final_capture_fails(tmp_path):
    result, work, evidence, out = run_extracted_finish(
        tmp_path, 'BLOCKED', b'previous line\n', initialized=True,
        capture=b'partial secret', capture_failure=True)
    assert result.returncode == 1
    assert not work.exists()
    assert (evidence / 'serial-redacted.log').read_bytes() == b'previous line\n'
    assert (out / 'result-summary.txt').read_text() == 'BLOCKED: original\n'


def test_finish_redaction_oversize_is_best_effort(tmp_path):
    result, work, evidence, out = run_extracted_finish(
        tmp_path, 'FAILED', b'x' * 1048577)
    assert result.returncode == 1
    assert not work.exists()
    assert not (evidence / 'serial-redacted.log').exists()
    assert (out / 'result-summary.txt').read_text() == 'FAILED: original\n'


@pytest.mark.parametrize(('state', 'serial', 'initialized', 'persist_failure'), [
    ('PASS', b'secret', False, False),
    ('BLOCKED', None, False, False),
    ('FAILED', b'boot message\n', False, True),
])
def test_finish_skips_or_tolerates_serial_evidence(tmp_path, state, serial, initialized, persist_failure):
    result, work, evidence, out = run_extracted_finish(
        tmp_path, state, serial, initialized=initialized, persist_failure=persist_failure)
    assert result.returncode == (0 if state == 'PASS' else 1)
    assert not work.exists()
    assert not (evidence / 'serial-redacted.log').exists()
    expected = f'{state}: original\n'
    assert (out / 'result-summary.txt').read_text() == expected
    assert (evidence / 'result-summary.txt').read_text() == expected


@pytest.mark.parametrize("start", [None, "garbage", "nan", "inf", "1e3", "1001.00", "9" * 80])
def test_guest_rejects_bad_unit_start_before_actions(qa, tmp_path, monkeypatch, start):
    m = manifest()
    normalized = tmp_path / "normalized.json"
    normalized.write_bytes(qa.canonical(m))
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized)
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "phase").write_text("stage")
    (creds / "nonce").write_text(NONCE)
    for key in ("image_n", "image_n_plus_1", "target_ref"):
        (creds / key).write_text(m[key])
    if start is not None:
        (creds / "unit_started").write_text(start)
    (tmp_path / "uptime").write_text("1000.00 50.00\n")
    monkeypatch.setattr(qa, "GUEST_PATHS", {"uptime": tmp_path / "uptime"})
    monkeypatch.setattr(qa.subprocess, "run", lambda *a, **kw: pytest.fail("action before unit-start validation"))
    with pytest.raises(qa.GateError, match="^unit_start$"):
        qa.guest(creds)


def test_run_install_unit_uses_manifest_install_budget_without_systemd_specifiers():
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assignment = re.search(r'(?m)^unit="\[Unit\].*?^drop=', script, re.S)
    assert assignment
    m = manifest()
    unit = subprocess.run(["bash", "-c", assignment.group().rsplit("drop=", 1)[0] +
                           '\nprintf "%s" "$unit"'],
                          env={**os.environ, "install_timeout": str(m["timeouts"]["install"]), "install_b64": "YQ=="},
                          capture_output=True, check=True, text=True).stdout
    assert unit.splitlines().count(f'TimeoutStartSec={m["timeouts"]["install"]}') == 1
    assert "%" not in unit.replace("%%", "")


@pytest.mark.parametrize(("scenario", "marker", "installed"), [
    ("pass", "SNOW_INSTALL_OK", True),
    ("v2", "SNOW_INSTALL_FAILED firn_v2", False),
    ("hash", "SNOW_INSTALL_FAILED firn_hash", False),
    ("disk_detect", "SNOW_INSTALL_FAILED disk_detect", False),
    ("disk_byid", "SNOW_INSTALL_FAILED disk_byid", False),
    ("validate_secret", "SNOW_INSTALL_FAILED firn_validate:secret-file", False),
    ("validate_unknown", "SNOW_INSTALL_FAILED firn_validate:unclassified", False),
    ("validate_final_token", "SNOW_INSTALL_FAILED firn_validate:unclassified", False),
    ("validate_skip_unknown", "SNOW_INSTALL_FAILED firn_validate:secret-file", False),
])
def test_installer_vm_checks_firn_before_install(tmp_path, scenario, marker, installed):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    guest = re.search(r"(?ms)^cat > \"\$WORK/install.sh\" <<'GUEST'\n(.*?)^GUEST$", script)
    replacement = re.search(r"(?ms)^python3 - \"\$WORK/install.sh\".*?^PY$", script[guest.end():]) if guest else None
    assert guest and replacement
    work = tmp_path / "work"
    work.mkdir()
    (work / "install.sh").write_text(guest[1])
    expected_hash = hashlib.sha256(b"firn-in-iso").hexdigest()
    env = {**os.environ, "WORK": str(work), "n": f"{REPO}@sha256:{N}",
           "target": f"{REPO}:qa-controlled", "firn_sha256": expected_hash,
           "PATH": str(tmp_path / "bin") + ":" + os.environ["PATH"],
           "SCENARIO": scenario, "TEST_ROOT": str(tmp_path)}
    subprocess.run(["bash", "-e", "-c", replacement.group()], env=env, check=True)
    guest_text = (work / "install.sh").read_text()
    assert "@N@" not in guest_text and "@TARGET@" not in guest_text and "@FIRN_SHA256@" not in guest_text
    # Isolate guest paths and the disk discovery from the test host; run the
    # actual validator/installation section with stub executables in PATH.
    guest_text = guest_text.replace("/run/", str(tmp_path / "run") + "/")
    guest_text = guest_text.replace("/dev/ttyS0", str(tmp_path / "serial"))
    if scenario in ("disk_detect", "disk_byid"):
        guest_text = re.sub(r"mapfile -t disks < <\(lsblk .*?\)",
                            'disks=()' if scenario == "disk_detect" else 'disks=(/dev/example-disk)', guest_text)
        guest_text = guest_text.replace('for item in /dev/disk/by-id/*; do',
                                        'for item in "$TEST_ROOT/no-byid/"*; do')
    else:
        guest_text = re.sub(r"mapfile -t disks .*?\[\[ -n \"\$byid\" && -b \"\$byid\" \]\] \|\| (?:exit 1|\{.*?exit 1; \})",
                            'disk="/dev/example-disk"; byid="/dev/disk/by-id/example"', guest_text, flags=re.S)
        assert 'byid="/dev/disk/by-id/example"' in guest_text
    (tmp_path / "run").mkdir()
    (tmp_path / "bin").mkdir()
    firn = tmp_path / "bin/firn"
    firn.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$TEST_ROOT/calls\"\n"
                    "if [[ $1 == validate ]]; then\n"
                    "  if [[ $2 == */snow-recipe.toml ]]; then\n"
                    "    case $SCENARIO in\n"
                    "      validate_secret) printf 'security.mok_password_file: /run/x (secret-file)\\nrecipe is invalid (1 issue(s))\\n' >&2; exit 1;;\n"
                    "      validate_unknown) printf 'private reason (future-issue)\\n' >&2; exit 1;;\n"
                    "      validate_final_token) printf 'private reason (secret-file) extra\\nprivate reason (secret-file) (future-issue)\\n' >&2; exit 1;;\n"
                    "      validate_skip_unknown) printf 'private (future-issue)\\nprivate (secret-file)\\nprivate (disk)\\n' >&2; exit 1;;\n"
                    "    esac\n"
                    "    exit 0\n"
                    "  fi\n"
                    "  [[ $SCENARIO == v2 ]] && { printf 'bad-version\\n' >&2; exit 1; }\n"
                    "  exit 0\n"
                    "fi\nexit 0\n")
    firn.chmod(0o755)
    sums = tmp_path / "bin/sha256sum"
    sums.write_text("#!/bin/bash\nif [[ $SCENARIO == hash ]]; then printf '%064d  %s\\n' 0 \"$1\"; "
                    "else printf '%s  %s\\n' \"$firn_sha256\" \"$1\"; fi\n")
    sums.chmod(0o755)
    (tmp_path / "bin/openssl").write_text("#!/bin/sh\nprintf '0123456789abcdef\\n'\n")
    (tmp_path / "bin/openssl").chmod(0o755)
    guest_text = guest_text.replace("/usr/bin/firn", str(firn))
    if scenario == "validate_secret":
        # The install VM does not require python3 for pre-install diagnostics.
        env["PATH"] = str(tmp_path / "bin")
        for name in ("bash", "grep", "sed", "cat", "chmod"):
            (tmp_path / "bin" / name).symlink_to(shutil.which(name))
    result = subprocess.run(["bash", "-e", "-c", guest_text], env=env, capture_output=True, timeout=10)
    assert result.returncode == (0 if installed else 1), result.stderr
    assert (tmp_path / "serial").read_text().splitlines()[-1] == marker
    if scenario == "pass":
        recipe = (tmp_path / "run/snow-recipe.toml").read_text()
        assert recipe.startswith("version = 2\n")
        assert '[system]\nhostname = "snow-qa"' in recipe
    if scenario != "hash":
        validator = (tmp_path / "run/snow-firn-v2.toml").read_text()
        assert all(field in validator for field in ('version = 2', 'disk = "/dev/example-disk"',
                                                    'filesystem = "ext4"', 'encryption = "none"',
                                                    'hostname = "snow-qa"'))
        assert not (tmp_path / "run/snow-firn-v1.toml").exists()
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    assert ("install" in [line.split()[0] for line in calls]) == installed
    if scenario != "hash":
        assert calls[:1] == [f"validate --secure-boot off --tpm off {tmp_path}/run/snow-firn-v2.toml"]
    if scenario in ("pass", "validate_secret", "validate_unknown", "validate_final_token", "validate_skip_unknown"):
        assert calls[1] == f"validate {tmp_path}/run/snow-recipe.toml --secure-boot on --tpm on"
        assert "--uefi" not in calls[1]
        assert (tmp_path / "run/snow-validate.log").stat().st_mode & 0o777 == 0o600
        if scenario == "pass":
            assert calls[2].startswith("install ")
        else:
            assert len(calls) == 2
        assert b"private reason" not in (tmp_path / "serial").read_bytes() + result.stdout + result.stderr
        assert b"security.mok_password_file" not in (tmp_path / "serial").read_bytes() + result.stdout + result.stderr


def test_installer_recipes_speak_firn_schema_version_2_only():
    # Firn 0.9.0 uses schema version 2; version 1 is deprecated and is rejected
    # from 2026-12-30 on, so both the validator and the install recipe say 2.
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    guest = re.search(r"(?ms)^cat > \"\$WORK/install.sh\" <<'GUEST'\n(.*?)^GUEST$", script)
    assert guest
    body = guest[1]
    validator = re.search(r"(?ms)^cat > /run/snow-firn-v2\.toml <<'VALIDATOR'\n(.*?)^VALIDATOR$", body)
    recipe = re.search(r"(?ms)^cat > /run/snow-recipe\.toml <<RECIPE\n(.*?)^RECIPE$", body)
    assert validator and recipe
    assert validator[1].splitlines()[0] == "version = 2"
    assert recipe[1].splitlines()[0] == "version = 2"
    assert not re.search(r"(?m)^\s*version = 1$", body)
    assert "snow-firn-v1.toml" not in script
    assert "SNOW_INSTALL_FAILED firn_v1" not in script


INSTALL_FAILURE_CASES = [
    ('{"event":"start","protocol":1,"firn":"76518f0","steps":[{"name":"partition","weight":1}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:partition:step_failed", "firn_install:partition:step_failed"),
    ('{"event":"start","protocol":1,"firn":"x","steps":[{"name":"partition","weight":1},{"name":"install","weight":1}]}\n'
     '{"event":"step_start","name":"partition"}\n'
     '{"event":"step_start","name":"install"}\n'
     '{"event":"error","step":"install","code":"step_failed","message":"m"}\n',
     "firn_install:install:step_failed", "firn_install:install:step_failed"),
    ('{"event":"start","protocol":1,"firn":"x","steps":[{"name":"partition","weight":1},{"name":"install","weight":1}]}\n'
     '{"event":"step_start","name":"partition"}\n'
     '{"event":"step_start","name":"install"}\n'
     '{"event":"error","step":"other","code":"step_failed","message":"m"}\n',
     "firn_install:install:step_failed", "firn_install:install:step_failed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"step_start","name":"other"}\n'
     '{"event":"error","step":"other","code":"step_failed","message":"m"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"step_start","step":"partition"}\n'
     '{"event":"error","step":"other","code":"step_failed","message":"m"}\n',
     "firn_install:partition:step_failed", "firn_install:partition:step_failed"),
    ('', "firn_install:unknown:empty_stream", "firn_install:unknown:empty_stream"),
    ('{"event":"done"}\n', "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    ('{"event":"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:step_failed"),
    ('{"event":"start","protocol":1,"firn":"76518f0","steps":[]}\n'
     '{"event":"recovery_key","key":"SECRET-RECOVERY"}\n'
     '{"event":"error","step":"","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:run:step_failed", "firn_install:run:step_failed"),
    ('{"event":"start","steps":[]}\x00\n'
     '{"event":"error","step":"","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:step_failed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"step_start","step":"partition"}\n'
     '{"event":"recovery_key","key":"SECRET-RECOVERY"}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:partition:step_failed", "firn_install:partition:step_failed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"other","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"secret_code","message":"SECRET-MESSAGE"}\n',
     "firn_install:partition:unlisted", "firn_install:unknown:unparsed"),
    ('{"event": "start", "steps": [{"name": "partition"}]}\n'
     '{"event": "error", "step": "", "code": "no_tpm", "message": "SECRET-MESSAGE"}\n',
     # Firn's empty step means a synthetic run-level failure, not an unlisted step.
     "firn_install:run:no_tpm", "firn_install:unknown:unparsed"),
    ('{"event":\t"start","steps":[{"name":"partition"}]}\n'
     '{"event":\t"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:partition:step_failed", "firn_install:unknown:unparsed"),
    ('{"event":"start","steps":[{"meta":{"name":"bogus"}}]}\n'
     '{"event":"step_start","name":"bogus"}\n'
     '{"event":"error","step":"bogus","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
    ('{"event":"start","steps":[{"name":"partition","meta":{"children":[{"name":"bogus"}]}}]}\n'
     '{"event":"step_start","name":"bogus"}\n'
     '{"event":"error","step":"bogus","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
    ('{"event":"start","meta":{"steps":[{"name":"bogus"}]},"steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"bogus","code":"step_failed","message":"m"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
    ('{"event":"start","steps":[{"name":"bogus"}],"steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"bogus","code":"step_failed","message":"m"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
    ('{"event":"start","steps":[{"name":"bogus","name":"partition"}]}\n'
     '{"event":"error","step":"bogus","code":"step_failed","message":"m"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
    ('{"event":"start","event":"done","steps":[{"name":"bogus"}]}\n'
     '{"event":"error","step":"bogus","code":"step_failed","message":"m"}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:step_failed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"}',
     "firn_install:unknown:stream_truncated", "firn_install:unknown:stream_truncated"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":not-json}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"}garbage\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"escaped \\"quote\\""}\n',
     "firn_install:partition:step_failed", "firn_install:partition:step_failed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"bad \\q"}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"bad \t tab"}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"bad \t, tab"}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"bad \x00 nul"}\n',
     "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
    pytest.param('{"event":"error","step":"","code":"step_failed","message":"\x00' + 'x' * 60000 + '"}\n',
                 "firn_install:unknown:unparsed", "firn_install:unknown:unparsed", id="early-nul-long-terminal"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"escaped \\n and \\u00e9"}\n',
     "firn_install:partition:step_failed", "firn_install:partition:step_failed"),
    pytest.param('{"event":"start","steps":[{"name":"partition"}]}\n'
                 '{"event":"error","step":"partition","code":"step_failed","message":"' + 'x' * 65536 + '"}\n',
                 "firn_install:partition:step_failed", "firn_install:unknown:unparsed", id="oversize-terminal-line"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"step_start","step":"partition"}\n',
     "firn_install:unknown:stream_truncated", "firn_install:unknown:stream_truncated"),
    ('not json\n', "firn_install:unknown:unparsed", "firn_install:unknown:stream_truncated"),
]
def run_installer_failure(tmp_path, stream, without_python, stderr='', base64_available=True,
                          emit_credentials=False):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    guest = re.search(r"(?ms)^cat > \"\$WORK/install.sh\" <<'GUEST'\n(.*?)^GUEST$", script)
    replacement = re.search(r"(?ms)^python3 - \"\$WORK/install.sh\".*?^PY$", script[guest.end():]) if guest else None
    assert guest and replacement
    work = tmp_path / "work"
    work.mkdir()
    (work / "install.sh").write_text(guest[1])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "run").mkdir()
    env = {**os.environ, "WORK": str(work), "n": f"{REPO}@sha256:{N}",
           "target": f"{REPO}:qa-controlled", "firn_sha256": "f" * 64,
           "PATH": str(bin_dir) + ":" + os.environ["PATH"], "TEST_ROOT": str(tmp_path),
           "EMIT_CREDENTIALS": "1" if emit_credentials else "0"}
    subprocess.run(["bash", "-e", "-c", replacement.group()], env=env, check=True)
    guest_text = (work / "install.sh").read_text()
    guest_text = guest_text.replace("/run/", str(tmp_path / "run") + "/")
    guest_text = guest_text.replace("/dev/ttyS0", str(tmp_path / "serial"))
    guest_text = re.sub(r"mapfile -t disks .*?\[\[ -n \"\$byid\" && -b \"\$byid\" \]\] \|\| (?:exit 1|\{.*?exit 1; \})",
                        'disk="/dev/example-disk"; byid="/dev/disk/by-id/example"', guest_text, flags=re.S)
    assert 'byid="/dev/disk/by-id/example"' in guest_text
    firn = bin_dir / "firn"
    firn.write_text('#!/bin/bash\nif [[ $1 == validate ]]; then\n'
                    '  if [[ $2 == */snow-recipe.toml ]] || grep -q "version = 2" "${@: -1}"; then exit 0; fi\n'
                    '  printf "bad-version\\n" >&2; exit 1\nfi\n'
                    '[[ $1 == install && " $* " == *" --json-progress "* ]] || exit 3\n'
                    'cp "$TEST_ROOT/stream" /dev/stdout\n'
                    'if [[ $EMIT_CREDENTIALS == 1 ]]; then cat "$TEST_ROOT/run/snow-passphrase" "$TEST_ROOT/run/snow-mok-password" >&2; fi\n'
                    'cat "$TEST_ROOT/stderr" >&2\nexit 1\n')
    firn.chmod(0o755)
    (tmp_path / "stream").write_text(stream)
    (tmp_path / "stderr").write_bytes(stderr if isinstance(stderr, bytes) else stderr.encode())
    sums = bin_dir / "sha256sum"
    sums.write_text('#!/bin/bash\nprintf "%s  %s\\n" "$firn_sha256" "$1"\n')
    sums.chmod(0o755)
    openssl = bin_dir / "openssl"
    openssl.write_text('#!/bin/sh\nif [ "$3" = 32 ]; then printf "%064d\\n" 1; else printf "%032d\\n" 2; fi\n')
    openssl.chmod(0o755)
    guest_text = guest_text.replace("/usr/bin/firn", str(firn))
    if without_python:
        # A command lookup must genuinely fail while bash, grep, sed etc. remain usable.
        env["PATH"] = str(bin_dir)
        for name in ("bash", "grep", "sed", "cp", "chmod", "cat", "head", "tail", "wc", "tr", "base64"):
            if name == "base64" and not base64_available:
                continue
            binary = shutil.which(name)
            if binary:
                (bin_dir / name).symlink_to(binary)
    result = subprocess.run(["/bin/bash", "-e", "-c", guest_text], env=env, capture_output=True, timeout=10)
    assert result.returncode == 1, result.stderr
    return (tmp_path / "serial").read_bytes(), result


def diagnostic(serial):
    lines = [line for line in serial.splitlines() if line.startswith(b"SNOW_INSTALL_DIAG ")]
    assert len(lines) == 1
    assert serial.splitlines()[-2] == lines[0]
    payload = lines[0].split(b" ", 1)[1]
    return b'unavailable' if payload == b'unavailable' else base64.b64decode(payload, validate=True)


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_redacts_recovery_key_in_message(tmp_path, without_python):
    key = 'cbdefghi-jklnrtuv-' * 3 + 'cbdefghi-jklnrtuv'
    stream = ('{"event":"start","steps":[{"name":"partition"}]}\n'
              + json.dumps({"event": "recovery_key", "key": key}, separators=(',', ':')) + '\n'
              + json.dumps({"event": "error", "step": "partition", "code": "step_failed",
                            "message": "mkfs failed " + key}, separators=(',', ':')) + '\n')
    serial, result = run_installer_failure(tmp_path, stream, without_python)
    decoded = diagnostic(serial)
    assert b'mkfs failed' in decoded and b'[REDACTED]' in decoded
    assert key.encode() not in serial + decoded + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_noncompact_recovery_event_does_not_leak(tmp_path, without_python):
    key = 'SECRET-RECOVERY-UNUSUAL'
    stream = ('{"event":"start","steps":[{"name":"partition"}]}\n'
              + json.dumps({"event": "recovery_key", "key": key}) + '\n'
              + json.dumps({"event": "error", "step": "partition", "code": "step_failed",
                            "message": "mkfs failed " + key}, separators=(',', ':')) + '\n')
    serial, result = run_installer_failure(tmp_path, stream, without_python, key + '\ncryptsetup: boom\n')
    assert serial.splitlines()[-1] == b'SNOW_INSTALL_FAILED firn_install:partition:step_failed'
    if without_python:
        assert serial.splitlines()[-2] == b'SNOW_INSTALL_DIAG unavailable'
    else:
        decoded = diagnostic(serial)
        assert b'mkfs failed' in decoded and b'cryptsetup: boom' in decoded
        assert b'[REDACTED]' in decoded
        assert key.encode() not in decoded
    assert key.encode() not in serial + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_redacts_every_duplicate_recovery_key(tmp_path, without_python):
    first, second = 'FIRST-PRIVATE-KEY', 'SECOND-PRIVATE-KEY'
    stream = ('{"event":"start","steps":[{"name":"partition"}]}\n'
              f'{{"event":"recovery_key","key":"{first}","key":"{second}"}}\n'
              f'{{"event":"error","step":"partition","code":"step_failed","message":"mkfs failed {first} {second}"}}\n')
    serial, result = run_installer_failure(tmp_path, stream, without_python,
                                            f'{first} {second}\ncryptsetup: boom\n')
    decoded = diagnostic(serial)
    assert b'mkfs failed' in decoded and b'cryptsetup: boom' in decoded
    assert decoded.count(b'[REDACTED]') >= 2
    for secret in (first, second):
        assert secret.encode() not in serial + decoded + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_escaped_event_name_duplicate_keys_unavailable(tmp_path, without_python):
    first, second = 'FIRST-PRIVATE-KEY', 'SECOND-PRIVATE-KEY'
    stream = ('{"event":"start","steps":[{"name":"partition"}]}\n'
              f'{{"event":"\\u0072ecovery_key","key":"{first}","key":"{second}"}}\n'
              f'{{"event":"error","step":"partition","code":"step_failed","message":"mkfs failed {first} {second}"}}\n')
    serial, result = run_installer_failure(tmp_path, stream, without_python, f'{first} {second}\n')
    assert diagnostic(serial) == b'unavailable'
    for secret in (first, second):
        assert secret.encode() not in serial + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_redacts_key_on_non_recovery_event(tmp_path, without_python):
    secret = 'UNRELATED-PRIVATE-KEY'
    stream = ('{"event":"start","steps":[{"name":"partition"}]}\n'
              f'{{"event":"progress","key":"{secret}"}}\n'
              f'{{"event":"error","step":"partition","code":"step_failed","message":"mkfs failed {secret}"}}\n')
    serial, result = run_installer_failure(tmp_path, stream, without_python, f'{secret}\ncryptsetup: boom\n')
    decoded = diagnostic(serial)
    assert b'mkfs failed' in decoded and b'cryptsetup: boom' in decoded and b'[REDACTED]' in decoded
    assert secret.encode() not in serial + decoded + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
@pytest.mark.parametrize("partial", ["key_event", "terminal"])
def test_diag_unavailable_on_unterminated_progress(tmp_path, without_python, partial):
    secret = 'UNTERMINATED-PRIVATE-KEY'
    start = '{"event":"start","steps":[{"name":"partition"}]}\n'
    error = '{"event":"error","step":"partition","code":"step_failed","message":"mkfs failed"}'
    if partial == "key_event":
        stream = start + error + '\n' + f'{{"event":"recovery_key","key":"{secret}"}}'
    else:
        stream = start + f'{{"event":"recovery_key","key":"{secret}"}}\n' + error
    serial, result = run_installer_failure(tmp_path, stream, without_python,
                                            f'cryptsetup: boom {secret}\n')
    assert diagnostic(serial) == b'unavailable'
    assert secret.encode() not in serial + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_unavailable_on_escaped_recovery_key(tmp_path, without_python):
    stream = ('{"event":"start","steps":[]}\n'
              '{"event":"recovery_key","key":"PRIVATE\\u002dKEY"}\n'
              '{"event":"error","step":"","code":"step_failed","message":"disk failed"}\n')
    serial, _ = run_installer_failure(tmp_path, stream, without_python, 'cryptsetup: boom\n')
    assert serial.splitlines()[-2] == b'SNOW_INSTALL_DIAG unavailable'


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_drops_unterminated_log_secret_prefix(tmp_path, without_python):
    stream = '{"event":"start","steps":[]}\n{"event":"error","step":"","code":"step_failed","message":"disk failure"}\n'
    prefix = '0' * 19 + '1'  # Not long enough for the hex-pattern redaction.
    serial, result = run_installer_failure(tmp_path, stream, without_python,
                                            'cryptsetup: boom\n' + prefix)
    decoded = diagnostic(serial)
    assert b'cryptsetup: boom' in decoded
    assert prefix.encode() not in serial + decoded + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_redacts_passphrase_in_stderr(tmp_path, without_python):
    stream = '{"event":"start","steps":[]}\n{"event":"error","step":"","code":"step_failed","message":"disk failure"}\n'
    # Stub openssl emits a 64-digit passphrase and a 32-digit MOK password.
    secret = '0' * 63 + '1'
    mok = '0' * 31 + '2'
    serial, result = run_installer_failure(tmp_path, stream, without_python,
                                            'cryptsetup: boom\n', emit_credentials=True)
    decoded = diagnostic(serial)
    assert b'cryptsetup: boom' in decoded
    for value in (secret, mok):
        assert value.encode() not in serial + decoded + result.stdout + result.stderr


@pytest.mark.parametrize('without_python', [False, True], ids=['python', 'shell-fallback'])
def test_guest_diag_redacts_hex_interrupted_by_control(tmp_path, without_python):
    stream = '{"event":"start","steps":[]}\n{"event":"error","step":"","code":"step_failed","message":"disk failure"}\n'
    secret = b'1' * 64
    serial, result = run_installer_failure(tmp_path, stream, without_python,
                                           b'key=' + secret[:30] + b'\x07' + secret[30:] + b'\n')
    assert b'key=[REDACTED]' in diagnostic(serial)
    assert secret not in serial + result.stdout + result.stderr


@pytest.mark.parametrize('without_python', [False, True], ids=['python', 'shell-fallback'])
@pytest.mark.parametrize('control', [b'\x07', b'\x00', b'\r'])
def test_guest_diag_redacts_literal_recovery_key_interrupted_by_control(tmp_path, without_python, control):
    secret = b'PRIVATE-RECOVERY-KEY'
    stream = ('{"event":"start","steps":[]}\n'
              '{"event":"recovery_key","key":"PRIVATE-RECOVERY-KEY"}\n'
              '{"event":"error","step":"","code":"step_failed","message":"disk failure"}\n')
    serial, result = run_installer_failure(tmp_path, stream, without_python,
                                           b'key=' + secret[:10] + control + secret[10:] + b'\n')
    decoded = diagnostic(serial)
    assert b'key=[REDACTED]\n' in decoded
    assert secret not in serial + decoded + result.stdout + result.stderr


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
def test_diag_bounded(tmp_path, without_python):
    stream = '{"event":"start","steps":[]}\n{"event":"error","step":"","code":"step_failed","message":"failure"}\n'
    serial, _ = run_installer_failure(tmp_path, stream, without_python, 'A' * 100000 + '\nEND-OF-LOG\n')
    decoded = diagnostic(serial)
    assert len(decoded) <= 4096 and decoded.endswith(b'END-OF-LOG\n')


def test_diag_unavailable_without_base64(tmp_path):
    stream = '{"event":"start","steps":[]}\n{"event":"error","step":"","code":"step_failed","message":"failure"}\n'
    serial, _ = run_installer_failure(tmp_path, stream, True, base64_available=False)
    assert serial.splitlines()[-2:] == [b'SNOW_INSTALL_DIAG unavailable', b'SNOW_INSTALL_FAILED firn_install:run:step_failed']


@pytest.mark.parametrize(('payload', 'expected'), [
    (b'SNOW_INSTALL_DIAG ' + base64.b64encode(b'mkfs failed ' + b'a' * 64) + b'\r\n', b'mkfs failed [REDACTED]'),
    (b'SNOW_INSTALL_DIAG ' + base64.b64encode(b'key=' + b'1' * 30 + b'\x00' + b'1' * 34 + b'\n') + b'\n', b'key=[REDACTED]\n'),
    (b'SNOW_INSTALL_DIAG unavailable\n', b'unavailable'),
    (b'SNOW_INSTALL_DIAG YQ==\nSNOW_INSTALL_DIAG Yg==\n', b'unparsed'),
    (b'SNOW_INSTALL_DIAG abc\n', b'unparsed'),
    (b'SNOW_INSTALL_DIAG ' + base64.b64encode(b'z' * 4097) + b'\n', b'unparsed'),
])
def test_host_install_diagnostic_decoder(tmp_path, payload, expected):
    script = yaml.safe_load(SOURCE.read_text())['data']['run.sh']
    await_body = script.split('await() {', 1)[1].split('\n}', 1)[0]
    match = re.search(r'python3 - "\$WORK/serial" "\$WORK/install-diagnostic.txt" <<\'PY\'\n(.*?)\nPY', await_body, re.S)
    assert match
    serial = tmp_path / 'serial'
    serial.write_bytes(payload + b'SNOW_INSTALL_FAILED firn_install:partition:step_failed\n')
    out = tmp_path / 'install-diagnostic.txt'
    subprocess.run([sys.executable, '-', str(serial), str(out)], input=match[1], text=True, check=True)
    assert out.read_bytes() == expected
    assert out.stat().st_mode & 0o777 == 0o600
    assert 'persist "$WORK/install-diagnostic.txt" "$EVIDENCE/install-diagnostic.txt" || true' in await_body
    assert 'fail "install_failed:$code"' in await_body


def test_host_serial_and_install_diagnostic_share_redaction_contract(tmp_path):
    # Both independent host paths must apply the same control/ANSI cleanup and
    # secret patterns, including a credential split by a control byte.
    secret = b'1' * 64
    recovery = b'c' * 8 + (b'-' + b'd' * 8) * 7
    raw = (b'key=' + secret[:30] + b'\x00' + secret[30:] + b' ' + recovery
           + b'\x1b[0m\r\r\n')
    (tmp_path / 'finish').mkdir()
    result, _, evidence, _ = run_extracted_finish(tmp_path / 'finish', 'FAILED', raw)
    assert result.returncode == 1
    serial_result = (evidence / 'serial-redacted.log').read_bytes()

    script = yaml.safe_load(SOURCE.read_text())['data']['run.sh']
    await_body = script.split('await() {', 1)[1].split('\n}', 1)[0]
    decoder = re.search(r'python3 - "\$WORK/serial" "\$WORK/install-diagnostic.txt" <<\'PY\'\n(.*?)\nPY',
                        await_body, re.S)
    assert decoder
    source = tmp_path / 'diag-serial'
    source.write_bytes(b'SNOW_INSTALL_DIAG ' + base64.b64encode(raw) + b'\n')
    output = tmp_path / 'install-diagnostic.txt'
    subprocess.run([sys.executable, '-', str(source), str(output)], input=decoder[1], text=True, check=True)
    assert serial_result == output.read_bytes() == b'key=[REDACTED] [REDACTED]\n'


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
@pytest.mark.parametrize(("stream", "expected", "fallback"), INSTALL_FAILURE_CASES)
def test_installer_failure_summary_is_bounded_and_secret_free(tmp_path, stream, expected, fallback, without_python):
    serial, result = run_installer_failure(tmp_path, stream, without_python)
    assert serial.splitlines()[-1] == f"SNOW_INSTALL_FAILED {fallback if without_python else expected}".encode()
    assert b"SECRET-RECOVERY" not in (tmp_path / "serial").read_bytes()
    assert b"SECRET-MESSAGE" not in (tmp_path / "serial").read_bytes()
    decoded = diagnostic(serial)
    assert b"SECRET-RECOVERY" not in decoded
    assert (tmp_path / "run/snow-install.ndjson").read_text() == stream
    assert (tmp_path / "run/snow-install.ndjson").stat().st_mode & 0o777 == 0o600
    if without_python:
        terminal = tmp_path / "run/snow-install.terminal"
        last_line = stream.rsplit('\n', 2)[-2] + '\n' if stream.endswith('\n') else stream.rsplit('\n', 1)[-1]
        assert terminal.read_bytes() == last_line.encode()
        assert terminal.stat().st_mode & 0o777 == 0o600


def test_installer_fallback_raw_control_check_does_not_pipe_into_grep():
    # grep -q may exit on an early NUL, causing upstream SIGPIPE; with pipefail
    # the negated pipeline can then wrongly classify a dirty line as clean.
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    guest = re.search(r"(?ms)^cat > \"\$WORK/install.sh\" <<'GUEST'\n(.*?)^GUEST$", script)
    assert guest
    fallback = guest[1].split('export LC_ALL=C', 1)[1].split("printf 'SNOW_INSTALL_FAILED firn_install:", 1)[0]
    assert not re.search(r"\|\s*grep\b", fallback)


@pytest.mark.parametrize(("phase", "key"), [
    ("installed-n", "installed_boot"), ("stage", "stage"),
    ("boot-n-plus-1", "reboot"), ("rollback", "reboot"), ("boot-n", "reboot"),
])
def test_run_guest_unit_uses_manifest_phase_budget_without_systemd_specifiers(tmp_path, phase, key):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assignment = re.search(r'(?m)^  guestunit="\[Unit\].*?^  gb=', script, re.S)
    assert assignment
    work = tmp_path / "work"
    work.mkdir()
    (work / "normalized.json").write_text(json.dumps(manifest()))
    # Run the real phase budget selection and the actual unit assignment.
    selection = re.search(r'(?m)^  case "\$phase" in installed-n\).*?;; esac', script, re.S)
    assert selection
    unit = subprocess.run(["bash", "-c", selection.group() + "\n" +
                           assignment.group().rsplit("  gb=", 1)[0] + '\nprintf "%s" "$guestunit"'],
                          env={**os.environ, "WORK": str(work), "phase": phase,
                               "qa_b64": "YQ==", "manifest_b64": "e30=", "nonce": NONCE,
                               "n": f"{REPO}@sha256:{N}", "next": f"{REPO}@sha256:{NEXT}",
                               "target": f"{REPO}:qa-controlled"},
                          capture_output=True, check=True, text=True).stdout
    # The phase budget plus qa.py PUBLISH_MARGIN for the record replay and poweroff.
    assert unit.splitlines().count(f'TimeoutStartSec={manifest()["timeouts"][key] + 150}') == 1
    assert "%" not in unit.replace("%%", "")


@pytest.mark.parametrize(("phase", "key"), [
    ("installed-n", "installed_boot"), ("stage", "stage"),
    ("boot-n-plus-1", "reboot"), ("rollback", "reboot"), ("boot-n", "reboot"),
])
def test_host_wait_outlives_guest_unit_budget(tmp_path, phase, key):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    selection = re.search(r'(?m)^  case "\$phase" in installed-n\).*?;; esac', script, re.S)
    assignment = re.search(r'(?m)^  guestunit="\[Unit\].*?^  gb=', script, re.S)
    # The wait sits in the shared boot body (2 or 3 levels deep with two modes).
    wait = re.search(r'(?m)^ +await SNOW_QA_V1 .*$', script)
    assert selection and assignment and wait
    work = tmp_path / "work"
    work.mkdir()
    (work / "normalized.json").write_text(json.dumps(manifest()))
    commands = (selection.group() + "\n" + assignment.group().rsplit("  gb=", 1)[0] +
                '\nprintf "%s\\n" "$guestunit"; await() { printf "HOST_WAIT=%s\\n" "$2"; }\n' + wait.group())
    result = subprocess.run(["bash", "-c", commands], capture_output=True, check=True, text=True,
                            env={**os.environ, "WORK": str(work), "phase": phase})
    unit_budget = int(re.search(r"(?m)^TimeoutStartSec=(\d+)$", result.stdout).group(1))
    host_wait = int(re.search(r"(?m)^HOST_WAIT=(\d+)$", result.stdout).group(1))
    assert unit_budget == manifest()["timeouts"][key] + 150
    # The record is first written within the phase budget; its replay within the unit budget.
    assert host_wait >= unit_budget + 150


def test_host_install_wait_outlives_install_unit_budget():
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assignment = re.search(r'(?m)^unit="\[Unit\].*?^drop=', script, re.S)
    wait = re.search(r'(?m)^await SNOW_INSTALL_OK .*$', script)
    assert assignment and wait
    commands = (assignment.group().rsplit("drop=", 1)[0] +
                '\nprintf "%s\\n" "$unit"; await() { printf "HOST_WAIT=%s\\n" "$2"; }\n' + wait.group())
    result = subprocess.run(["bash", "-c", commands], capture_output=True, check=True, text=True,
                            env={**os.environ, "install_timeout": str(manifest()["timeouts"]["install"]),
                                 "install_b64": "YQ=="})
    unit_budget = int(re.search(r"(?m)^TimeoutStartSec=(\d+)$", result.stdout).group(1))
    host_wait = int(re.search(r"(?m)^HOST_WAIT=(\d+)$", result.stdout).group(1))
    assert unit_budget == manifest()["timeouts"]["install"]
    assert host_wait >= unit_budget + 300


@pytest.mark.parametrize(("reason", "marker"), [
    ("bootc_status", "SNOW_QA_ERR bootc_status\n"),
    ("secret\nSNOW_QA_V1 fake", ""),
])
def test_guest_failure_console_reports_only_safe_gate_code(qa, monkeypatch, capsys, reason, marker):
    class Console(io.StringIO):
        def close(self):
            pass

    console = Console()
    original_open = open

    def safe_open(path, *args, **kwargs):
        return console if path == "/dev/console" else original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", safe_open)
    monkeypatch.setattr(qa, "GUEST_CONSOLES", ("/dev/console",))
    monkeypatch.setattr(qa, "PUBLISH_HOLD", 0)
    monkeypatch.setattr(qa, "serial_getty_ready", lambda **_: True)
    monkeypatch.setattr(qa, "guest", lambda _: (_ for _ in ()).throw(qa.GateError(reason)))
    assert qa.main(["guest", "/irrelevant"]) == 1
    # Written once at once, and once more after serial-getty's TTY setup.
    assert console.getvalue() == ("\n" + marker if marker else "") * 2
    assert "SNOW_QA_V1" not in console.getvalue()


@pytest.mark.parametrize("damage", ["policy", "pull_failure", "pull_timeout", "pull_mismatch", "secure_boot", "tpm_unsigned", "tpm_pcrlock", "tpm_wrong_pcrs",
                                    "marker", "outcome", "updater", "updater_timeout", "wrong_boot", "rollback_failure", "rollback_timeout", "pre_spec", "root_device_missing", "uki_other_entry", "uki_unmeasured", "uki_partial", "uki_prefixed", "esp_ambiguous", "esp_missing", "bls_wrong", "bls_linux", "pre_image_n", "pre_transport", "rollback_transport"])
def test_guest_never_emits_evidence_for_failed_probe_or_action(qa, tmp_path, monkeypatch, damage):
    m = manifest()
    m["cosign_key_sha256"] = hashlib.sha256(b"public-key").hexdigest()
    normalized = tmp_path / "normalized.json"
    normalized.write_bytes(qa.canonical(m))
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized)
    creds = tmp_path / "creds"
    creds.mkdir()
    phase = "rollback" if damage in ("rollback_failure", "rollback_timeout", "rollback_transport") else "stage"
    for key, value in {"phase": phase, "nonce": NONCE, "image_n": m["image_n"],
                        "image_n_plus_1": m["image_n_plus_1"], "target_ref": m["target_ref"]}.items():
        (creds / key).write_text(value)
    (creds / "unit_started").write_text("1000.00\n")
    (tmp_path / "uptime").write_text("1000.00 50.00\n")
    for key, value in {"boot_id": "12345678-1234-4234-8234-123456789abc",
                       "policy": json.dumps(SHIPPED_POLICY),
                       "key": "public-key", "update_check": "outcome=staged\n", "update_staged": STAGED_FILE,
                       "osrelease": KERNEL + "\n"}.items():
        (tmp_path / key).write_text(value)
    if damage in ("policy", "outcome", "marker", "wrong_boot"):
        (tmp_path / {"policy": "policy", "outcome": "update_check", "marker": "update_staged",
                      "wrong_boot": "boot_id"}[damage]).write_text("bad")
    esp = tmp_path / "esp"
    (esp / "loader/entries").mkdir(parents=True)
    (esp / UKI_PATH.lstrip("/")).parent.mkdir(parents=True)
    (esp / UKI_PATH.lstrip("/")).write_bytes(b"mock-uki")
    (esp / "loader/entries/snow.conf").write_text("uki " + (UKI_PATH.replace("d" * 128, "a" * 128) if damage == "bls_wrong" else UKI_PATH) + "\n" + ("linux /vmlinuz\n" if damage == "bls_linux" else ""))
    monkeypatch.setattr(qa, "GUEST_PATHS", {**{key: tmp_path / key for key in
        ("boot_id", "policy", "key", "update_check", "update_staged", "uptime", "osrelease")}, "esp": esp})
    monkeypatch.setattr(qa, "guest_lockdown", lambda: True)
    booted = NEXT if phase == "rollback" else N
    version = m["version_n_plus_1"] if phase == "rollback" else m["version_n"]
    status = {"spec": {"image": {"image": m["target_ref"], "transport": "containers-storage" if phase == "rollback" else "registry"}},
              "status": {"booted": {"image": {"imageDigest": "sha256:" + booted, "version": version}},
                             "staged": None,
                         "rollback": {"image": {"imageDigest": "sha256:" + N}} if phase == "rollback" else None}}
    if damage == "pre_spec":
        status["spec"]["image"]["image"] = f"{REPO}:latest"
    if damage == "pre_image_n":
        status["spec"]["image"]["image"] = m["image_n"]   # Firn records target_ref, never image_n
    if damage == "pre_transport":
        status["spec"]["image"]["transport"] = "containers-storage"   # stage pre-action must be registry
    if damage == "rollback_transport":
        status["spec"]["image"]["transport"] = "docker"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs.get("capture_output") and not kwargs.get("shell")
        if ((damage == "pull_timeout" and args[:2] == ["podman", "pull"]) or
            (damage == "updater_timeout" and args == ["/usr/libexec/bootc-update-stage"]) or
            (damage == "rollback_timeout" and args == ["bootc", "rollback"])):
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if args[:2] == ["bootc", "status"]:
            data = json.dumps(status).encode()
        elif args[:2] == ["podman", "image"]:
            data = ("sha256:" + (N if damage == "pull_mismatch" else NEXT)).encode()
        elif args[:2] == ["cryptsetup", "luksDump"]:
            token = dict(TPM_TOKEN)
            if damage == "tpm_unsigned":
                token.pop("tpm2_pubkey")
            if damage == "tpm_pcrlock":
                token["tpm2-pcrlock"] = "enabled"
            if damage == "tpm_wrong_pcrs":
                token["tpm2-pcrs"] = [11]
            data = json.dumps({"tokens": {"0": token}}).encode()
        elif args[0] == "lsblk":
            blocks = json.loads(json.dumps(BLOCKS))
            if damage == "esp_ambiguous":
                blocks["blockdevices"].append(dict(blocks["blockdevices"][1]))
            if damage == "esp_missing":
                blocks["blockdevices"].pop()
            data = json.dumps(blocks).encode()
        elif args[0] in ("mount", "umount"):
            data = b""
        elif args[0] == "/usr/libexec/bootc-update-stage":
            status["spec"]["image"]["transport"] = "containers-storage"
            status["status"]["staged"] = {"image": {"imageDigest": "sha256:" + NEXT}}
            data = b""
        else:
            data = {"podman": b"", "mokutil": b"SecureBoot disabled" if damage == "secure_boot" else b"SecureBoot enabled",
                    "bootctl": ("  Secure Boot: enabled (user)\n  Measured UKI: " + ("no" if damage == "uki_unmeasured" else "yes") + "\n  Current: $BOOT" + UKI_PATH + " (on the EFI System Partition)\n" +
                                ("  Default: /boot/efi" + UKI_PATH.replace("d" * 128, "a" * 128) + "\n" if damage == "uki_other_entry" else "") +
                                ("  Other: " + UKI_PATH + ".old\n" if damage == "uki_partial" else "") +
                                ("  Other: evil" + UKI_PATH + "\n" if damage == "uki_prefixed" else "")).encode(),
                    "cryptsetup": b"/dev/mapper/root is active\n" +
                        (b"" if damage == "root_device_missing" else b"  device: /dev/vda3"),
                    "findmnt": b"btrfs", "/usr/libexec/bootc-update-stage": b"", "bootc": b""}[args[0]]
        fail = (damage == "pull_failure" and args[:2] == ["podman", "pull"]) or (
            damage == "updater" and args[0] == "/usr/libexec/bootc-update-stage") or (
            damage == "rollback_failure" and args == ["bootc", "rollback"])
        return subprocess.CompletedProcess(args, 1 if fail else 0, data, b"secret-never-print")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)
    reasons = {"policy": "policy", "pull_failure": "policy_pull", "pull_timeout": "policy_pull", "pull_mismatch": "pull_digest",
               "secure_boot": "secure_boot", "tpm_unsigned": "tpm_token", "tpm_pcrlock": "tpm_token",
               "tpm_wrong_pcrs": "tpm_token", "marker": "stage_marker", "outcome": "stage_outcome",
               "updater": "updater", "updater_timeout": "updater", "wrong_boot": "boot_identity", "rollback_failure": "rollback_action", "rollback_timeout": "rollback_action",
               "pre_spec": "booted_image", "pre_image_n": "booted_image", "pre_transport": "booted_image",
               "rollback_transport": "rollback_image", "root_device_missing": "root_luks",
               "uki_other_entry": "uki", "uki_unmeasured": "uki", "uki_partial": "uki", "uki_prefixed": "uki", "esp_ambiguous": "esp_discovery",
               "esp_missing": "esp_discovery", "bls_wrong": "uki_bls", "bls_linux": "uki_bls"}
    with pytest.raises(qa.GateError, match="^" + reasons[damage] + "$"):
        qa.guest(creds)
    if damage in ("pre_spec", "pre_image_n", "pre_transport", "uki_other_entry", "uki_unmeasured", "uki_partial", "uki_prefixed", "esp_ambiguous", "esp_missing", "bls_wrong", "bls_linux", "tpm_unsigned", "tpm_pcrlock", "tpm_wrong_pcrs"):
        assert ["/usr/libexec/bootc-update-stage"] not in calls
    if damage == "rollback_transport":
        assert ["bootc", "rollback"] not in calls
    if damage in ("uki_other_entry", "uki_unmeasured", "uki_partial", "uki_prefixed", "bls_wrong", "bls_linux"):
        assert ["umount", str(esp)] in calls
    if damage in ("esp_ambiguous", "esp_missing"):
        assert not any(command[0] == "mount" for command in calls)


def test_boot_n_requires_rollback_n_plus_1(qa, tmp_path, capsys):
    normalized, checks = phase_files(tmp_path, qa)
    serial = tmp_path / "serial"
    record = phase_record("boot-n")
    record["rollback_digest"] = NEXT
    serial.write_text(serial_record(record))
    assert qa.main(["phase", str(normalized), "boot-n", NONCE, "none", str(serial), str(checks)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == record["boot_id"]
    record["rollback_digest"] = None
    serial.write_text(serial_record(record))
    assert qa.main(["phase", str(normalized), "boot-n", NONCE, "none", str(serial), str(checks)]) == 1


@pytest.mark.parametrize(("payload", "reason"), [
    (b"not-json", "record_encoding"),
    (b'{"phase":"stage","phase":"stage"}', "duplicate_key"),
    (b'{"x":NaN}', "record_encoding"),
])
def test_single_marker_rejects_invalid_json_without_other_markers(qa, tmp_path, capsys, payload, reason):
    normalized, checks = phase_files(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text("unrelated\nSNOW_QA_V1 " + base64.urlsafe_b64encode(payload).decode().rstrip("=") + "\n")
    assert qa.main(["phase", str(normalized), "stage", NONCE, "none", str(serial), str(checks)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:" + reason


def test_single_marker_rejects_noncanonical_base64(qa, tmp_path, capsys):
    normalized, checks = phase_files(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text("SNOW_QA_V1 " + base64.urlsafe_b64encode(b"{}").decode().rstrip("=") + "=\n")
    assert qa.main(["phase", str(normalized), "stage", NONCE, "none", str(serial), str(checks)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:record_encoding"


def test_single_marker_rejects_noncanonical_pad_bits(qa, tmp_path, capsys):
    normalized, checks = phase_files(tmp_path, qa)
    # e30 decodes to {}, and e31 decodes to the same bytes with nonzero pad bits.
    serial = tmp_path / "serial"
    serial.write_text("SNOW_QA_V1 e31\n")
    assert qa.main(["phase", str(normalized), "stage", NONCE, "none", str(serial), str(checks)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:record_encoding"


def test_guest_shell_requires_credentials_without_echoing_inputs(tmp_path):
    scripts = yaml.safe_load(SOURCE.read_text())["data"]
    assert set(scripts) == {"guest.sh", "qa.py", "run.sh"}
    guest = tmp_path / "guest.sh"
    guest.write_text(scripts["guest.sh"])
    no_creds = subprocess.run(["sh", str(guest)], capture_output=True, env={"PATH": "/usr/bin"}, check=False)
    assert no_creds.returncode != 0 and b"SNOW_QA_V1" not in no_creds.stdout
    assert b"set -x" not in scripts["guest.sh"].encode()


@pytest.mark.parametrize("status", [{"spec": None, "status": {}}, {"spec": {"image": {}}, "status": {"booted": []}}])
def test_malformed_bootc_status_fails_with_bounded_gate_error(qa, status):
    with pytest.raises(qa.GateError, match="^bootc_status$"):
        qa.status_fields(status)


def test_current_esp_uki_requires_measured_secure_exact_path_without_assumed_heading(qa):
    status = ("  Secure Boot: enabled (user)\n  Measured UKI: yes\n  Current: $BOOT" + UKI_PATH +
              " (on the EFI System Partition)\n  Default: /boot/efi" + UKI_PATH + "\n").encode()
    assert qa.current_uki(status, UKI_PATH)
    assert not qa.current_uki(status, UKI_PATH.replace("d" * 128, "a" * 128))
    assert not qa.current_uki(status + b"Other: " + UKI_PATH.replace("d" * 128, "a" * 128).encode(), UKI_PATH)
    assert not qa.current_uki(status + b"Other: " + (UKI_PATH + ".old").encode(), UKI_PATH)
    assert not qa.current_uki(status + b"Other: evil" + UKI_PATH.encode(), UKI_PATH)
    assert not qa.current_uki(status + b"Other: /unrelated" + UKI_PATH.encode(), UKI_PATH)
    assert not qa.current_uki(status.replace(b"enabled (user)", b"disabled"), UKI_PATH)


def test_esp_probe_rejects_symlinked_uki_parent(qa, tmp_path):
    esp = tmp_path / "esp"
    outside = tmp_path / "outside"
    (esp / "loader/entries").mkdir(parents=True)
    (esp / "loader/entries/snow.conf").write_text("uki " + UKI_PATH + "\n")
    outside.mkdir()
    (outside / UKI_PATH.rsplit("/", 1)[-1]).write_bytes(b"not-on-esp")
    (esp / "EFI/Linux").mkdir(parents=True)
    (esp / "EFI/Linux/bootc").symlink_to(outside, target_is_directory=True)
    status = ("Secure Boot: enabled\nMeasured UKI: yes\n" + UKI_PATH + "\n").encode()
    with pytest.raises(qa.GateError, match="^uki$"):
        qa.esp_uki(esp, status)


@pytest.mark.parametrize("value", [-1, "0", True, None, 1.0, 10**9 + 1])
def test_phase_rejects_malformed_fsverity_count(qa, tmp_path, capsys, value):
    normalized, checks = phase_files(tmp_path, qa)
    record = phase_record("stage")
    record["fsverity_errors"] = value
    serial = tmp_path / "serial"
    serial.write_text(serial_record(record))
    assert qa.main(["phase", str(normalized), "stage", NONCE, "none", str(serial), str(checks)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:fsverity_field"


@pytest.mark.parametrize("value", [None, "", "7.2 11", "a" * 65, 7, "7.2.11\n+x"])
def test_phase_rejects_malformed_kernel(qa, tmp_path, capsys, value):
    normalized, checks = phase_files(tmp_path, qa)
    record = phase_record("stage")
    record["kernel"] = value
    serial = tmp_path / "serial"
    serial.write_text(serial_record(record))
    assert qa.main(["phase", str(normalized), "stage", NONCE, "none", str(serial), str(checks)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:kernel_field"


def test_phase_rejects_record_without_kernel(qa, tmp_path, capsys):
    normalized, checks = phase_files(tmp_path, qa)
    record = phase_record("stage")
    del record["kernel"]
    serial = tmp_path / "serial"
    serial.write_text(serial_record(record))
    assert qa.main(["phase", str(normalized), "stage", NONCE, "none", str(serial), str(checks)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:record_fields"


def test_phase_keeps_attested_boot_when_fsverity_errors_are_nonzero(qa, tmp_path, capsys):
    # The runner judges the count after it has stored the evidence.
    normalized, checks = phase_files(tmp_path, qa)
    record = phase_record("stage")
    record["fsverity_errors"] = 3
    serial = tmp_path / "serial"
    serial.write_text(serial_record(record))
    assert qa.main(["phase", str(normalized), "stage", NONCE, "none", str(serial), str(checks)]) == 0
    assert capsys.readouterr().out.splitlines() == [record["boot_id"], "fsverity_errors=3", "kernel=" + KERNEL]


def test_five_phase_chain_rejects_reused_boot_identity(qa, tmp_path):
    normalized, checks = phase_files(tmp_path, qa)
    prior = "none"
    for number, phase in enumerate(PHASES, 1):
        record = phase_record(phase)
        record["boot_id"] = f"{number:08x}-1234-4234-8234-123456789abc"
        record["nonce"] = f"{number:032x}"
        serial = tmp_path / "serial"
        serial.write_text(serial_record(record))
        assert qa.phase_verdict(manifest(), phase, record["nonce"], prior, serial, checks) == record["boot_id"]
        with pytest.raises(qa.GateError, match="^boot_identity$"):
            qa.phase_verdict(manifest(), phase, record["nonce"], record["boot_id"], serial, checks)
        prior = record["boot_id"]


def test_manual_template_contract():
    path = SOURCE.parent / "run-snow-bootc-lifecycle.yaml"
    template = yaml.safe_load(path.read_text())
    assert template["kind"] == "WorkflowTemplate"
    assert template["metadata"]["namespace"] == "argo"
    spec = template["spec"]
    assert "schedule" not in str(spec) and "cron" not in str(spec).lower()
    entry = next(t for t in spec["templates"] if t["name"] == spec["entrypoint"])
    assert entry["inputs"]["parameters"] == [
        {"name": "manifest-json"}, {"name": "keep-vm-on-failure", "value": "false"}]
    assert entry["synchronization"]["semaphores"][0]["configMapKeyRef"]["key"] == "snosi-vm-qa"
    outputs = entry["outputs"]["parameters"]
    assert {o["valueFrom"]["path"] for o in outputs} == {
        "/tmp/results/result-summary.txt", "/tmp/results/checks.txt"}
    container = entry["container"]
    assert container["command"] == ["/bin/bash", "/opt/snow-qa/run.sh"]
    assert {v["name"] for v in container["volumeMounts"]} == {"scripts", "incus-bin", "incus-sock", "incus-pools", "iso-cache", "evidence", "results"}
    assert any(v.get("configMap", {}).get("name") == "snow-bootc-lifecycle-scripts" for v in spec["volumes"])
    assert not any("serviceAccount" in str(t) or "privileged" in str(t) for t in spec["templates"])
    assert entry["activeDeadlineSeconds"] >= 30000
    assert any(v["mountPath"] == "/var/lib/snosi-lab/snow-bootc-evidence" for v in container["volumeMounts"])


def test_manual_lane_docs_match_runner_manifest_and_template(qa):
    root = SOURCE.parents[2]
    template_path = SOURCE.parent / "run-snow-bootc-lifecycle.yaml"
    template = yaml.safe_load(template_path.read_text())
    entry = next(t for t in template["spec"]["templates"] if t["name"] == template["spec"]["entrypoint"])
    assert entry["inputs"]["parameters"] == [
        {"name": "manifest-json"}, {"name": "keep-vm-on-failure", "value": "false"}]
    readme = (root / "README.md").read_text()
    quality = (root / "docs/quality.md").read_text()
    section = readme.split("### Manual Snow bootc lifecycle", 1)[1].split("\n### ", 1)[0]
    quality_section = quality.split("## Manual Snow bootc lifecycle evidence", 1)[1].split("\n## ", 1)[0]
    assert "argo/workflow-templates/run-snow-bootc-lifecycle.yaml" in section
    assert "../argo/workflow-templates/run-snow-bootc-lifecycle.yaml" in quality_section
    assert "manifest-json" in section and "manifest-json" in quality_section
    documented = set(re.findall(r"^\| `([a-z][a-z0-9_]*)` \|", section, flags=re.M))
    assert documented == qa.FIELDS
    timeout_row = next(line for line in section.splitlines() if line.startswith("| `timeouts` |"))
    assert set(re.findall(r"`([a-z_]+)`", timeout_row)) == {
        "timeouts", "iso_boot", "install", "installed_boot", "stage", "reboot"}
    assert '"$MANIFEST_FILE"' in section
    commands = re.findall(r"```bash\n(.*?)\n```", section, flags=re.S)
    assert len(commands) == 1
    assert "--from workflowtemplate/" + template["metadata"]["name"] in commands[0]
    assert '-p "manifest-json=$(<"$MANIFEST_FILE")"' in commands[0]
    assert 'exit 1' not in commands[0]
    assert not re.search(r"sha256:[a-f0-9]{64}|:latest|:example|:placeholder", commands[0])
    assert "not scheduled" in section and "not a release gate" in section
    # The first manual main-lane PASS is recorded, and the lane stays advisory.
    assert "first manual main-lane PASS" in quality_section and "release gate" in quality_section
    assert "first manual main-lane PASS" in section


def test_workflow_deadline_covers_maximum_guest_windows_and_host_margins(qa):
    template = yaml.safe_load((SOURCE.parent / "run-snow-bootc-lifecycle.yaml").read_text())
    entry = next(t for t in template["spec"]["templates"] if t["name"] == template["spec"]["entrypoint"])
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]

    def host_timeout(command):
        match = re.search(r"(?m)^\s*timeout (\d+) " + command + r"(?:\s|$)", script)
        assert match, f"missing bounded host operation: {command}"
        return int(match[1])

    limits = qa.LIMITS
    # ISO boot, installer and five post-install boots each consume a separate
    # host await; six unit waits carry an additional 300s startup/polling margin.
    assert 'await SNOW_INSTALL_OK "$((install_timeout + 300))"' in script
    assert 'await SNOW_QA_V1 "$((budget + 300))"' in script
    guest_windows = sum(limits[key] for key in
                        ("iso_boot", "install", "installed_boot", "stage")) + 3 * limits["reboot"] + 6 * 300
    # Preflight's ISO download allows install+30; the host downloads it again.
    assert 'timeout=m["timeouts"]["install"] + 30' in yaml.safe_load(SOURCE.read_text())["data"]["qa.py"]
    iso_downloads = 2 * (limits["install"] + 30)
    # Preflight: five fetches, two GPG checks, mcopy/zstd/seven cpio reads,
    # two sandbox runs, two cosign + two image inspections, three tag resolves.
    preflight = (5 + 2 + 1 + 1 + 7 + 4 + 3) * 120 + 2 * 30
    apt = host_timeout(r"apt-get update") + host_timeout(r"apt-get install")
    tag_checks = 9 * 120  # One bounded skopeo resolve for every tag invocation.
    vm_ops = (host_timeout(r"incus init") + 2 * host_timeout(r"incus config device add") +
              6 * host_timeout(r"incus config set") + 6 * host_timeout(r"incus start") +
              host_timeout(r"incus stop") + host_timeout(r"incus query") +
              host_timeout(r"curl") + host_timeout(r"virt-fw-vars --inplace") +
              host_timeout(r"virt-fw-vars -i") + host_timeout(r"incus config device remove") +
              host_timeout(r"incus delete"))
    # Five pre-boot serial baselines, plus one potential last poll per await.
    serial_timeout = re.search(r"(?m)^\s*timeout=(\d+), check=False, preexec_fn=limit", script)
    assert serial_timeout
    serial_polling = (5 + 6) * int(serial_timeout[1])
    # Each of the five phases waits up to the reboot budget for the guest's poweroff. An
    # iteration is one bounded `incus info` plus a 5s sleep; the deadline check follows an
    # iteration, so the overshoot is at most one more info and sleep.
    info_timeout = re.search(r"info=\$\(timeout (\d+) incus info", script)
    assert info_timeout and 'shutdown_end=$((SECONDS + shutdown_budget + 150))' in script
    shutdown_waits = 5 * (limits["reboot"] + 150 + 2 * int(info_timeout[1]) + 5)
    worst_case = shutdown_waits + guest_windows + iso_downloads + preflight + apt + tag_checks + vm_ops + serial_polling
    assert entry["activeDeadlineSeconds"] * 10 >= worst_case * 11


def test_runner_extracts_and_uses_bounded_serial_and_cleanup(tmp_path):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    path = tmp_path / "run.sh"
    path.write_text(script)
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0
    assert "set -x" not in script
    assert "incus delete --force" in script
    assert "SNOW_QA_V1" in script
    assert "timeout" in script
    assert "qa.py" in script
    assert 'seen_boot_ids' in script
    assert '[[ ${#ub} -lt 60000 ]]' in script
    assert '[[ ${#gb} -lt 60000 ]]' in script
    assert 'python3-virt-firmware' in script
    assert 'pip install' not in script
    assert 'resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))' in script


def test_runner_installs_gpgv_without_recommends(tmp_path):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    install_command = re.search(
        r"(?m)^timeout 900 apt-get install .*?(?:\\\n  .*?)*\n  >/dev/null 2>&1 \|\| blocked packages_unavailable",
        script,
    )
    assert install_command
    tools = tmp_path / "tools"
    tools.mkdir()
    apt = tools / "apt-get"
    apt.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$APT_ARGS"\n')
    apt.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", 'blocked() { exit 1; }\n' + install_command.group()],
        env={**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"],
             "APT_ARGS": str(tmp_path / "apt-args")},
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--no-install-recommends" in (tmp_path / "apt-args").read_text().splitlines()
    assert "gpgv" in (tmp_path / "apt-args").read_text().splitlines()


@pytest.mark.parametrize(("scenario", "expected"), [
    ("preflight", "BLOCKED: publication_unavailable:trust_material_missing"),
    ("signature", "FAILED: preflight_mismatch:oci_signature"),
    ("firn_sandbox", "BLOCKED: environment_unavailable:firn_sandbox"),
    ("preflight_garbage", "FAILED: preflight_mismatch:unparsed"),
    ("preflight_multiline", "FAILED: preflight_mismatch:unparsed"),
    ("preflight_injection", "FAILED: preflight_mismatch:unparsed"),
    ("preflight_overlong", "FAILED: preflight_mismatch:unparsed"),
    ("tag", "FAILED: tag_mismatch:tag_drift"),
    ("tag_multiline", "FAILED: tag_mismatch:unparsed"),
    ("evidence", "FAILED: evidence_write"),
    ("invalid", "FAILED: manifest_invalid:unparsed"),
    ("invalid_code", "FAILED: manifest_invalid:manifest_fields"),
    ("invalid_multiline", "FAILED: manifest_invalid:unparsed"),
    ("invalid_name", "FAILED: workflow_name"),
    ("apt_unavailable", "BLOCKED: packages_unavailable"),
    ("missing_gpgv", "BLOCKED: tool_unavailable:gpgv"),
    ("iso_changed", "FAILED: iso_changed"),
    ("manifest_read", "FAILED: manifest_read"),
])
def test_runner_failure_never_initializes_vm_and_does_not_expose_inputs(tmp_path, scenario, expected):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    # Isolate both host paths using shell substitution; all external operations
    # are mocked as executables, not a real Incus/registry/cluster call.
    script = script.replace("ROOT=/var/lib/snosi-lab/snow-lifecycle", f"ROOT={tmp_path}/evidence").replace("ROOT=/var/lib/snosi-lab/snow-bootc-evidence", f"ROOT={tmp_path}/evidence")
    script = script.replace("OUT=/tmp/results", f"OUT={tmp_path}/results")
    script = script.replace("/opt/snow-qa/qa.py", f"{tmp_path}/qa.py")
    script = script.replace('/var/lib/snosi-lab/iso/snow-qa-', f'{tmp_path}/snow-qa-')
    if scenario == 'missing_gpgv':
        # gpgv may exist on the test host; hide only that lookup in this shell.
        script = script.replace('# Extracted Firn is hashed in preflight',
                                'command() { if [[ "$1" == -v && "$2" == gpgv ]]; then return 1; fi; builtin command "$@"; }\n# Extracted Firn is hashed in preflight')
    script = script.replace("persist \"$WORK/hash\" \"$EVIDENCE/manifest-sha256.txt\"", "false") if scenario == "evidence" else script
    runner = tmp_path / "run.sh"
    runner.write_text(script)
    (tmp_path / "qa.py").write_text(textwrap.dedent('''
        import json, os, sys
        from pathlib import Path
        command = sys.argv[1]
        if command == 'validate':
            if os.environ['SCENARIO'] in ('invalid_code', 'invalid_multiline'):
                print('qa_failed:manifest_fields' + ('\\nqa_failed:other' if os.environ['SCENARIO'] == 'invalid_multiline' else ''), file=sys.stderr)
                sys.exit(1)
            Path(sys.argv[3]).write_text(json.dumps(json.loads(Path(sys.argv[2]).read_text())))
            print('a' * 64)
        elif command == 'preflight':
            if os.environ['SCENARIO'] in ('preflight', 'signature', 'firn_sandbox', 'preflight_garbage', 'preflight_multiline', 'preflight_injection', 'preflight_overlong'):
                messages = {'preflight': 'qa_failed:trust_material_missing', 'signature': 'qa_failed:oci_signature', 'firn_sandbox': 'qa_failed:firn_sandbox',
                            'preflight_garbage': 'nonsense', 'preflight_multiline': 'qa_failed:download\\nextra',
                            'preflight_injection': 'qa_failed:download; sensitive-value',
                            'preflight_overlong': 'qa_failed:' + 'a' * 41}
                print(messages[os.environ['SCENARIO']], file=sys.stderr)
                sys.exit(1)
            Path(sys.argv[3]).write_text('{}')
        elif command == 'tag':
            print('qa_failed:tag_drift' + ('\\nsecret sensitive-value' if os.environ['SCENARIO'] == 'tag_multiline' else ''), file=sys.stderr)
            sys.exit(1)
        else:
            sys.exit(1)
    '''))
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool in ("curl", "gpg", "gpgv", "skopeo", "cosign", "mcopy", "bwrap", "zstd", "cpio", "incus", "virt-fw-vars"):
        binary = tools / tool
        binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{tool}' >> '{tmp_path}/calls'\nexit 99\n")
        binary.chmod(0o755)
    (tools / 'apt-get').write_text('#!/bin/sh\nexit 0\n')
    if scenario == 'apt_unavailable':
        (tools / 'apt-get').write_text('#!/bin/sh\nexit 1\n')
    (tools / 'apt-get').chmod(0o755)
    (tools / 'python3').write_text('#!/bin/sh\nif [ "$1" = -m ]; then exit 0; fi\nif [ "$SCENARIO" = manifest_read ] && [ "$1" = -c ]; then case "$2" in *image_n_plus_1*) exit 1;; esac; fi\nexec /usr/bin/python3 "$@"\n')
    (tools / 'python3').chmod(0o755)
    (tools / 'curl').write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then shift; if [ "$SCENARIO" = iso_changed ]; then printf altered-iso > "$1"; else printf iso-data > "$1"; fi; exit 0; fi; shift; done\nexit 1\n')
    safe_manifest = manifest()
    safe_manifest['iso_sha256'] = hashlib.sha256(b'iso-data').hexdigest()
    env = {**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"],
           "WORKFLOW_NAME": "mock-run", "MANIFEST_JSON": '{"bad":"sensitive-value"}' if scenario == 'invalid' else json.dumps(safe_manifest),
           "SCENARIO": scenario}
    if scenario == 'invalid':
        (tmp_path / 'qa.py').write_text('import sys; print(open(sys.argv[2]).read(), file=sys.stderr); sys.exit(1)\n')
    if scenario == 'invalid_name':
        env['WORKFLOW_NAME'] = 'path/../../untrusted'
    run = subprocess.run(["bash", str(runner)], env=env, capture_output=True, timeout=30)
    assert run.returncode != 0
    assert b"sensitive-value" not in run.stdout + run.stderr
    assert b"sensitive-value" not in (tmp_path / 'results/result-summary.txt').read_bytes()
    assert b"SNOW_QA_V1" not in run.stdout + run.stderr
    assert not (tmp_path / "calls").exists() or "init" not in (tmp_path / "calls").read_text()
    assert (tmp_path / "results/result-summary.txt").read_text().startswith(("FAILED:", "BLOCKED:"))
    assert (tmp_path / "results/result-summary.txt").read_text().strip() == expected


@pytest.mark.parametrize("scenario", ["pass", "stage", "stage_keep", "iso_add_partial_keep", "iso_add_partial_detach_failed", "no_reboot", "tag_drift", "evidence_write", "output_write", "stage_teardown", "teardown", "serial_oversize", "serial_unreadable", "blocked_teardown", "phase_missing", "phase_malformed", "guest_error", "guest_error_crlf", "updater_error_crlf", "rollback_error_crlf", "duplicate_error_crlf", "conflicting_error_crlf", "install_missing", "install_failed", "install_firn_v2", "install_firn_hash", "install_fake_code", "install_disk_detect", "install_disk_byid", "install_progress", "install_bad_progress", "install_secret_code", "install_fallback", "install_empty_stream", "install_validate_secret", "install_validate_unknown", "install_validate_unclassified", "install_validate_fake", "cumulative_pass", "cumulative_stale", "cumulative_reset", "cumulative_prefix", "fsverity", "fsverity_known", "guest_shutdown", "nvram_escape", "console_reset_each_start", "reset_mid_boot", "reset_twice", "reset_then_truncate", "empty_then_reset", "installer_restart", "reset_then_stale", "reset_then_stale_error", "stopped_without_record", "final_read_record", "final_read_error", "final_read_conflict", "final_read_fresh_record"])
def test_runner_five_boots_and_post_init_failures_are_offline(tmp_path, scenario):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    for old, new in (("ROOT=/var/lib/snosi-lab/snow-bootc-evidence", f"ROOT={tmp_path}/evidence"),
                     ("OUT=/tmp/results", f"OUT={tmp_path}/results"),
                     ("/var/lib/snosi-lab/iso/snow-qa-", f"{tmp_path}/snow-qa-"),
                     ("/var/lib/incus/storage-pools", str(tmp_path / "pools")),
                     ("/opt/snow-qa/qa.py", str(tmp_path / "qa.py"))):
        script = script.replace(old, new)
    if scenario in ('reset_twice', 'empty_then_reset', 'reset_mid_boot', 'installer_restart', 'reset_then_stale', 'reset_then_stale_error',
                    'stopped_without_record', 'final_read_record', 'final_read_error', 'final_read_conflict', 'final_read_fresh_record'):
        script = script.replace('sleep 5', 'sleep 0.01')  # several polls in one boot
    if scenario in ('phase_missing', 'install_missing', 'install_failed', 'cumulative_stale', 'guest_error_crlf', 'updater_error_crlf', 'rollback_error_crlf', 'duplicate_error_crlf', 'conflicting_error_crlf', 'guest_shutdown'):
        # Keep the actual await loop; cap its clock only in offline timeout tests.
        assert 'end=$((SECONDS + $2))' in script
        script = script.replace('end=$((SECONDS + $2))', 'end=$((SECONDS + 1))')
        assert 'shutdown_end=$((SECONDS + shutdown_budget + 150))' in script
        script = script.replace('shutdown_end=$((SECONDS + shutdown_budget + 150))', 'shutdown_end=$((SECONDS + 1))')
        script = script.replace('sleep 5', 'sleep 0.01')
    if scenario == "evidence_write":
        script = script.replace('local source="$1" dest="$2"',
                                'local source="$1" dest="$2"\n  if [[ "$source" == "$WORK/checks.json" ]] && grep -q after-stage "$source"; then return 1; fi')
    if scenario == 'output_write':
        old = 'if ! printf \'%s: %s\\n\' "$STATE" "$REASON" > "$summary_source"; then'
        assert old in script
        script = script.replace(old, 'if ! false; then')
    (tmp_path / "run.sh").write_text(script)
    # Mock orchestration only: synthetic serial/phase output cannot prove a
    # real guest or systemd boot. The unit specifier seam is tested separately.
    (tmp_path / "qa.py").write_text(textwrap.dedent('''
        import json, os, sys, hashlib, re
        from pathlib import Path
        cmd = sys.argv[1]; root = Path(os.environ['MOCK_ROOT'])
        def log(line):
            with (root / 'events').open('a') as stream: stream.write(line + '\\n')
        if cmd == 'validate':
            m = json.loads(Path(sys.argv[2]).read_text())
            Path(sys.argv[3]).write_text(json.dumps(m))
            print(hashlib.sha256(Path(sys.argv[3]).read_bytes()).hexdigest())
        elif cmd == 'preflight':
            log('preflight')
            Path(sys.argv[3]).write_text(json.dumps({'tags': {}, 'firn_sha256': 'f' * 64}))
        elif cmd == 'tag':
            phase = sys.argv[3]; log('tag ' + phase)
            if os.environ['SCENARIO'] == 'tag_drift' and phase == 'after-stage': sys.exit(1)
            p = Path(sys.argv[4]); data = json.loads(p.read_text())
            data['tags'][phase] = '2026-09-24T00:00:00+00:00'; p.write_text(json.dumps(data))
        elif cmd == 'phase':
            phase, nonce, prior, serial = sys.argv[3:7]
            log('phase ' + phase + ' ' + nonce + ' ' + prior)
            match = re.search(r'SNOW_QA_V1 ([a-z0-9-]+) ([0-9a-f]{32}) ([0-9]+)', Path(serial).read_text())
            if not match or (phase, nonce) != match.group(1, 2): sys.exit(1)
            number = int(match.group(3))
            expected = 'none' if number == 1 else f'{number - 1:08x}-1234-4234-8234-123456789abc'
            if prior != expected or os.environ['SCENARIO'] in ('stage', 'stage_keep', 'stage_teardown') and phase == 'stage': sys.exit(1)
            if os.environ['SCENARIO'] == 'no_reboot' and phase == 'boot-n-plus-1': print(prior); print('fsverity_errors=0'); print('kernel=7.3.1+deb14-amd64'); sys.exit(0)
            print(f'{number:08x}-1234-4234-8234-123456789abc')
            errors = os.environ['SCENARIO'] in ('fsverity', 'fsverity_known') and phase == 'boot-n-plus-1'
            print('fsverity_errors=' + ('2' if errors else '0'))
            print('kernel=' + ('7.2.11+deb14-amd64' if os.environ['SCENARIO'] == 'fsverity_known' and phase == 'boot-n-plus-1' else '7.3.1+deb14-amd64'))
        else: sys.exit(1)
    '''))
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / 'incus').write_text(textwrap.dedent('''
        #!/usr/bin/env python3
        import base64, json, os, re, sys
        from pathlib import Path
        root = Path(os.environ['MOCK_ROOT']); args = sys.argv[1:]
        with (root / 'events').open('a') as out: out.write('incus ' + ' '.join(args[:3] if args[:2] == ['config', 'set'] else args[:4]) + '\\n')
        if args[0] == 'config' and args[1] == 'show' and '--format=json' in args:
            sys.exit(1)  # Incus 7: "unknown flag: --format".
        elif args[0] == 'query' and args[1].startswith('/1.0/instances/'):
            # The instance object; its config holds the large raw.qemu payload.
            print(json.dumps({'name': args[1].rsplit('/', 1)[-1], 'config': {'raw.qemu': 'x' * 70000},
                              'expanded_devices': {'root': {'pool': 'qa-pool', 'type': 'disk', 'path': '/'}}}))
        elif args[:3] == ['config', 'device', 'add'] and args[4] == 'installer':
            (root / 'installer-attached').write_text('yes')
            if os.environ['SCENARIO'] in ('iso_add_partial_keep', 'iso_add_partial_detach_failed'):
                sys.exit(1)  # Incus changed state but the command reported failure.
        elif args[:3] == ['config', 'device', 'remove'] and args[4] == 'installer':
            attached = root / 'installer-attached'
            if not attached.exists(): sys.exit(1)
            if os.environ['SCENARIO'] == 'iso_add_partial_detach_failed': sys.exit(1)
            attached.unlink()
        elif args[0] == 'config' and args[1] == 'set':
            unit = base64.b64decode(re.search(r'service=([A-Za-z0-9+/=]+)', args[-1]).group(1)).decode()
            (root / 'unit').write_text(unit)
        elif args[0] == 'start':
            count = int((root / 'count').read_text()) + 1 if (root / 'count').exists() else 1
            (root / 'count').write_text(str(count))
            # Like Incus 7: qemu.nvram is a relative symlink to the varstore file.
            vmdir = root / 'pools' / 'qa-pool' / 'virtual-machines' / args[1]
            vmdir.mkdir(parents=True, exist_ok=True)
            varstore = vmdir / 'OVMF_VARS.4MB.ms.fd'
            if not varstore.exists(): varstore.write_text('nvram')
            link = vmdir / 'qemu.nvram'
            if not link.is_symlink():
                if os.environ['SCENARIO'] == 'nvram_escape':
                    (root / 'outside.fd').write_text('nvram')
                    link.symlink_to(root / 'outside.fd')
                else:
                    link.symlink_to('OVMF_VARS.4MB.ms.fd')
        elif args[0] == 'console':
            count = int((root / 'count').read_text())
            if os.environ['SCENARIO'] == 'serial_oversize' and count == 3:
                sys.stdout.write('X' * 1048577); sys.exit(0)
            if os.environ['SCENARIO'] in ('serial_unreadable', 'blocked_teardown') and count == 3: sys.exit(1)
            content = ''
            if count == 1:
                content = 'SNOW_INSTALL_BEGIN\\n'
                if os.environ['SCENARIO'] == 'install_failed': content += 'SNOW_INSTALL_FAILED\\n'
                elif os.environ['SCENARIO'].startswith('install_firn_'): content += 'SNOW_INSTALL_FAILED ' + os.environ['SCENARIO'][8:] + '\\r\\n'
                elif os.environ['SCENARIO'] == 'install_fake_code': content += 'SNOW_INSTALL_FAILED arbitrary\\r\\n'
                elif os.environ['SCENARIO'] == 'install_progress': content += 'SNOW_INSTALL_FAILED firn_install:partition:step_failed\\r\\n'
                elif os.environ['SCENARIO'] == 'install_bad_progress': content += 'SNOW_INSTALL_FAILED firn_install:partition:SECRET-CODE\\r\\n'
                elif os.environ['SCENARIO'] == 'install_secret_code': content += 'SNOW_INSTALL_FAILED firn_install:recovery_key:secret\\r\\n'
                elif os.environ['SCENARIO'] == 'install_fallback': content += 'SNOW_INSTALL_FAILED firn_install:unknown:step_failed\\r\\n'
                elif os.environ['SCENARIO'] == 'install_empty_stream': content += 'SNOW_INSTALL_FAILED firn_install:unknown:empty_stream\\r\\n'
                elif os.environ['SCENARIO'] == 'install_validate_secret': content += 'SNOW_INSTALL_FAILED firn_validate:secret-file\\r\\n'
                elif os.environ['SCENARIO'] == 'install_validate_unknown': content += 'SNOW_INSTALL_FAILED firn_validate:unclassified\\n'
                elif os.environ['SCENARIO'] == 'install_validate_unclassified': content += 'SNOW_INSTALL_FAILED firn_validate:unclassified\\r\\n'
                elif os.environ['SCENARIO'] == 'install_validate_fake': content += 'SNOW_INSTALL_FAILED firn_validate:future-issue\\r\\n'
                elif os.environ['SCENARIO'] in ('install_disk_detect', 'install_disk_byid'):
                    content += 'SNOW_INSTALL_FAILED ' + os.environ['SCENARIO'][8:] + '\\r\\n'
                elif os.environ['SCENARIO'] != 'install_missing': content += 'SNOW_INSTALL_OK\\n'
            else:
                unit = (root / 'unit').read_text()
                phase = re.search(r'printf %%s ([a-z0-9-]+) > /run/snow-credentials/phase', unit).group(1)
                nonce = re.search(r'printf %%s ([0-9a-f]{32}) > /run/snow-credentials/nonce', unit).group(1)
                if os.environ['SCENARIO'] == 'guest_error' and count == 3:
                    content = 'SNOW_QA_ERR bootc_status\\n'
                elif os.environ['SCENARIO'] in ('guest_error_crlf', 'updater_error_crlf') and count == 3:
                    code = 'updater' if os.environ['SCENARIO'] == 'updater_error_crlf' else 'bootc_status'
                    content = f'SNOW_QA_ERR {code}\\r\\n'
                elif os.environ['SCENARIO'] == 'rollback_error_crlf' and count == 5:
                    content = 'SNOW_QA_ERR rollback_action\\r\\n'
                elif os.environ['SCENARIO'] == 'duplicate_error_crlf' and count == 3:
                    content = 'SNOW_QA_ERR bootc_status\\r\\nSNOW_QA_ERR updater\\r\\n'
                elif os.environ['SCENARIO'] == 'conflicting_error_crlf' and count == 3:
                    content = f'SNOW_QA_ERR updater\\r\\nSNOW_QA_V1 {phase} {nonce} {count - 1}\\n'
                elif os.environ['SCENARIO'] == 'phase_malformed' and count == 3:
                    content = 'SNOW_QA_V1 malformed\\n'
                elif (os.environ['SCENARIO'] not in ('phase_missing', 'cumulative_stale', 'stopped_without_record') or count != 3):
                    content = f'SNOW_QA_V1 {phase} {nonce} {count - 1}\\n'
            previous = (root / 'console-log').read_bytes() if (root / 'console-log').exists() else b''
            if os.environ['SCENARIO'] in ('cumulative_reset', 'console_reset_each_start', 'reset_twice', 'reset_then_truncate', 'empty_then_reset') and count >= 2:
                if os.environ['SCENARIO'] != 'cumulative_reset' or count == 3:
                    previous = b''  # Incus 7: a new console log at each start.
            if os.environ['SCENARIO'] == 'installer_restart' and count == 1:
                # The ISO boot restarts QEMU early (firmware reset): the log starts over.
                reads = int((root / 'reads').read_text()) + 1 if (root / 'reads').exists() else 1
                (root / 'reads').write_text(str(reads))
                if reads == 1:
                    sys.stdout.buffer.write(b'firmware banner\\n'); sys.exit(0)
                (root / 'console-log').write_bytes(content.encode())
                (root / 'console-count').write_text(str(count))
                sys.stdout.buffer.write(content.encode()); sys.exit(0)
            sequences = {
                # Incus 7 resets at each start; then the log changes again within the boot.
                'reset_twice': [b'boot-start\\n', b'other\\n' + content.encode()],
                # A valid reset boot, then the stopped VM's log is truncated.
                'reset_then_truncate': [content.encode(), b''],
                # An empty first capture is a reset; later output must extend it.
                'empty_then_reset': [b'', b'data\\n', b'X' + content.encode()],
                # A new log is accepted, then the previous boot's log reappears
                # (with this boot's record, or with an old error marker).
                'reset_then_stale': [b'fresh-banner\\n', b'CHANGED\\n' + previous + content.encode()],
                'reset_then_stale_error': [b'fresh-banner\\n', b'CHANGED\\n' + previous + b'SNOW_QA_ERR bootc_status\\n'],
                # The VM is already stopped; the last output appears only at the final read.
                'final_read_record': [previous, previous + content.encode()],
                'final_read_error': [previous, previous + b'SNOW_QA_ERR updater\\r\\n'],
                'final_read_conflict': [previous, previous + content.encode() + b'SNOW_QA_ERR updater\\r\\n'],
                'final_read_fresh_record': [previous, content.encode()],
            }
            if os.environ['SCENARIO'] in sequences and count == 3:
                reads = int((root / 'reads').read_text()) + 1 if (root / 'reads').exists() else 1
                (root / 'reads').write_text(str(reads))
                sequence = sequences[os.environ['SCENARIO']]
                output = sequence[min(reads, len(sequence)) - 1]
                if reads >= len(sequence) and os.environ['SCENARIO'].startswith('final_read_'):
                    # The stopped VM keeps this log for the next boot's baseline.
                    (root / 'console-log').write_bytes(output)
                    (root / 'console-count').write_text(str(count))
                sys.stdout.buffer.write(output); sys.exit(0)
            if os.environ['SCENARIO'] == 'reset_mid_boot' and count == 3:
                reads = int((root / 'reads').read_text()) + 1 if (root / 'reads').exists() else 1
                (root / 'reads').write_text(str(reads))
                if reads == 1:
                    # This boot first continues the old log, without its record yet ...
                    sys.stdout.buffer.write((root / 'console-log').read_bytes()); sys.exit(0)
                # ... then the log restarts mid-boot: lineage is ambiguous.
                (root / 'console-log').write_bytes(content.encode())
                (root / 'console-count').write_text(str(count))
                sys.stdout.buffer.write(content.encode()); sys.exit(0)
            if os.environ['SCENARIO'] == 'cumulative_prefix' and count == 3:
                previous = b'CHANGED\\n' + previous
            if not (root / 'console-count').exists() or (root / 'console-count').read_text() != str(count):
                (root / 'console-log').write_bytes(previous + content.encode())
                (root / 'console-count').write_text(str(count))
            sys.stdout.buffer.write((root / 'console-log').read_bytes())
        elif args[0] == 'info':
            # The guest powers itself off only after the host verified its record
            # (count 2 is installed-n, the first phase); a stuck guest stays RUNNING.
            count = int((root / 'count').read_text())
            verified = sum(1 for e in (root / 'events').read_text().splitlines() if e.startswith('phase '))
            stuck = os.environ['SCENARIO'] == 'guest_shutdown' and count == 3
            running = stuck or (count >= 2 and verified < count - 1)
            if os.environ['SCENARIO'] in ('stopped_without_record', 'final_read_record', 'final_read_error',
                                          'final_read_conflict', 'final_read_fresh_record') and count == 3:
                running = False   # powered off before the host captured anything
            print('Name: ' + args[1]); print('Status: ' + ('RUNNING' if running else 'STOPPED'))
        elif args[0] == 'delete' and os.environ['SCENARIO'] in ('stage_teardown', 'teardown', 'blocked_teardown'):
            sys.exit(1)
    ''' ).lstrip())
    (tools / 'incus').chmod(0o755)
    (tools / 'curl').write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then shift; case "$1" in *.crt) printf mok-cert > "$1";; *) printf iso-data > "$1";; esac; exit 0; fi; shift; done\nexit 1\n')
    (tools / 'virt-fw-vars').write_text('#!/bin/sh\nprintf "virt-fw-vars %s\\n" "$*" >> "$MOCK_ROOT/events"\nprintf MokList\n')
    for tool in ('curl', 'virt-fw-vars'):
        (tools / tool).chmod(0o755)
    for tool in ('gpg', 'gpgv', 'skopeo', 'cosign', 'mcopy', 'bwrap', 'zstd', 'cpio', 'apt-get', 'pip', 'pip3'):
        binary = tools / tool
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
    (tools / 'apt-get').write_text('#!/bin/sh\nprintf "apt %s\\n" "$1" >> "$MOCK_ROOT/events"\nif [ "$1" = install ]; then case " $* " in *python3-virt-firmware*) : ;; *) exit 1;; esac; fi\nexit 0\n')
    (tools / 'python3').write_text('#!/bin/sh\nif [ "$1" = -m ]; then exit 1; fi\nexec /usr/bin/python3 "$@"\n')
    (tools / 'python3').chmod(0o755)
    m = manifest()
    m['iso_sha256'] = hashlib.sha256(b'iso-data').hexdigest()
    m['mok_cert_sha256'] = hashlib.sha256(b'mok-cert').hexdigest()
    if scenario in ('install_missing', 'install_failed'):
        m['timeouts']['install'] = 300
    if scenario == 'phase_missing':
        m['timeouts']['stage'] = 300
    if scenario in ('guest_error_crlf', 'updater_error_crlf', 'duplicate_error_crlf', 'conflicting_error_crlf'):
        m['timeouts']['stage'] = 300
    if scenario == 'rollback_error_crlf':
        m['timeouts']['reboot'] = 300
    if scenario == 'cumulative_stale':
        m['timeouts']['stage'] = 300
    env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'],
           'MOCK_ROOT': str(tmp_path), 'SCENARIO': scenario,
           'WORKFLOW_NAME': 'mock-run', 'MANIFEST_JSON': json.dumps(m)}
    if scenario in ('stage_keep', 'iso_add_partial_keep', 'iso_add_partial_detach_failed'):
        env['KEEP_VM_ON_FAILURE'] = 'true'
    result = subprocess.run(['bash', str(tmp_path / 'run.sh')], env=env, capture_output=True, timeout=40)
    events = (tmp_path / 'events').read_text().splitlines()
    assert events.index('apt update') < events.index('apt install') < events.index('preflight')
    assert events.index('preflight') < next(i for i, e in enumerate(events) if e.startswith('incus init'))
    assert any(e.startswith('incus delete --force') for e in events) is not (
        scenario in ('stage_keep', 'iso_add_partial_keep'))
    summary_path = tmp_path / 'results/result-summary.txt'
    summary = summary_path.read_text() if summary_path.exists() else (tmp_path / 'evidence/mock-run/result-summary.txt').read_text()
    assert not any('console' in p.name or p.name == 'serial-full'
                   for p in (tmp_path / 'evidence/mock-run').iterdir())
    if scenario in ('pass', 'cumulative_pass', 'fsverity_known', 'cumulative_reset', 'console_reset_each_start',
                    'reset_mid_boot', 'reset_twice', 'reset_then_truncate', 'empty_then_reset', 'installer_restart',
                    'final_read_record', 'final_read_fresh_record'):
        assert not (tmp_path / 'evidence/mock-run/serial-redacted.log').exists()
        assert result.returncode == 0, (summary, result.stderr)
        if scenario == 'fsverity_known':
            # Known upstream defect: recorded with a visible note, never failed.
            assert summary == 'PASS: verified_five_fresh_boots;fsverity_known_defect=7.2.11+deb14-amd64\n'
        else:
            assert summary == 'PASS: verified_five_fresh_boots\n'
        assert len([e for e in events if e.startswith('phase ')]) == 5
        assert len([e for e in events if e.startswith('incus start')]) == 6
        # Only the installer ISO is force-stopped; each phase waits for the guest's own poweroff.
        assert [e for e in events if e.startswith('incus stop')] == ['incus stop snow-qa-mock-run --force']
        # One shutdown check per phase; scenarios that read the console several
        # times in a boot also ask once per extra poll whether the guest stopped.
        infos = len([e for e in events if e.startswith('incus info')])
        assert infos == 5 if scenario in ('pass', 'cumulative_pass', 'fsverity_known') else infos >= 5
        # Incus 7: the pool comes from `incus query`, and the MOK goes into the
        # varstore file that the qemu.nvram symlink names, not the link itself.
        assert 'incus query /1.0/instances/snow-qa-mock-run' in events
        fw = [e for e in events if e.startswith('virt-fw-vars ')]
        assert fw and all('/virtual-machines/snow-qa-mock-run/OVMF_VARS.4MB.ms.fd ' in e for e in fw)
        assert (tmp_path / 'pools/qa-pool/virtual-machines/snow-qa-mock-run/qemu.nvram').is_symlink()
        assert len(set(e.split()[2] for e in events if e.startswith('phase '))) == 5
        assert next(i for i, e in enumerate(events) if e.startswith('incus config device remove')) < next(i for i, e in enumerate(events) if e.startswith('phase installed-n '))
        assert events.index('tag after-stage') > next(i for i, e in enumerate(events) if e.startswith('phase stage '))
        assert events.index('tag after-rollback') > next(i for i, e in enumerate(events) if e.startswith('phase rollback '))
        assert (tmp_path / 'evidence/mock-run/manifest-sha256.txt').stat().st_mode & 0o777 == 0o600
        assert (tmp_path / 'evidence/mock-run').stat().st_mode & 0o777 == 0o700
        assert not (tmp_path / 'snow-qa-mock-run.iso').exists()
        checks = (tmp_path / 'results/checks.txt').read_text().splitlines()
        assert len(checks) == 1 + 9 + 5 + (scenario == 'fsverity_known')  # manifest, tags, attested boots, note
        assert all(re.fullmatch(r'(?:manifest_sha256=[0-9a-f]{64}|tag=[a-z0-9-]+ timestamp=[0-9T:+.\-]+|phase=[a-z0-9-]+ boot_id=[0-9a-f-]+ kernel=[0-9A-Za-z._+~-]+ fsverity_errors=[0-9]+|fsverity_known_defect=7\.2\.11\+deb14-amd64)', line) for line in checks)
        assert ('fsverity_known_defect=7.2.11+deb14-amd64' in checks) is (scenario == 'fsverity_known')
    else:
        if scenario not in ('evidence_write',):
            serial_evidence = tmp_path / 'evidence/mock-run/serial-redacted.log'
            if serial_evidence.exists():
                assert serial_evidence.stat().st_mode & 0o777 == 0o600
                assert serial_evidence.stat().st_size <= 1048576
            assert result.returncode != 0
            assert not summary.startswith('PASS')
            assert len([e for e in events if e.startswith('incus start')]) >= (
                0 if scenario.startswith('iso_add_partial_') else 1 if scenario.startswith('install_') or scenario == 'nvram_escape' else 3)
            if scenario.startswith('iso_add_partial_'):
                assert len([e for e in events if e.startswith('incus config device remove')]) == 1
                assert (tmp_path / 'installer-attached').exists() is (scenario == 'iso_add_partial_detach_failed')
                assert (tmp_path / 'evidence/mock-run/kept-vm.txt').exists() is (scenario == 'iso_add_partial_keep')
                assert (tmp_path / 'snow-qa-mock-run.iso').exists() is False
            if scenario == 'fsverity':
                # Judged only after all five fresh boots, so no phase's evidence is lost.
                assert len([e for e in events if e.startswith('phase ')]) == 5
                recorded = (tmp_path / 'results/checks.txt').read_text()
                assert re.search(r'phase=boot-n-plus-1 boot_id=\S+ kernel=7\.3\.1\+deb14-amd64 fsverity_errors=2\n', recorded)
                assert 'fsverity_known_defect' not in recorded
                assert recorded.count('fsverity_errors=') == 5
            if scenario == 'stage_keep':
                assert len([e for e in events if e.startswith('incus config device remove')]) == 1
                assert not (tmp_path / 'installer-attached').exists()
                assert (tmp_path / 'evidence/mock-run/kept-vm.txt').read_text().startswith('vm=snow-qa-mock-run\n')
            if scenario in ('stage_teardown', 'teardown', 'blocked_teardown'):
                assert (tmp_path / 'snow-qa-mock-run.iso').exists()
            assert summary.strip() == {'stage': 'FAILED: phase_mismatch',
                                    'stage_keep': 'FAILED: phase_mismatch;vm_kept=snow-qa-mock-run',
                                    'iso_add_partial_keep': 'FAILED: vm_iso;vm_kept=snow-qa-mock-run',
                                    'iso_add_partial_detach_failed': 'FAILED: vm_iso;vm_kept_failed',
                                    'no_reboot': 'FAILED: reused_boot_id',
                                    'tag_drift': 'FAILED: tag_mismatch:unparsed',
                                   'evidence_write': 'FAILED: evidence_write',
                                   'output_write': 'FAILED: output_write',
                                    'stage_teardown': 'FAILED: phase_mismatch;teardown_failed;vm_left=snow-qa-mock-run',
                                    'teardown': 'FAILED: teardown_failed;vm_left=snow-qa-mock-run',
                                    'serial_oversize': 'BLOCKED: serial_capture',
                                    'serial_unreadable': 'BLOCKED: serial_capture',
                                    'blocked_teardown': 'FAILED: cleanup_after_blocked:serial_capture;teardown_failed;vm_left=snow-qa-mock-run',
                                    'phase_missing': 'BLOCKED: phase_timeout',
                                     'phase_malformed': 'FAILED: phase_mismatch',
                                      'guest_error': 'BLOCKED: guest_probe_error_observed',
                                     'guest_error_crlf': 'BLOCKED: guest_probe_error_observed',
                                     'updater_error_crlf': 'FAILED: guest_action_failed',
                                     'rollback_error_crlf': 'FAILED: guest_action_failed',
                                     'duplicate_error_crlf': 'FAILED: phase_mismatch',
                                     'conflicting_error_crlf': 'FAILED: phase_mismatch',
                                    'install_missing': 'BLOCKED: phase_timeout',
                                     'install_failed': 'FAILED: install_failed:unparsed',
                                     'install_firn_v2': 'FAILED: install_failed:firn_v2',
                                     'install_firn_hash': 'FAILED: install_failed:firn_hash',
                                      'install_fake_code': 'FAILED: install_failed:unparsed',
                                      'install_progress': 'FAILED: install_failed:firn_install:partition:step_failed',
                                      'install_bad_progress': 'FAILED: install_failed:unparsed',
                                      'install_secret_code': 'FAILED: install_failed:unparsed',
                                      'install_fallback': 'FAILED: install_failed:firn_install:unknown:step_failed',
                                      'install_empty_stream': 'FAILED: install_failed:firn_install:unknown:empty_stream',
                                      'install_validate_secret': 'FAILED: install_failed:firn_validate:secret-file',
                                      'install_validate_unknown': 'FAILED: install_failed:firn_validate:unclassified',
                                      'install_validate_unclassified': 'FAILED: install_failed:firn_validate:unclassified',
                                      'install_validate_fake': 'FAILED: install_failed:unparsed',
                                      'install_disk_detect': 'FAILED: install_failed:disk_detect',
                                      'install_disk_byid': 'FAILED: install_failed:disk_byid',
                                    'cumulative_stale': 'BLOCKED: phase_timeout',
                                    'cumulative_prefix': 'BLOCKED: channel_lineage',
                                    'reset_then_stale': 'BLOCKED: channel_lineage',
                                    'stopped_without_record': 'BLOCKED: guest_stopped_without_record',
                                    'final_read_error': 'FAILED: guest_action_failed',
                                    'final_read_conflict': 'FAILED: phase_mismatch',
                                    'reset_then_stale_error': 'BLOCKED: channel_lineage',
                                    'guest_shutdown': 'FAILED: guest_shutdown',
                                    'nvram_escape': 'BLOCKED: host_storage_unsupported',
                                    'fsverity': 'FAILED: fsverity_errors'}[scenario]


def test_installer_gives_firn_the_canonical_disk_not_the_by_id_link():
    # Firn 0.9.0 appends the partition number to the disk path it gets, so a
    # by-id link makes it format a nonexistent ...root1 (run lnzgv, format-boot).
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    install = script[script.index("cat > \"$WORK/install.sh\""):script.index("\nGUEST\n")]
    assert 'disk = "$disk"' in install and 'disk = "$byid"' not in install
    assert '--confirm "$disk"' in install and '--confirm "$byid"' not in install
    # The by-id discovery still fails closed when no stable link exists.
    assert "SNOW_INSTALL_FAILED disk_byid" in install


@pytest.mark.parametrize("firn_rc,sink", [(0, "ok"), (1, "ok"), (0, "log_fails"), (0, "stream_fails")])
def test_firn_install_has_no_file_size_limit_and_bounded_outputs(tmp_path, firn_rc, sink):
    # RLIMIT_FSIZE on Firn is inherited by podman/bootc: any image blob above
    # it fails with EFBIG (run-snow-bootc-lifecycle-6prlx). The outputs are
    # bounded at their sinks instead, and Firn's exit status survives.
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    install = script[script.index('cat > "$WORK/install.sh"'):script.index("\nGUEST\n")]
    line = next(l for l in install.splitlines() if "firn install " in l and l.lstrip().startswith("if "))
    assert "ulimit" not in line
    bounded = next(l for l in install.splitlines() if l.lstrip().startswith("bounded()"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    (bin_dir / "firn").write_text(
        "#!/bin/bash\n"
        # A 3 MiB 'blob' write, as podman does during the image copy.
        f"head -c 3145728 /dev/zero > {tmp_path}/blob || exit 7\n"
        "for i in $(seq 1 40000); do printf 'Copying blob sha256:%064d\\n' $i >&2; done\n"
        "printf '{\"event\":\"start\",\"steps\":[]}\\n'\n"
        f"touch {tmp_path}/finished\n"
        f"exit {firn_rc}\n")
    (bin_dir / "firn").chmod(0o755)
    body = (bounded.strip() + "\n" + line.strip().replace("/run/", str(run) + "/")
            + "\n  echo OK\nelse\n  echo FAILED\nfi\n")
    if sink != "ok":
        # A sink that cannot be written (here: its path is a directory) must
        # fail the install, even when Firn itself succeeds.
        (run / ("snow-install.log" if sink == "log_fails" else "snow-install.ndjson")).mkdir()
    env = {**os.environ, "PATH": str(bin_dir) + ":" + os.environ["PATH"], "disk": "/dev/example-disk"}
    result = subprocess.run(["bash", "-Eeuo", "pipefail", "-c", "umask 077\n" + body],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("OK" if firn_rc == 0 and sink == "ok" else "FAILED")
    # The producer always runs to completion: no SIGPIPE from a failed sink.
    assert (tmp_path / "blob").stat().st_size == 3145728 and (tmp_path / "finished").exists()
    if sink != "ok":
        return
    log = run / "snow-install.log"
    assert log.stat().st_size == 1048576 and log.stat().st_mode & 0o777 == 0o600
    assert (run / "snow-install.ndjson").read_text() == '{"event":"start","steps":[]}\n'


def test_guest_publish_margin_matches_unit_timeout_and_bounds_the_replay(qa, monkeypatch):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assert f"TimeoutStartSec=$((budget + {qa.PUBLISH_MARGIN}))" in script
    assert qa.PUBLISH_WAIT + qa.PUBLISH_HOLD + 20 <= qa.PUBLISH_MARGIN  # last query, the hold, poweroff
    # A getty that never comes, with slow queries: the whole wait stays bounded,
    # and no query may run past the deadline.
    now = [0.0]
    monkeypatch.setattr(qa.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    timeouts = []

    def slow_query(timeout=10):
        timeouts.append(timeout)
        now[0] += timeout
        return False

    monkeypatch.setattr(qa, "serial_getty_ready", slow_query)
    monkeypatch.setattr(qa, "emit", lambda line: None)
    qa.publish("SNOW_QA_V1 abc")
    assert now[0] <= qa.PUBLISH_WAIT + qa.PUBLISH_HOLD + 1
    assert all(1 <= value <= 10 for value in timeouts)


def test_host_shutdown_wait_includes_the_guest_publish_margin(qa):
    # The host starts its shutdown deadline at the first record; the guest may
    # still replay for up to PUBLISH_WAIT before it requests poweroff.
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assert f"shutdown_end=$((SECONDS + shutdown_budget + {qa.PUBLISH_MARGIN}))" in script
    assert qa.PUBLISH_MARGIN >= qa.PUBLISH_WAIT + qa.PUBLISH_HOLD + 20


@pytest.mark.parametrize("change", [
    lambda p: None,
    lambda p: p["transports"]["docker"].pop("ghcr.io/frostyard/cayo"),   # fewer signed repos is fine
])
def test_guest_policy_accepts_the_shipped_snosi_policy(qa, change):
    policy = json.loads(json.dumps(SHIPPED_POLICY))
    change(policy)
    assert qa.policy_ok(policy)


@pytest.mark.parametrize("change", [
    lambda p: p.__setitem__("default", [{"type": "insecureAcceptAnything"}]),
    lambda p: p["transports"]["docker"].pop(REPO),
    lambda p: p["transports"]["docker"][REPO][0].__setitem__("keyPath", "/etc/other.pub"),
    lambda p: p["transports"]["docker"][REPO][0].pop("signedIdentity"),
    lambda p: p["transports"]["docker"].__setitem__("", [{"type": "insecureAcceptAnything"}]),
    lambda p: p["transports"]["docker"].__setitem__("docker.io/library/debian", [{"type": "insecureAcceptAnything"}]),
    lambda p: p["transports"]["docker"].__setitem__("ghcr.io/frostyard/x", [{"type": "insecureAcceptAnything"}]),
    lambda p: p["transports"].__setitem__("docker-daemon", {"": [{"type": "insecureAcceptAnything"}]}),
    lambda p: p["transports"]["oci"].__setitem__("", [{"type": "reject"}, {"type": "insecureAcceptAnything"}]),
    lambda p: p.__setitem__("extra", True),
])
def test_guest_policy_rejects_weakened_policies(qa, change):
    policy = json.loads(json.dumps(SHIPPED_POLICY))
    change(policy)
    assert not qa.policy_ok(policy)


def test_guest_accepts_both_tpm_pubkey_spellings_and_bootctl_double_slash(qa, monkeypatch, tmp_path):
    # systemd writes "tpm2_pubkey" (observed on Snow); older releases "tpm2-pubkey".
    script = yaml.safe_load(SOURCE.read_text())["data"]["qa.py"]
    assert '("tpm2_pubkey", "tpm2-pubkey")' in script
    assert '["findmnt", "-n", "-o", "FSTYPE", "/sysroot"]' in script
    esp = tmp_path / "esp"
    monkeypatch.setattr(qa, "GUEST_PATHS", {"esp": esp})
    status = ("Secure Boot: enabled (user)\nMeasured UKI: yes\nStub: \u2514\u2500" + UKI_PATH +
              "\nuki: " + str(esp) + "/" + UKI_PATH + "\n").encode()
    assert qa.status_uki_paths(status) == {UKI_PATH.encode()}
    # A doubled slash without the ESP prefix is still not a UKI path token.
    assert qa.status_uki_paths(("uki: x//" + UKI_PATH.lstrip("/") + "\n").encode()) == set()


def test_guest_holds_its_record_for_several_host_polls(qa, monkeypatch):
    # The host reads the console every 5 s and Incus keeps only what it read:
    # the record must stay on the line, repeated, for at least 3 polls.
    now = [0.0]
    monkeypatch.setattr(qa.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(qa, "serial_getty_ready", lambda **_: True)
    emitted = []
    monkeypatch.setattr(qa, "emit", lambda line: emitted.append((now[0], line)))
    qa.publish("SNOW_QA_V1 abc")
    assert qa.PUBLISH_HOLD >= 15
    assert emitted[-1][0] >= qa.PUBLISH_HOLD and len(emitted) >= 4
    assert {line for _, line in emitted} == {"SNOW_QA_V1 abc"}
    gaps = [b[0] - a[0] for a, b in zip(emitted, emitted[1:])]
    assert max(gaps) <= 10


@pytest.mark.parametrize(("prefix", "ok"), [
    ("snow-qa login: ", True),    # observed: the getty prompt has no newline (run fhhk4)
    ("", True),
    ("x", False),                  # never a mid-word marker
])
def test_phase_reads_a_record_after_the_getty_prompt(qa, tmp_path, capsys, prefix, ok):
    normalized, checks = phase_files(tmp_path, qa)
    record = serial_record(phase_record("installed-n"))
    copy = record.split("\n")[1]
    serial = tmp_path / "serial"
    serial.write_text("Debian GNU/Linux 13 snow-qa ttyS0\n\n" + prefix + copy + "\r\n" + copy + "\r\n")
    result = qa.main(["phase", str(normalized), "installed-n", NONCE, "none", str(serial), str(checks)])
    assert result == (0 if ok else 1)
    if not ok:
        assert capsys.readouterr().err.strip() == "qa_failed:record_count"


def test_runner_reads_a_guest_error_after_the_getty_prompt(tmp_path):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    parser = re.search(r"code=\$\(python3 - \"\$WORK/serial\" <<'PY'\n(.*?)\nPY\n", script, re.S)
    assert parser
    path = tmp_path / "parse.py"
    path.write_text(parser.group(1))
    for text, expected in ((b"snow-qa login: SNOW_QA_ERR updater\r\nSNOW_QA_ERR updater\r\n", "updater"),
                           (b"xSNOW_QA_ERR updater\r\nSNOW_QA_ERR updater\r\n", None)):
        serial = tmp_path / "serial"
        serial.write_bytes(text)
        result = subprocess.run(["python3", str(path), str(serial)], capture_output=True, text=True)
        assert (result.stdout.strip() if result.returncode == 0 else None) == expected


def test_record_and_error_readers_share_lf_line_boundaries(qa, tmp_path, capsys):
    # An embedded CR must not end a marker for one reader and not the other.
    normalized, checks = phase_files(tmp_path, qa)
    copy = serial_record(phase_record("installed-n")).split("\n")[1]
    serial = tmp_path / "serial"
    for text, ok in (("snow-qa login: " + copy + "\rjunk\n", False),   # trailing data after a CR
                     (copy + " " + copy + "\n", False),                  # 2 markers on 1 line
                     ("\n" + copy + "\r\r\n" + copy + "\n", True)):   # ONLCR CRs only at the end
        serial.write_text(text)
        assert qa.main(["phase", str(normalized), "installed-n", NONCE, "none", str(serial), str(checks)]) == (0 if ok else 1)
        capsys.readouterr()
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    parser = re.search(r"code=\$\(python3 - \"\$WORK/serial\" <<'PY'\n(.*?)\nPY\n", script, re.S)
    path = tmp_path / "parse.py"
    path.write_text(parser.group(1))
    for text, expected in ((b"snow-qa login: SNOW_QA_ERR updater\rjunk\n", None),
                           (b"SNOW_QA_ERR updater SNOW_QA_ERR updater\n", None),
                           (b"\nSNOW_QA_ERR updater\r\r\nSNOW_QA_ERR updater\n", "updater"),
                           (b"snow-qa login: SNOW_QA_ERR updater\n" + copy.encode() + b"\n", None)):  # error + record
        serial.write_bytes(text)
        result = subprocess.run(["python3", str(path), str(serial)], capture_output=True, text=True)
        assert (result.stdout.strip() if result.returncode == 0 else None) == expected


# ---------------------------------------------------------------------------
# Optional sysext probe: three boots that install N, enable updex features and
# check the merged sysexts across one reboot. N+1 is never staged.
# ---------------------------------------------------------------------------

SYSEXT_BOOT_ID = "87654321-1234-4234-8234-123456789abc"
SYSEXT_PROBE = {"features": ["notesd", "maild"],
                "units": ["frostnotes.service", "frostmail.service"],
                "first_start": [{"unit": "frostmail-secrets-key.service",
                                 "path": "/etc/credstore.encrypted/frostmail-secrets-key"}]}
SECRET_PATH = "/etc/credstore.encrypted/frostmail-secrets-key"
DEV_PLAN = Path(__file__).resolve().parents[1] / "docs/plans/snow-sysext-probe-2026-10-28/manifest.json"


def sysext_manifest():
    m = manifest()
    m["sysext_probe"] = json.loads(json.dumps(SYSEXT_PROBE))
    return m


def sysext_normalized(tmp_path, qa, m=None):
    path = tmp_path / "normalized.json"
    path.write_bytes(qa.canonical(m or sysext_manifest()))
    return path


def sysext_record(phase="sysext-enable", **overrides):
    record = dict(phase=phase, nonce=NONCE, boot_id=SYSEXT_BOOT_ID, booted_digest=N,
                  updex={}, merged={"names": ["maild", "notesd"], "reported": ["maild", "notesd"]},
                  links={"notesd": "notesd.raw", "maild": "maild.raw"}, units={}, first_start={},
                  sysusers={"fragments": [], "users": {}, "groups": {}, "memberships": {}},
                  extra_journal={}, failures=[])
    record.update(overrides)
    return record


def sysext_serial(qa, *records, extra=()):
    lines = ["boot message unrelated"]
    for record in records:
        lines.append("SNOW_SYSEXT_V1 " + qa.sysext_token(record))
    lines.extend(extra)
    return "\n".join(lines) + "\nother serial data\n"


def sysext_payload(line):
    assert line.startswith("SNOW_SYSEXT_V1 ")
    encoded = line.split()[1]
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))))


def test_sysext_dev_plan_normalizes_with_a_distinct_version_rule(qa):
    plan = json.loads(DEV_PLAN.read_text())
    # N is newer than N+1 in the probe plan: only the two versions must differ.
    assert plan["version_n"] > plan["version_n_plus_1"]
    assert qa.normalize(plan) is plan
    assert qa.normalize(plan)["sysext_probe"]["features"] == ["notesd", "maild"]


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra="x"),
    lambda p: p.pop("units"),
    lambda p: p.update(features=[]),
    lambda p: p.update(features=["Notesd"]),
    lambda p: p.update(features=["notesd", "notesd"]),
    lambda p: p.update(features=["feature-%d" % index for index in range(9)]),
    lambda p: p.update(units=["frostmail"]),
    lambda p: p.update(units=["frostmail.service", "frostmail.service"]),
    lambda p: p.update(units=["frostmail.service"] + ["unit%d.service" % index for index in range(16)]),
    lambda p: p.update(first_start=[{"unit": "frostmail-secrets-key.socket", "path": "/etc/x"}]),
    lambda p: p.update(first_start=[{"unit": "key.service", "path": "etc/credstore"}]),
    lambda p: p.update(first_start=[{"unit": "key.service", "path": "/etc/../shadow"}]),
    lambda p: p.update(first_start=[{"unit": "key.service", "path": "/etc/a/a/a/a/a"}]),
    lambda p: p.update(first_start=[{"unit": "key.service", "path": "/etc/x", "mode": "0600"}]),
    lambda p: p.update(first_start=[{"unit": "key%d.service" % index, "path": "/etc/x"} for index in range(5)]),
])
def test_sysext_probe_rejects_unsafe_input(qa, change):
    m = sysext_manifest()
    change(m["sysext_probe"])
    with pytest.raises(qa.GateError, match=r"^[a-z_]+$"):
        qa.normalize(m)


def test_version_rule_follows_the_sysext_mode(qa):
    m = manifest()
    m["version_n"], m["version_n_plus_1"] = "20260924000000", "20260923000000"
    m["version_tag_n"], m["version_tag_n_plus_1"] = f"{REPO}:20260924000000", f"{REPO}:20260923000000"
    with pytest.raises(qa.GateError, match="^version_order$"):
        qa.normalize(m)
    m["sysext_probe"] = json.loads(json.dumps(SYSEXT_PROBE))
    assert qa.normalize(m)["version_n"] == "20260924000000"
    m["version_n_plus_1"] = "20260924000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260924000000"
    with pytest.raises(qa.GateError, match="^version_distinct$"):
        qa.normalize(m)


def test_sysext_phase_accepts_one_record_and_writes_it(qa, tmp_path, capsys):
    normalized = sysext_normalized(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text(sysext_serial(qa, sysext_record()))
    out = tmp_path / "sysext-enable.json"
    assert qa.main(["sysext-phase", str(normalized), "sysext-enable", NONCE, "none",
                    str(serial), str(out)]) == 0
    assert capsys.readouterr().out.splitlines() == [SYSEXT_BOOT_ID, "failures=none"]
    assert out.stat().st_mode & 0o777 == 0o600
    written = json.loads(out.read_text())
    assert written["host_failures"] == [] and written["phase"] == "sysext-enable"


def test_sysext_phase_prints_guest_failures_and_still_writes_the_record(qa, tmp_path, capsys):
    normalized = sysext_normalized(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text(sysext_serial(qa, sysext_record(
        failures=["not_merged:maild", "unit_inactive:frostmail.service"])))
    out = tmp_path / "sysext-enable.json"
    assert qa.main(["sysext-phase", str(normalized), "sysext-enable", NONCE, "none",
                    str(serial), str(out)]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "failures=not_merged:maild,unit_inactive:frostmail.service"
    assert json.loads(out.read_text())["failures"] == ["not_merged:maild", "unit_inactive:frostmail.service"]


@pytest.mark.parametrize("damage,code", [
    ("missing", "record_count"), ("mangled", "record_count"), ("conflict", "record_count"),
    ("nonce", "record_identity"), ("reused", "boot_identity"),
    ("fields", "record_fields"), ("failures", "record_failures"),
    ("newline", "record_failures"), ("plain_manifest", "phase_arguments"),
])
def test_sysext_phase_rejects_untrustworthy_records(qa, tmp_path, capsys, damage, code):
    record = sysext_record()
    phase, prior = "sysext-enable", "none"
    m = None
    if damage == "missing":
        text = "unrelated serial\n"
    elif damage == "mangled":
        text = "unrelated serial\nSNOW_SYSEXT_V1 not-a-token!\n"
    elif damage == "conflict":
        text = sysext_serial(qa, record, dict(record, boot_id="12345678-1234-4234-8234-123456789abc"))
    elif damage == "nonce":
        text = sysext_serial(qa, dict(record, nonce="f" * 32))
    elif damage == "reused":
        text, prior = sysext_serial(qa, record), record["boot_id"]
    elif damage == "fields":
        text = sysext_serial(qa, dict(record, spec_image="ghcr.io/frostyard/snow:latest"))
    elif damage == "failures":
        text = sysext_serial(qa, dict(record, failures="all"))
    elif damage == "newline":
        text = sysext_serial(qa, dict(record, failures=["booted_image\nINJECTED: 1"]))
    else:
        text, m = sysext_serial(qa, record), manifest()
    normalized = sysext_normalized(tmp_path, qa, m)
    serial = tmp_path / "serial"
    serial.write_text(text)
    out = tmp_path / "sysext-enable.json"
    assert qa.main(["sysext-phase", str(normalized), phase, NONCE, prior,
                    str(serial), str(out)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:" + code
    assert not out.exists()


def test_sysext_phase_ignores_a_copy_that_does_not_decode(qa, tmp_path, capsys):
    normalized = sysext_normalized(tmp_path, qa)
    garbage = "SNOW_SYSEXT_V1 " + base64.urlsafe_b64encode(b"not zlib").decode().rstrip("=")
    serial = tmp_path / "serial"
    serial.write_text(sysext_serial(qa, sysext_record(),
                                    extra=["snow-qa login: SNOW_SYSEXT_V1 broken!!", garbage]))
    out = tmp_path / "sysext-enable.json"
    assert qa.main(["sysext-phase", str(normalized), "sysext-enable", NONCE, "none",
                    str(serial), str(out)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == SYSEXT_BOOT_ID


def test_sysext_reboot_adds_host_failures_from_the_enable_record(qa, tmp_path, capsys):
    normalized = sysext_normalized(tmp_path, qa)
    enable = sysext_record("sysext-enable",
                           first_start={"frostmail-secrets-key.service": {"exists": True, "sha256": "a" * 64,
                                                                    "ConditionResult": "yes"}},
                           sysusers={"fragments": ["frostmail.conf"],
                                     "users": {"frostmail": {"present": True, "uid": 990}},
                                     "groups": {"frostmail": {"present": True, "gid": 990}},
                                     "memberships": {}})
    enable_path = tmp_path / "sysext-enable.json"
    enable_path.write_text(json.dumps(enable))
    reboot = sysext_record("sysext-reboot",
                           first_start={"frostmail-secrets-key.service": {"exists": True, "sha256": "b" * 64,
                                                                    "ConditionResult": "no"}},
                           sysusers={"fragments": ["frostmail.conf"],
                                     "users": {"frostmail": {"present": True, "uid": 991}},
                                     "groups": {"frostmail": {"present": True, "gid": 990}},
                                     "memberships": {}})
    serial = tmp_path / "serial"
    serial.write_text(sysext_serial(qa, reboot))
    out = tmp_path / "sysext-reboot.json"
    # The host adds its own codes; run.sh judges, so the command still succeeds.
    assert qa.main(["sysext-phase", str(normalized), "sysext-reboot", NONCE, "none",
                    str(serial), str(out), str(enable_path)]) == 0
    assert capsys.readouterr().out.splitlines()[1] == (
        "failures=first_start_changed:frostmail-secrets-key.service,sysusers_changed:frostmail")
    written = json.loads(out.read_text())
    assert written["host_failures"] == ["first_start_changed:frostmail-secrets-key.service",
                                        "sysusers_changed:frostmail"]
    # An unchanged reboot record adds nothing.
    same = sysext_record("sysext-reboot",
                         first_start={"frostmail-secrets-key.service": {"exists": True, "sha256": "a" * 64,
                                                                        "ConditionResult": "no"}},
                         sysusers=enable["sysusers"])
    serial.write_text(sysext_serial(qa, same))
    assert qa.main(["sysext-phase", str(normalized), "sysext-reboot", NONCE, "none",
                    str(serial), str(out), str(enable_path)]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "failures=none"
    # The key existed after the enable boot: a reboot that runs the unit again is a rerun.
    rerun = sysext_record("sysext-reboot",
                          first_start={"frostmail-secrets-key.service": {"exists": True, "sha256": "a" * 64,
                                                                         "ConditionResult": "yes"}},
                          sysusers=enable["sysusers"])
    serial.write_text(sysext_serial(qa, rerun))
    assert qa.main(["sysext-phase", str(normalized), "sysext-reboot", NONCE, "none",
                    str(serial), str(out), str(enable_path)]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "failures=first_start_rerun:frostmail-secrets-key.service"


def test_sysext_reboot_first_made_after_the_enable_boot_is_no_host_failure(qa, tmp_path, capsys):
    # dev-snow-bootc-lifecycle-mjhb4: accounts absent in the enable boot (no
    # sysusers on a live refresh) and present after the reboot; a key first made
    # in the reboot boot. Neither is a change or a rerun.
    normalized = sysext_normalized(tmp_path, qa)
    enable = sysext_record("sysext-enable",
                           first_start={"frostmail-secrets-key.service": {"exists": False, "sha256": None,
                                                                          "ConditionResult": "no"}},
                           sysusers={"fragments": ["frostmail.conf"],
                                     "users": {"frostmail": {"present": False, "uid": None}},
                                     "groups": {}, "memberships": {}})
    enable_path = tmp_path / "sysext-enable.json"
    enable_path.write_text(json.dumps(enable))
    reboot = sysext_record("sysext-reboot",
                           first_start={"frostmail-secrets-key.service": {"exists": True, "sha256": "c" * 64,
                                                                          "ConditionResult": "yes"}},
                           sysusers={"fragments": ["frostmail.conf"],
                                     "users": {"frostmail": {"present": True, "uid": 974}},
                                     "groups": {}, "memberships": {}})
    serial = tmp_path / "serial"
    serial.write_text(sysext_serial(qa, reboot))
    out = tmp_path / "sysext-reboot.json"
    assert qa.main(["sysext-phase", str(normalized), "sysext-reboot", NONCE, "none",
                    str(serial), str(out), str(enable_path)]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "failures=none"
    assert json.loads(out.read_text())["host_failures"] == []


def test_sysext_probe_unit_orders_after_multi_user_target():
    run = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    assert 'sysext-enable|sysext-reboot) guest_mode=sysext; guest_after=" multi-user.target";;' in run
    assert '*) guest_mode=guest; guest_after="";;' in run
    assert "After=network-online.target${guest_after}" in run
    # Evidence names match the files the reboot comparison and the journal report read.
    assert '"$WORK/$phase.json"' in run and '"$WORK/sysext-enable.json"' in run


SYSEXT_STATES = {
    "frostnotes.service": {"ActiveState": "active", "SubState": "running", "Result": "success", "NRestarts": "0"},
    "frostmail.service": {"ActiveState": "active", "SubState": "running", "Result": "success", "NRestarts": "0"},
    "frostmail-secrets-key.service": {"ActiveState": "active", "Result": "success", "ConditionResult": "no",
                                      "ExecMainStartTimestampMonotonic": "123456", "ExecMainStatus": "0"},
}
SYSEXT_MERGED = {"hierarchies": [{"hierarchy": "/usr", "extensions": [{"name": "notesd"}, {"name": "maild"}]},
                                 {"hierarchy": "/opt", "extensions": [{"name": "foreign"}]}]}
FROSTMAIL_FRAGMENT = ('# created by the extension\n'
                      'u frostmail - "Frostmail account"\n'
                      'g frostmail -\n'
                      'm frostmail frostmail\n')


def sysext_guest(qa, tmp_path, monkeypatch, *, states=None, journal=b"", updex_rc=0,
                 token_max=None, poll_timeout=0):
    """Stand up the sysext collector with every guest command replaced."""
    m = sysext_manifest()
    normalized = tmp_path / "normalized.json"
    normalized.write_bytes(qa.canonical(m))
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    for name, value in {"phase": "sysext-enable", "nonce": NONCE, "image_n": m["image_n"],
                        "image_n_plus_1": m["image_n_plus_1"], "target_ref": m["target_ref"]}.items():
        (credentials / name).write_text(value + "\n")
    boot = tmp_path / "boot_id"
    boot.write_text(SYSEXT_BOOT_ID + "\n")
    state, extensions, sysusers = tmp_path / "snow-qa", tmp_path / "extensions", tmp_path / "sysusers.d"
    extensions.mkdir()
    sysusers.mkdir()
    for feature in SYSEXT_PROBE["features"]:
        (extensions / (feature + ".raw")).write_bytes(b"extension")
    (sysusers / "core.conf").write_text("u core -\n")
    key = tmp_path / SECRET_PATH.lstrip("/")
    key.parent.mkdir(parents=True)
    key.write_bytes(b"public-key-material")
    key.chmod(0o600)
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized)
    monkeypatch.setattr(qa, "GUEST_PATHS", {**qa.GUEST_PATHS, "boot_id": boot})
    monkeypatch.setattr(qa, "SYSEXT_STATE", state)
    monkeypatch.setattr(qa, "SYSEXT_EXTENSIONS", extensions)
    monkeypatch.setattr(qa, "SYSUSERS_DIR", sysusers)
    monkeypatch.setattr(qa, "SYSEXT_ROOT", tmp_path)
    monkeypatch.setattr(qa, "SYSEXT_POLL_TIMEOUT", poll_timeout)
    if token_max is not None:
        monkeypatch.setattr(qa, "SYSEXT_TOKEN_MAX", token_max)
    unit_states = dict(SYSEXT_STATES if states is None else states)
    status = {"spec": {"image": {"image": m["target_ref"], "transport": "registry"}},
              "status": {"booted": {"image": {"imageDigest": "sha256:" + N, "version": m["version_n"]}},
                         "staged": None, "rollback": None}}
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs.get("capture_output") and not kwargs.get("shell")

        def done(code, out=b"", err=b""):
            return subprocess.CompletedProcess(args, code, out, err)

        if args[:2] == ["bootc", "status"]:
            return done(0, json.dumps(status).encode())
        if args[:3] == ["updex", "features", "enable"]:
            (sysusers / "frostmail.conf").write_text(FROSTMAIL_FRAGMENT)
            return done(updex_rc, b"enabled extension\n")
        if args[0] == "systemctl":
            properties = args[2].split("=", 1)[1]
            values = unit_states.get(args[-1], {})
            return done(0, "".join(key + "=" + values.get(key, "") + "\n"
                                   for key in properties.split(",")).encode())
        if args[0] == "journalctl":
            return done(0, journal)
        if args[:2] == ["systemd-sysext", "status"]:
            return done(0, json.dumps(SYSEXT_MERGED).encode())
        if args[:3] == ["getent", "passwd", "frostmail"]:
            return done(0, b"frostmail:x:990:990::/:/usr/sbin/nologin\n")
        if args[:3] == ["getent", "group", "frostmail"]:
            return done(0, b"frostmail:x:990:\n")
        if args[:3] == ["id", "-nG", "frostmail"]:
            return done(0, b"frostmail\n")
        pytest.fail(f"unexpected guest tool {args}")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)
    return credentials, calls, state, key


def test_guest_sysext_collects_a_passing_record(qa, tmp_path, monkeypatch):
    credentials, calls, state, key = sysext_guest(qa, tmp_path, monkeypatch)
    record = sysext_payload(qa.sysext(credentials))
    assert set(record) == qa.SYSEXT_RECORD_FIELDS
    assert record["failures"] == []
    assert record["boot_id"] == SYSEXT_BOOT_ID and record["booted_digest"] == N
    # /usr only: the /opt hierarchy is not what the image ships.
    assert record["merged"] == {"names": ["maild", "notesd"], "reported": ["maild", "notesd"]}
    assert record["links"] == {"notesd": "notesd.raw", "maild": "maild.raw"}
    assert record["units"]["frostnotes.service"]["ActiveState"] == "active"
    assert record["units"]["frostnotes.service"]["journal"] == []
    assert record["updex"]["notesd"] == {"rc": 0, "output": ["enabled extension"]}
    assert record["sysusers"]["fragments"] == ["frostmail.conf"]
    assert record["sysusers"]["users"]["frostmail"] == {"present": True, "uid": 990}
    assert record["sysusers"]["groups"]["frostmail"] == {"present": True, "gid": 990}
    assert record["sysusers"]["memberships"] == {"frostmail:frostmail": True}
    item = record["first_start"]["frostmail-secrets-key.service"]
    assert item["exists"] is True and item["size"] == len(b"public-key-material")
    assert item["mode"] == "0600" and item["sha256"] == hashlib.sha256(b"public-key-material").hexdigest()
    assert item["ConditionResult"] == "no" and item["ExecMainStartTimestampMonotonic"] == "123456"
    # The "before" list is recorded before any action, in a 0700 directory.
    assert json.loads((state / "sysusers-before.json").read_text()) == ["core.conf"]
    assert state.stat().st_mode & 0o777 == 0o700
    assert ["systemd-sysext", "status", "--json=short"] in calls
    assert ["updex", "features", "enable", "maild", "--now"] in calls
    assert "truncated" not in record


def test_guest_sysext_reports_an_inactive_unit_with_its_journal(qa, tmp_path, monkeypatch):
    states = json.loads(json.dumps(SYSEXT_STATES))
    states["frostmail.service"]["ActiveState"] = "failed"
    journal = (b"Jan 01 00:00:01 snow systemd[1]: Starting frostmail...\n"
               b"Jan 01 00:00:02 snow frostmail[42]: token=aabbccddeeff00112233445566778899aabbccdd\n"
               b"Jan 01 00:00:03 snow frostmail[42]: \x1b[31mfailed\x07 to start\n")
    credentials, _, _, _ = sysext_guest(qa, tmp_path, monkeypatch, states=states, journal=journal)
    record = sysext_payload(qa.sysext(credentials))
    assert record["failures"] == ["unit_inactive:frostmail.service"]
    lines = record["units"]["frostmail.service"]["journal"]
    assert lines[0] == "Jan 01 00:00:01 snow systemd[1]: Starting frostmail..."
    assert lines[1].endswith("token=[REDACTED]")
    assert lines[2] == "Jan 01 00:00:03 snow frostmail[42]: [31mfailed to start"
    assert record["units"]["frostnotes.service"]["journal"] == []


def test_guest_sysext_drops_the_oldest_lines_until_the_token_fits(qa, tmp_path, monkeypatch):
    seed, lines = b"journal", []

    def blob(width=240, count=40):
        nonlocal seed
        out = []
        for _ in range(count):
            chunk = b""
            while len(chunk) < width:
                seed = hashlib.sha256(seed).digest()
                chunk += base64.b64encode(seed)
            out.append(chunk[:width].decode())
        return ("\n".join(out) + "\n").encode()

    credentials, _, _, _ = sysext_guest(qa, tmp_path, monkeypatch, journal=blob(), token_max=2000)
    line = qa.sysext(credentials)
    encoded = line.split(" ", 1)[1]
    assert len(encoded) <= 2000
    record = sysext_payload(line)
    assert record["truncated"] is True
    assert sum(len(lines) for lines in (record["units"][unit]["journal"]
                                        for unit in ("frostnotes.service", "frostmail.service"))) < 40


def test_guest_sysext_fatal_error_publishes_the_error_marker(qa, tmp_path, monkeypatch):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "phase").write_text("stage\n")      # not a sysext phase
    (credentials / "nonce").write_text(NONCE + "\n")
    ttys0 = tmp_path / "ttyS0"
    monkeypatch.setattr(qa, "GUEST_CONSOLES", (str(ttys0),))
    monkeypatch.setattr(qa, "PUBLISH_WAIT", 0)
    monkeypatch.setattr(qa, "PUBLISH_HOLD", 0)
    monkeypatch.setattr(qa, "serial_getty_ready", lambda **_: True)
    assert qa.main(["sysext", str(credentials)]) == 1
    assert "SNOW_SYSEXT_ERR credential" in ttys0.read_text()


def test_run_script_selects_three_sysext_boots_from_the_manifest(tmp_path):
    script = run_script()
    block = re.search(r"(?ms)^sysext_mode=.*?^fi\n", script).group()
    assert "phase_plan=(installed-n sysext-enable sysext-reboot)" in block
    for name, expected in (("plain", "installed-n stage boot-n-plus-1 rollback boot-n"),
                           ("sysext", "installed-n sysext-enable sysext-reboot")):
        work = tmp_path / name
        work.mkdir()
        (work / "normalized.json").write_text(json.dumps({"sysext_probe": {}} if name == "sysext" else {}))
        result = subprocess.run(["bash", "-c",
                                 'fail() { exit 9; }; WORK="$PWD"\n' + block +
                                 '\nprintf "%s\\n" "${phase_plan[*]}"'],
                                cwd=work, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


def test_run_script_wires_the_sysext_boots_into_the_shared_body():
    script = run_script()
    loop = phase_loop(script)
    assert loop.startswith('for phase in "${phase_plan[@]}"; do')
    assert 'await SNOW_SYSEXT_V1 "$((budget + 300))" SNOW_SYSEXT_ERR' in loop
    assert 'qa.py sysext-phase' in loop
    assert 'persist "$WORK/$phase.json" "$EVIDENCE/$phase.json"' in loop
    assert "sysext_failures=%s" in loop
    assert 'sysext-enable|sysext-reboot) guest_mode=sysext; guest_after=" multi-user.target";;' in loop
    assert 'qa.py ${guest_mode:-guest} /run/snow-credentials' in loop
    # The sysext phases keep the shared drop-in, the bounded wait and no force stop.
    assert loop.count("raw.qemu=") == 1 and "incus stop" not in loop and "--force" not in loop
    assert "until vm_stopped" in loop
    # Failures are collected, then judged once, and the reason stays bounded.
    assert 'sysext_failures+="${sysext_failures:+,}$sysext_state"' in script
    assert 'fail "${reason:0:400}"' in script
    assert "REASON=verified_sysext_probe" in script and "REASON=verified_five_fresh_boots" in script
    assert 'fail "sysext_guest_error:$code"' in script
    assert "SNOW_SYSEXT_ERR ([a-z_]{1,40})" in script


def test_qa_payload_stays_below_the_smbios_credential_bound():
    payload = yaml.safe_load(SOURCE.read_text())["data"]["qa.py"]
    assert len(base64.b64encode(zlib.compress(payload.encode(), 9))) < 45000


def test_sysext_reboot_host_codes_come_only_from_manifest_units_and_valid_names(qa, tmp_path, capsys):
    # Cross-review of lab#163: record keys are guest data and must never shape a code.
    normalized = sysext_normalized(tmp_path, qa)
    bad = "x;result=PASS"
    enable = sysext_record("sysext-enable",
                           first_start={"evil unit,PASS": {"exists": True, "sha256": "a" * 64,
                                                            "ConditionResult": "yes"}},
                           sysusers={"fragments": [], "users": {bad: {"present": True, "uid": 1}},
                                     "groups": {}, "memberships": {}})
    enable_path = tmp_path / "sysext-enable.json"
    enable_path.write_text(json.dumps(enable))
    reboot = sysext_record("sysext-reboot",
                           first_start={"evil unit,PASS": {"exists": True, "sha256": "b" * 64,
                                                            "ConditionResult": "yes"}},
                           sysusers={"fragments": [], "users": {bad: {"present": True, "uid": 2}},
                                     "groups": {}, "memberships": {}})
    serial = tmp_path / "serial"
    serial.write_text(sysext_serial(qa, reboot))
    out = tmp_path / "sysext-reboot.json"
    assert qa.main(["sysext-phase", str(normalized), "sysext-reboot", NONCE, "none",
                    str(serial), str(out), str(enable_path)]) == 0
    printed = capsys.readouterr().out
    assert printed.splitlines()[1] == "failures=none"
    assert "PASS" not in printed and ";" not in printed
    assert json.loads(out.read_text())["host_failures"] == []


def journal_printer():
    run = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    start = run.index('python3 - "$WORK/normalized.json" "$WORK/sysext-enable.json"')
    body = run[run.index("<<'PY'", start):]
    body = body[body.index("\n") + 1:]
    return body[:body.index("\nPY\n")]


def test_sysext_journal_printer_resanitizes_guest_lines(qa, tmp_path):
    # Cross-review of lab#163: the pod log must not carry raw guest text.
    normalized = sysext_normalized(tmp_path, qa)
    secret = "0123456789abcdef" * 4
    hostile = {"failures": ["unit_inactive:frostmail.service", "unit_inactive:other.service"],
               "host_failures": [],
               "units": {"frostmail.service": {"journal": ["line\nresult=PASS\x1b[2J " + secret, 7]},
                         "other.service": {"journal": ["not in the manifest"]}},
               "first_start": {}}
    enable = tmp_path / "sysext-enable.json"
    enable.write_text(json.dumps(hostile))
    script = tmp_path / "printer.py"
    script.write_text(journal_printer())
    result = subprocess.run([sys.executable, str(script), str(normalized), str(enable),
                             str(tmp_path / "missing.json")],
                            capture_output=True, text=True, timeout=60, check=True)
    lines = result.stdout.splitlines()
    assert lines == ["sysext_journal unit=frostmail.service lineresult=PASS[2J [REDACTED]"]
    assert "not in the manifest" not in result.stdout
