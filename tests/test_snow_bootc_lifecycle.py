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


def fake_inspector(args, m, failure, member_prefix="./", duplicate_members=False, catalog_data=None, **kwargs):
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
                   "io.snosi.bootc.secureboot-assembly": "bootc-1.16.8-storage-digest-v1",
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
    assert data["firn_compatibility"] == "installer-vm-v1-recipe-validator-only"
    qa.check_state(m, data)


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
             "firn_compatibility": "installer-vm-v1-recipe-validator-only",
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
NONCE = "0123456789abcdef0123456789abcdef"
UKI_PATH = "/EFI/Linux/bootc/bootc_composefs-" + "d" * 128 + ".efi"
BLOCKS = {"blockdevices": [{"path": "/dev/vda3", "pkname": "/dev/vda", "partn": "3", "fstype": "crypto_LUKS"},
                           {"path": "/dev/vda2", "pkname": "/dev/vda", "partn": "2", "fstype": "vfat"}]}


def phase_record(phase):
    newer = phase in ("boot-n-plus-1", "rollback")
    record = dict(phase=phase, nonce=NONCE, boot_id="12345678-1234-4234-8234-123456789abc",
                  spec_image=f"{REPO}:qa-controlled" if phase != "installed-n" else f"{REPO}@sha256:{N}",
                  spec_transport="registry", booted_digest=NEXT if newer else N,
                  booted_version="20260924000000" if newer else "20260923000000",
                  staged_digest=NEXT if phase == "stage" else None,
                   rollback_digest=N if phase in ("boot-n-plus-1", "rollback") else NEXT if phase == "boot-n" else None,
                  policy_sha256="2" * 64, policy_ok=True, pull_ok=True,
                  pull_digest=NEXT, secure_boot=True, uki_type2=True, lockdown=True,
                   root_luks=True, root_btrfs=True, tpm_pcr11_tokens=1,
                   action=None, marker_digest=None)
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
        firn_compatibility="installer-vm-v1-recipe-validator-only", index_key_sha256="1" * 64,
        cosign_key_sha256="2" * 64, mok_cert_sha256="3" * 64, tags={})))
    return normalized, checks


@pytest.mark.parametrize("phase", PHASES)
def test_phase_accepts_one_attested_fresh_boot(qa, tmp_path, capsys, phase):
    normalized, checks = phase_files(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text(serial_record(phase_record(phase)))
    assert qa.main(["phase", str(normalized), phase, NONCE,
                    "none", str(serial), str(checks)]) == 0
    assert capsys.readouterr().out.strip() == "12345678-1234-4234-8234-123456789abc"


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
        text += serial_record(record)
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


@pytest.mark.parametrize("phase", ["stage", "rollback"])
def test_guest_stage_probes_and_emits_allowlisted_single_record(qa, tmp_path, monkeypatch, phase):
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
    status = {"spec": {"image": {"image": f"{REPO}@sha256:{N}" if phase == "stage" else f"{REPO}:qa-controlled", "transport": "registry"}},
              "status": {"booted": {"image": {"imageDigest": "sha256:" + (N if phase == "stage" else NEXT),
                                              "version": "20260923000000" if phase == "stage" else "20260924000000"}},
                         "staged": None, "rollback": None if phase == "stage" else {"image": {"imageDigest": "sha256:" + N}}}}
    root = tmp_path / "root"
    root.mkdir()
    (root / "boot_id").write_text("12345678-1234-4234-8234-123456789abc\n")
    (root / "policy.json").write_text(json.dumps({"default": [{"type": "reject"}], "transports": {"docker": {REPO: [{"type": "sigstoreSigned", "keyPath": "/usr/lib/snosi/cosign.pub"}]}}}))
    (root / "key").write_bytes(b"public-key")
    (root / "update-check").write_text("outcome=staged\n")
    (root / "update-staged").write_text("sha256:" + NEXT + "\n")
    (root / "tokens.json").write_text(json.dumps({"tokens": {"0": {"type": "systemd-tpm2", "tpm2-pcrs": [], "tpm2_pubkey_pcrs": [11], "tpm2-pubkey": "public"}}}))
    (root / "uptime").write_text("1000.00 500.00\n")
    esp = root / "esp"
    (esp / "loader/entries").mkdir(parents=True)
    (esp / UKI_PATH.lstrip("/")).parent.mkdir(parents=True)
    (esp / UKI_PATH.lstrip("/")).write_bytes(b"mock-uki")
    (esp / "loader/entries/snow.conf").write_text("title Snow\nuki " + UKI_PATH + "\n")
    monkeypatch.setattr(qa, "GUEST_PATHS", {"boot_id": root / "boot_id", "policy": root / "policy.json",
        "key": root / "key", "update_check": root / "update-check", "update_staged": root / "update-staged",
        "esp": esp, "uptime": root / "uptime"})
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
            status["spec"]["image"]["image"] = f"{REPO}:qa-controlled"
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
        responses = {"mokutil": b"SecureBoot enabled", "bootctl": ("  Secure Boot: enabled (user)\n  Measured UKI: yes\n  Current: $BOOT" + UKI_PATH + " (on the EFI System Partition)\n  Default: /boot/efi" + UKI_PATH + "\n").encode(),
                      "findmnt": b"btrfs", "cryptsetup": b"/dev/mapper/root is active and is in use.\n  device:   /dev/vda3"}
        if args[0] in responses:
            return subprocess.CompletedProcess(args, 0, responses[args[0]], b"")
        pytest.fail(f"unexpected guest tool {args}")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)
    monkeypatch.setattr(qa, "guest_lockdown", lambda: True)
    line = qa.guest(credentials)
    assert line.startswith("SNOW_QA_V1 ")
    payload = json.loads(base64.urlsafe_b64decode(line.split()[1] + "=="))
    assert payload["action"] == ("staged" if phase == "stage" else "rolled-back")
    assert payload["booted_digest"] == (N if phase == "stage" else NEXT)
    assert payload["rollback_digest"] == (None if phase == "stage" else N)
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
                              str(tmp_path / "run/snow-credentials"), ":")
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
    ("v1", "SNOW_INSTALL_FAILED firn_v1", False),
    ("v2", "SNOW_INSTALL_FAILED firn_v2", False),
    ("v2_other_error", "SNOW_INSTALL_FAILED firn_v2", False),
    ("hash", "SNOW_INSTALL_FAILED firn_hash", False),
    ("disk_detect", "SNOW_INSTALL_FAILED disk_detect", False),
    ("disk_byid", "SNOW_INSTALL_FAILED disk_byid", False),
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
                            'byid="/dev/example-disk"', guest_text, flags=re.S)
        assert 'byid="/dev/example-disk"' in guest_text
    (tmp_path / "run").mkdir()
    (tmp_path / "bin").mkdir()
    firn = tmp_path / "bin/firn"
    firn.write_text("#!/bin/bash\nprintf '%s %s\\n' \"$1\" \"${@: -1}\" >> \"$TEST_ROOT/calls\"\n"
                    "if [[ $1 == validate ]]; then\n"
                    "  if grep -q 'version = 1' \"${@: -1}\"; then [[ $SCENARIO != v1 ]]; exit; fi\n"
                    "  [[ $SCENARIO == v2 ]] && exit 0\n"
                    "  [[ $SCENARIO == v2_other_error ]] && { printf 'other-error\\n' >&2; exit 1; }\n"
                    "  printf 'bad-version\\n' >&2; exit 1\n"
                    "fi\nexit 0\n")
    firn.chmod(0o755)
    sums = tmp_path / "bin/sha256sum"
    sums.write_text("#!/bin/bash\nif [[ $SCENARIO == hash ]]; then printf '%064d  %s\\n' 0 \"$1\"; "
                    "else printf '%s  %s\\n' \"$firn_sha256\" \"$1\"; fi\n")
    sums.chmod(0o755)
    (tmp_path / "bin/openssl").write_text("#!/bin/sh\nprintf '0123456789abcdef\\n'\n")
    (tmp_path / "bin/openssl").chmod(0o755)
    guest_text = guest_text.replace("/usr/bin/firn", str(firn))
    result = subprocess.run(["bash", "-e", "-c", guest_text], env=env, capture_output=True, timeout=10)
    assert result.returncode == (0 if installed else 1), result.stderr
    assert (tmp_path / "serial").read_text().splitlines()[-1] == marker
    if scenario != "hash":
        recipe = (tmp_path / "run/snow-firn-v1.toml").read_text()
        assert all(field in recipe for field in ('version = 1', 'disk = "/dev/example-disk"',
                                                 'filesystem = "ext4"', 'encryption = "none"',
                                                 'hostname = "snow-qa"'))
        if scenario != "v1":
            assert (tmp_path / "run/snow-firn-v2.toml").read_text() == recipe.replace("version = 1", "version = 2")
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    assert ("install" in [line.split()[0] for line in calls]) == installed
    if scenario not in ("hash", "v1"):
        assert calls[:2] == [f"validate {tmp_path}/run/snow-firn-v1.toml", f"validate {tmp_path}/run/snow-firn-v2.toml"]


@pytest.mark.parametrize("without_python", [False, True], ids=["python", "shell-fallback"])
@pytest.mark.parametrize(("stream", "expected", "fallback"), [
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"step_start","step":"partition"}\n'
     '{"event":"recovery_key","key":"SECRET-RECOVERY"}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:partition:step_failed", "firn_install:unknown:step_failed"),
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
     '{"event":"error","step":"bogus","code":"step_failed","message":"SECRET-MESSAGE"}\n',
     "firn_install:unlisted:step_failed", "firn_install:unknown:step_failed"),
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
     "firn_install:partition:step_failed", "firn_install:unknown:step_failed"),
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
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"error","step":"partition","code":"step_failed","message":"escaped \\n and \\u00e9"}\n',
     "firn_install:partition:step_failed", "firn_install:unknown:step_failed"),
    pytest.param('{"event":"start","steps":[{"name":"partition"}]}\n'
                 '{"event":"error","step":"partition","code":"step_failed","message":"' + 'x' * 65536 + '"}\n',
                 "firn_install:partition:step_failed", "firn_install:unknown:unparsed", id="oversize-terminal-line"),
    ('{"event":"start","steps":[{"name":"partition"}]}\n'
     '{"event":"step_start","step":"partition"}\n',
     "firn_install:unknown:stream_truncated", "firn_install:unknown:stream_truncated"),
    ('not json\n', "firn_install:unknown:unparsed", "firn_install:unknown:unparsed"),
])
def test_installer_failure_summary_is_bounded_and_secret_free(tmp_path, stream, expected, fallback, without_python):
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
           "PATH": str(bin_dir) + ":" + os.environ["PATH"], "TEST_ROOT": str(tmp_path)}
    subprocess.run(["bash", "-e", "-c", replacement.group()], env=env, check=True)
    guest_text = (work / "install.sh").read_text()
    guest_text = guest_text.replace("/run/", str(tmp_path / "run") + "/")
    guest_text = guest_text.replace("/dev/ttyS0", str(tmp_path / "serial"))
    guest_text = re.sub(r"mapfile -t disks .*?\[\[ -n \"\$byid\" && -b \"\$byid\" \]\] \|\| (?:exit 1|\{.*?exit 1; \})",
                        'byid="/dev/example-disk"', guest_text, flags=re.S)
    assert 'byid="/dev/example-disk"' in guest_text
    firn = bin_dir / "firn"
    firn.write_text('#!/bin/bash\nif [[ $1 == validate ]]; then\n'
                    '  if grep -q "version = 1" "${@: -1}"; then exit 0; fi\n'
                    '  printf "bad-version\\n" >&2; exit 1\nfi\n'
                    '[[ $1 == install && " $* " == *" --json-progress "* ]] || exit 3\n'
                    'cp "$TEST_ROOT/stream" /dev/stdout\nexit 1\n')
    firn.chmod(0o755)
    (tmp_path / "stream").write_text(stream)
    sums = bin_dir / "sha256sum"
    sums.write_text('#!/bin/bash\nprintf "%s  %s\\n" "$firn_sha256" "$1"\n')
    sums.chmod(0o755)
    openssl = bin_dir / "openssl"
    openssl.write_text('#!/bin/sh\nprintf "0123456789abcdef\\n"\n')
    openssl.chmod(0o755)
    guest_text = guest_text.replace("/usr/bin/firn", str(firn))
    if without_python:
        # A command lookup must genuinely fail while bash, grep, sed etc. remain usable.
        env["PATH"] = str(bin_dir)
        for name in ("bash", "grep", "sed", "cp", "chmod", "cat"):
            binary = shutil.which(name)
            if binary:
                (bin_dir / name).symlink_to(binary)
    result = subprocess.run(["/bin/bash", "-e", "-c", guest_text], env=env, capture_output=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert (tmp_path / "serial").read_text() == f"SNOW_INSTALL_FAILED {fallback if without_python else expected}\n"
    assert b"SECRET-RECOVERY" not in (tmp_path / "serial").read_bytes()
    assert b"SECRET-MESSAGE" not in (tmp_path / "serial").read_bytes()
    assert (tmp_path / "run/snow-install.ndjson").read_text() == stream
    assert (tmp_path / "run/snow-install.ndjson").stat().st_mode & 0o777 == 0o600


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
    assert unit.splitlines().count(f'TimeoutStartSec={manifest()["timeouts"][key]}') == 1
    assert "%" not in unit.replace("%%", "")


@pytest.mark.parametrize(("phase", "key"), [
    ("installed-n", "installed_boot"), ("stage", "stage"),
    ("boot-n-plus-1", "reboot"), ("rollback", "reboot"), ("boot-n", "reboot"),
])
def test_host_wait_outlives_guest_unit_budget(tmp_path, phase, key):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    selection = re.search(r'(?m)^  case "\$phase" in installed-n\).*?;; esac', script, re.S)
    assignment = re.search(r'(?m)^  guestunit="\[Unit\].*?^  gb=', script, re.S)
    wait = re.search(r'(?m)^  await SNOW_QA_V1 .*$', script)
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
    assert unit_budget == manifest()["timeouts"][key]
    assert host_wait >= unit_budget + 300


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
    monkeypatch.setattr(qa, "guest", lambda _: (_ for _ in ()).throw(qa.GateError(reason)))
    assert qa.main(["guest", "/irrelevant"]) == 1
    assert console.getvalue() == marker
    assert "SNOW_QA_V1" not in console.getvalue()


@pytest.mark.parametrize("damage", ["policy", "pull_failure", "pull_timeout", "pull_mismatch", "secure_boot", "tpm_unsigned", "tpm_pcrlock", "tpm_wrong_pcrs",
                                    "marker", "outcome", "updater", "updater_timeout", "wrong_boot", "rollback_failure", "rollback_timeout", "pre_spec", "root_device_missing", "uki_other_entry", "uki_unmeasured", "uki_partial", "uki_prefixed", "esp_ambiguous", "esp_missing", "bls_wrong", "bls_linux"])
def test_guest_never_emits_evidence_for_failed_probe_or_action(qa, tmp_path, monkeypatch, damage):
    m = manifest()
    m["cosign_key_sha256"] = hashlib.sha256(b"public-key").hexdigest()
    normalized = tmp_path / "normalized.json"
    normalized.write_bytes(qa.canonical(m))
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized)
    creds = tmp_path / "creds"
    creds.mkdir()
    phase = "rollback" if damage in ("rollback_failure", "rollback_timeout") else "stage"
    for key, value in {"phase": phase, "nonce": NONCE, "image_n": m["image_n"],
                        "image_n_plus_1": m["image_n_plus_1"], "target_ref": m["target_ref"]}.items():
        (creds / key).write_text(value)
    (creds / "unit_started").write_text("1000.00\n")
    (tmp_path / "uptime").write_text("1000.00 50.00\n")
    for key, value in {"boot_id": "12345678-1234-4234-8234-123456789abc",
                       "policy": json.dumps({"default": [{"type": "reject"}], "transports": {"docker": {REPO: [{"type": "sigstoreSigned", "keyPath": "/usr/lib/snosi/cosign.pub"}]}}}),
                       "key": "public-key", "update_check": "outcome=staged\n", "update_staged": "sha256:" + NEXT}.items():
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
        ("boot_id", "policy", "key", "update_check", "update_staged", "uptime")}, "esp": esp})
    monkeypatch.setattr(qa, "guest_lockdown", lambda: True)
    booted = NEXT if phase == "rollback" else N
    version = m["version_n_plus_1"] if phase == "rollback" else m["version_n"]
    status = {"spec": {"image": {"image": m["target_ref"] if phase == "rollback" else m["image_n"], "transport": "registry"}},
              "status": {"booted": {"image": {"imageDigest": "sha256:" + booted, "version": version}},
                             "staged": None,
                         "rollback": {"image": {"imageDigest": "sha256:" + N}} if phase == "rollback" else None}}
    if damage == "pre_spec":
        status["spec"]["image"]["image"] = f"{REPO}:latest"
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
            token = {"type": "systemd-tpm2", "tpm2-pcrs": [], "tpm2_pubkey_pcrs": [11], "tpm2-pubkey": "public"}
            if damage == "tpm_unsigned":
                token.pop("tpm2-pubkey")
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
            status["spec"]["image"]["image"] = m["target_ref"]
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
               "pre_spec": "booted_image", "root_device_missing": "root_luks",
               "uki_other_entry": "uki", "uki_unmeasured": "uki", "uki_partial": "uki", "uki_prefixed": "uki", "esp_ambiguous": "esp_discovery",
               "esp_missing": "esp_discovery", "bls_wrong": "uki_bls", "bls_linux": "uki_bls"}
    with pytest.raises(qa.GateError, match="^" + reasons[damage] + "$"):
        qa.guest(creds)
    if damage in ("pre_spec", "uki_other_entry", "uki_unmeasured", "uki_partial", "uki_prefixed", "esp_ambiguous", "esp_missing", "bls_wrong", "bls_linux", "tpm_unsigned", "tpm_pcrlock", "tpm_wrong_pcrs"):
        assert ["/usr/libexec/bootc-update-stage"] not in calls
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
    assert capsys.readouterr().out.strip() == record["boot_id"]
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
    assert entry["inputs"]["parameters"] == [{"name": "manifest-json"}]
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
    assert entry["inputs"]["parameters"] == [{"name": "manifest-json"}]
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
    assert "no live pass" in quality_section


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
              6 * host_timeout(r"incus stop") + host_timeout(r"incus config show") +
              host_timeout(r"curl") + host_timeout(r"virt-fw-vars --inplace") +
              host_timeout(r"virt-fw-vars -i") + host_timeout(r"incus config device remove") +
              host_timeout(r"incus delete"))
    # Five pre-boot serial baselines, plus one potential last poll per await.
    serial_timeout = re.search(r"(?m)^\s*timeout=(\d+), check=False, preexec_fn=limit", script)
    assert serial_timeout
    serial_polling = (5 + 6) * int(serial_timeout[1])
    worst_case = guest_windows + iso_downloads + preflight + apt + tag_checks + vm_ops + serial_polling
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


@pytest.mark.parametrize("scenario", ["pass", "stage", "no_reboot", "tag_drift", "evidence_write", "output_write", "stage_teardown", "teardown", "serial_oversize", "serial_unreadable", "blocked_teardown", "phase_missing", "phase_malformed", "guest_error", "guest_error_crlf", "updater_error_crlf", "rollback_error_crlf", "duplicate_error_crlf", "conflicting_error_crlf", "install_missing", "install_failed", "install_firn_v1", "install_firn_v2", "install_firn_hash", "install_fake_code", "install_disk_detect", "install_disk_byid", "install_progress", "install_bad_progress", "install_secret_code", "install_fallback", "cumulative_pass", "cumulative_stale", "cumulative_reset", "cumulative_prefix"])
def test_runner_five_boots_and_post_init_failures_are_offline(tmp_path, scenario):
    script = yaml.safe_load(SOURCE.read_text())["data"]["run.sh"]
    for old, new in (("ROOT=/var/lib/snosi-lab/snow-bootc-evidence", f"ROOT={tmp_path}/evidence"),
                     ("OUT=/tmp/results", f"OUT={tmp_path}/results"),
                     ("/var/lib/snosi-lab/iso/snow-qa-", f"{tmp_path}/snow-qa-"),
                     ("/var/lib/incus/storage-pools", str(tmp_path / "pools")),
                     ("/opt/snow-qa/qa.py", str(tmp_path / "qa.py"))):
        script = script.replace(old, new)
    if scenario in ('phase_missing', 'install_missing', 'install_failed', 'cumulative_stale', 'guest_error_crlf', 'updater_error_crlf', 'rollback_error_crlf', 'duplicate_error_crlf', 'conflicting_error_crlf'):
        # Keep the actual await loop; cap its clock only in offline timeout tests.
        assert 'end=$((SECONDS + $2))' in script
        script = script.replace('end=$((SECONDS + $2))', 'end=$((SECONDS + 1))')
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
            if prior != expected or os.environ['SCENARIO'] in ('stage', 'stage_teardown') and phase == 'stage': sys.exit(1)
            if os.environ['SCENARIO'] == 'no_reboot' and phase == 'boot-n-plus-1': print(prior); sys.exit(0)
            print(f'{number:08x}-1234-4234-8234-123456789abc')
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
        if args[0] == 'config' and args[1] == 'show':
            print(json.dumps({'expanded_devices': {'root': {'pool': 'qa-pool', 'type': 'disk', 'path': '/'}}}))
        elif args[0] == 'config' and args[1] == 'set':
            unit = base64.b64decode(re.search(r'service=([A-Za-z0-9+/=]+)', args[-1]).group(1)).decode()
            (root / 'unit').write_text(unit)
        elif args[0] == 'start':
            count = int((root / 'count').read_text()) + 1 if (root / 'count').exists() else 1
            (root / 'count').write_text(str(count))
            nvram = root / 'pools' / 'qa-pool' / 'virtual-machines' / args[1] / 'qemu.nvram'
            nvram.parent.mkdir(parents=True, exist_ok=True); nvram.write_text('nvram')
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
                elif (os.environ['SCENARIO'] not in ('phase_missing', 'cumulative_stale') or count != 3):
                    content = f'SNOW_QA_V1 {phase} {nonce} {count - 1}\\n'
            previous = (root / 'console-log').read_bytes() if (root / 'console-log').exists() else b''
            if os.environ['SCENARIO'] == 'cumulative_reset' and count == 3:
                previous = b''
            if os.environ['SCENARIO'] == 'cumulative_prefix' and count == 3:
                previous = b'CHANGED\\n' + previous
            if not (root / 'console-count').exists() or (root / 'console-count').read_text() != str(count):
                (root / 'console-log').write_bytes(previous + content.encode())
                (root / 'console-count').write_text(str(count))
            sys.stdout.buffer.write((root / 'console-log').read_bytes())
        elif args[0] == 'delete' and os.environ['SCENARIO'] in ('stage_teardown', 'teardown', 'blocked_teardown'):
            sys.exit(1)
    ''' ).lstrip())
    (tools / 'incus').chmod(0o755)
    (tools / 'curl').write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then shift; case "$1" in *.crt) printf mok-cert > "$1";; *) printf iso-data > "$1";; esac; exit 0; fi; shift; done\nexit 1\n')
    (tools / 'virt-fw-vars').write_text('#!/bin/sh\nprintf MokList\n')
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
    result = subprocess.run(['bash', str(tmp_path / 'run.sh')], env=env, capture_output=True, timeout=40)
    events = (tmp_path / 'events').read_text().splitlines()
    assert events.index('apt update') < events.index('apt install') < events.index('preflight')
    assert events.index('preflight') < next(i for i, e in enumerate(events) if e.startswith('incus init'))
    assert any(e.startswith('incus delete --force') for e in events)
    summary_path = tmp_path / 'results/result-summary.txt'
    summary = summary_path.read_text() if summary_path.exists() else (tmp_path / 'evidence/mock-run/result-summary.txt').read_text()
    assert not any('serial' in p.name or 'console' in p.name
                   for p in (tmp_path / 'evidence/mock-run').iterdir())
    if scenario in ('pass', 'cumulative_pass'):
        assert result.returncode == 0, (summary, result.stderr)
        assert summary == 'PASS: verified_five_fresh_boots\n'
        assert len([e for e in events if e.startswith('phase ')]) == 5
        assert len([e for e in events if e.startswith('incus start')]) == 6
        assert len(set(e.split()[2] for e in events if e.startswith('phase '))) == 5
        assert next(i for i, e in enumerate(events) if e.startswith('incus config device remove')) < next(i for i, e in enumerate(events) if e.startswith('phase installed-n '))
        assert events.index('tag after-stage') > next(i for i, e in enumerate(events) if e.startswith('phase stage '))
        assert events.index('tag after-rollback') > next(i for i, e in enumerate(events) if e.startswith('phase rollback '))
        assert (tmp_path / 'evidence/mock-run/manifest-sha256.txt').stat().st_mode & 0o777 == 0o600
        assert (tmp_path / 'evidence/mock-run').stat().st_mode & 0o777 == 0o700
        assert not (tmp_path / 'snow-qa-mock-run.iso').exists()
        checks = (tmp_path / 'results/checks.txt').read_text().splitlines()
        assert len(checks) == 1 + 9 + 5  # manifest, tags, attested boots
        assert all(re.fullmatch(r'(?:manifest_sha256=[0-9a-f]{64}|tag=[a-z0-9-]+ timestamp=[0-9T:+.\-]+|phase=[a-z0-9-]+ boot_id=[0-9a-f-]+)', line) for line in checks)
    else:
        assert result.returncode != 0
        assert not summary.startswith('PASS')
        assert len([e for e in events if e.startswith('incus start')]) >= (1 if scenario.startswith('install_') else 3)
        assert summary.strip() == {'stage': 'FAILED: phase_mismatch',
                                    'no_reboot': 'FAILED: reused_boot_id',
                                    'tag_drift': 'FAILED: tag_mismatch:unparsed',
                                   'evidence_write': 'FAILED: evidence_write',
                                   'output_write': 'FAILED: output_write',
                                   'stage_teardown': 'FAILED: phase_mismatch;teardown_failed',
                                   'teardown': 'FAILED: teardown_failed',
                                    'serial_oversize': 'BLOCKED: serial_capture',
                                    'serial_unreadable': 'BLOCKED: serial_capture',
                                    'blocked_teardown': 'FAILED: cleanup_after_blocked:serial_capture;teardown_failed',
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
                                     'install_firn_v1': 'FAILED: install_failed:firn_v1',
                                     'install_firn_v2': 'FAILED: install_failed:firn_v2',
                                     'install_firn_hash': 'FAILED: install_failed:firn_hash',
                                      'install_fake_code': 'FAILED: install_failed:unparsed',
                                      'install_progress': 'FAILED: install_failed:firn_install:partition:step_failed',
                                      'install_bad_progress': 'FAILED: install_failed:unparsed',
                                      'install_secret_code': 'FAILED: install_failed:unparsed',
                                      'install_fallback': 'FAILED: install_failed:firn_install:unknown:step_failed',
                                      'install_disk_detect': 'FAILED: install_failed:disk_detect',
                                      'install_disk_byid': 'FAILED: install_failed:disk_byid',
                                    'cumulative_stale': 'BLOCKED: phase_timeout',
                                    'cumulative_reset': 'BLOCKED: channel_lineage',
                                    'cumulative_prefix': 'BLOCKED: channel_lineage'}[scenario]
