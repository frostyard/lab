"""The firn install runner accepts only pinned bootc installer media."""

import os
from pathlib import Path
import subprocess
import hashlib
import json
import re

import pytest
import yaml


TEMPLATE = Path(__file__).resolve().parents[1] / "argo/workflow-templates/run-firn-install-tests.yaml"
ISO_BYTES = b"wrong ISO"


@pytest.fixture
def lane():
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    return template["spec"]["templates"][0]


def test_iso_inputs_are_required_and_wired_to_script(lane):
    params = {p["name"]: p for p in lane["inputs"]["parameters"]}
    assert set(params["iso-sha256"]) == {"name"}
    assert set(params["iso-url"]) == {"name"}
    env = {e["name"]: e["value"] for e in lane["script"]["env"]}
    assert env["ISO_SHA256"] == "{{inputs.parameters.iso-sha256}}"
    assert env["ISO_URL"] == "{{inputs.parameters.iso-url}}"
    assert "latest" not in str(params["iso-url"])


def test_recipe_and_checks_are_bootc_only(lane):
    source = lane["script"]["source"]
    params = {p["name"] for p in lane["inputs"]["parameters"]}
    assert "varfs" not in params
    assert "pubring-path" not in params
    assert 'family = "bootc"' in source
    assert 'family = "ab"' not in source
    assert '--pubring' not in source
    # No native A/B dm-verity: the only verity words are the fs-verity (snosi#1130) probe.
    assert 'verity' not in source.replace('fsverity', '').replace('fs-verity', '').replace('_verity_', '')
    assert '-ab"' not in source
    assert 'sha256sum' in source
    assert '${ISO_PATH}' in source
    assert '${ISO_SHA256}' in source
    assert 'trap cleanup EXIT' in source
    assert 'incus delete --force "${VM}"' in source
    # The Secure Boot enrollment still uses the shared snosi certificate.
    assert 'shared/native-ab/keys/mok-2026.crt' in source


def test_installer_reports_firn_version_before_install(lane):
    source = lane["script"]["source"]
    install = source.split("INSTALL_SH=$(cat <<SCRIPT\n", 1)[1].split("\nSCRIPT", 1)[0]
    i = install.index("FIRN_QA__FIRN_VERSION=")
    line = install[install.rindex("\n", 0, i) + 1:install.index("\n", i)]
    assert "firn --version" in line
    assert i < install.index('echo "${M_BEGIN}"')
    assert i < install.index("if firn install")


@pytest.mark.parametrize("fake,want", [
    ("printf 'firn 0.6.0 (abc)\\n'", "FIRN_QA__FIRN_VERSION=firn_0.6.0_(abc)"),
    ("return 1", "FIRN_QA__FIRN_VERSION="),
])
def test_installer_version_line_tokenises(lane, fake, want):
    source = lane["script"]["source"]
    assignment = source[source.index("INSTALL_SH=$(cat <<SCRIPT\n"):source.index("\n)", source.index("INSTALL_SH=$(cat <<SCRIPT\n")) + 2]
    rendered = subprocess.run(
        ["bash", "-c", ('set -euo pipefail\nM_BEGIN=FIRN_QA__INSTALL_BEGIN\n'
                       'M_OK=FIRN_QA__INSTALL_OK\nM_FAIL=FIRN_QA__INSTALL_FAIL\n'
                       'IMAGE=floe\nENC=none\nSECUREBOOT=false\n'
                       + assignment + '\nprintf "%s" "$INSTALL_SH"')],
        capture_output=True, text=True, check=True,
    ).stdout
    line = next(line for line in rendered.splitlines() if 'FIRN_QA__FIRN_VERSION=' in line)
    begin = next(line for line in rendered.splitlines() if 'FIRN_QA__INSTALL_BEGIN' in line)
    assert rendered.index(line) < rendered.index(begin)
    result = subprocess.run(["bash", "-c", f"firn() {{ {fake}; }}\n{line}\n{begin}"],
                            capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [want, "FIRN_QA__INSTALL_BEGIN"]


@pytest.mark.parametrize("console,want", [
    ("noise\nFIRN_QA__FIRN_VERSION=firn_0.6.0\r\nFIRN_QA__BEGIN", "firn_0.6.0"),
    ("FIRN_QA__FIRN_VERSION=\nFIRN_QA__BEGIN", "unknown"),
    ("FIRN_QA__BEGIN", "unknown"),
])
def test_host_captures_installer_version_under_pipefail(lane, console, want):
    source = lane["script"]["source"]
    block = source.split("# --- BEGIN firn-version ---\n", 1)[1].split("# --- END firn-version ---", 1)[0]
    script = ('set -euo pipefail\nconsole_log() { printf "%b\\n" "$CONSOLE"; }\n'
              + block + '\nprintf "%s" "$FIRN_VERSION"')
    result = subprocess.run(["bash", "-c", script], env={**os.environ, "CONSOLE": console},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == want


def test_version_capture_follows_installer_boot_and_precedes_install_wait(lane):
    source = lane["script"]["source"]
    assert source.index('FIRN_VERSION=not_reached') < source.index('fail() {')
    assert source.index('wait_for "${M_BEGIN}"') < source.index('# --- BEGIN firn-version ---')
    assert source.index('# --- END firn-version ---') < source.index('wait_for "${M_OK}"')


@pytest.mark.parametrize("version,step,code,want", [
    ("not_reached", "iso", "sha_mismatch", "FAILED: iso:sha_mismatch (c) firn=not_reached"),
    ("firn_0.6.0", "install", "firn_rc_1", "FAILED: install:firn_rc_1 (c) firn=firn_0.6.0"),
])
def test_fail_records_firn_version(lane, tmp_path, version, step, code, want):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    script = (f'set -euo pipefail\nCELL=c\nFIRN_VERSION={version}\n'
              + fail.replace('/tmp/results', str(tmp_path)) + f'\nfail {step} {code}')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    assert (tmp_path / 'result-summary.txt').read_text() == want


@pytest.mark.parametrize(
    ("family", "digest", "image_digest", "cached", "expected", "downloaded"),
    [
        ("bootc", "0" * 64, "sha256:" + "a" * 64, False, "FAILED: iso:sha256_mismatch", True),
        ("bootc", "", "sha256:" + "a" * 64, False, "FAILED: iso:sha256_param", False),
        ("ab", "0" * 64, "sha256:" + "a" * 64, False, "FAILED: params:family_unsupported", False),
        ("bootc", "0" * 64, "sha256:" + "a" * 64, True, "FAILED: iso:sha256_mismatch", False),
        ("bootc", "0" * 64, "", False, "FAILED: params:image_digest_param", False),
        ("bootc", "0" * 64, "sha256:" + "A" * 64, False, "FAILED: params:image_digest_param", False),
    ],
)
def test_invalid_iso_never_reaches_incus_init(lane, tmp_path, family, digest, image_digest, cached, expected, downloaded):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    block = source.split("# --- BEGIN iso-pin ---\n", 1)[1].split("# --- END iso-pin ---", 1)[0]
    results = tmp_path / "results"
    results.mkdir()
    fail = fail.replace("/tmp/results", str(results))
    block = block.replace("/var/lib/snosi-lab/iso", str(tmp_path / "iso"))
    cache = tmp_path / "iso"
    cache.mkdir()
    iso = cache / "test.iso"
    if cached:
        iso.write_bytes(b"old, incorrect ISO")
    calls = tmp_path / "calls"
    script = (
        'set -euo pipefail\nFIRN_VERSION=not_reached\nCELL="${FAMILY}/floe/none/sb=false"\n'
        f'{fail}\n'
        'curl() {\n'
        '  if [[ "$1" == "-sSLI" ]]; then printf "%s" "https://example.invalid/test.iso"; return; fi\n'
        '  printf "%s\\n" download >> "$CALLS"\n'
        '  while [[ $# -gt 0 ]]; do\n'
        '    if [[ "$1" == "-o" ]]; then printf "%s" "wrong ISO" > "$2"; return; fi\n'
        '    shift\n'
        '  done\n'
        '}\n'
        'incus() { printf "%s\\n" "$*" >> "$CALLS"; }\n'
        f'{block}\nincus init test-vm --empty --vm\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "FAMILY": family, "ISO_SHA256": digest, "IMAGE_DIGEST": image_digest,
             "ISO_URL": "https://example.invalid/test.iso",
             "CALLS": str(calls)},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 1, result.stderr
    assert (results / "result-summary.txt").read_text(encoding="utf-8") == (
        f"{expected} ({family}/floe/none/sb=false) firn=not_reached"
    )
    assert not iso.exists()
    assert not (cache / "test.iso.part").exists()
    assert (calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []) == (
        ["download"] if downloaded else []
    )


@pytest.mark.parametrize("cached", [False, True])
def test_verified_iso_is_promoted_before_vm_creation(lane, tmp_path, cached):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    block = source.split("# --- BEGIN iso-pin ---\n", 1)[1].split("# --- END iso-pin ---", 1)[0]
    cache = tmp_path / "iso"
    cache.mkdir()
    iso = cache / "test.iso"
    if cached:
        iso.write_bytes(ISO_BYTES)
    script = (
        'set -euo pipefail\nCELL="bootc/floe/none/sb=false"\n'
        + fail.replace("/tmp/results", str(tmp_path)) + '\n'
        + 'curl() {\n'
          '  if [[ "$1" == "-sSLI" ]]; then printf "%s" "https://example.invalid/test.iso"; return; fi\n'
          '  printf "%s\\n" download >> "$CALLS"\n'
          '  while [[ $# -gt 0 ]]; do\n'
          '    if [[ "$1" == "-o" ]]; then printf "%s" "wrong ISO" > "$2"; return; fi\n'
          '    shift\n'
          '  done\n'
          '}\n'
          'incus() { printf "%s\\n" "$*" >> "$CALLS"; }\n'
        + block.replace("/var/lib/snosi-lab/iso", str(cache))
        + '\nincus init test-vm --empty --vm\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "FAMILY": "bootc", "ISO_URL": "https://example.invalid/test.iso",
              "ISO_SHA256": hashlib.sha256(ISO_BYTES).hexdigest(), "IMAGE_DIGEST": EXPECTED_DIGEST,
              "CALLS": str(tmp_path / "calls")},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert iso.read_bytes() == ISO_BYTES
    assert not (cache / "test.iso.part").exists()
    assert (tmp_path / "calls").read_text(encoding="utf-8").splitlines() == (
        ([] if cached else ["download"]) + ["init test-vm --empty --vm"]
    )


EXPECTED_DIGEST = "sha256:" + "a" * 64
EXPECTED_IMAGE = "ghcr.io/frostyard/floe:latest"
GOOD_CHECKS = (
    "booted=ok\nrootfs=btrfs\nosrelease=floe\nluks=absent\n"
    f"bootc=bootc_1.0\nlogin=active\nbootc_image={EXPECTED_IMAGE}\n"
    f"bootc_digest={EXPECTED_DIGEST}\n"
    "cfs_compressed_objects=0\nfsverity_errors=0\nkernel=7.3.1+deb14-amd64\nkernel_btrfs_verity_canary=clean\n"
)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        ({"login_unit": "none"}, f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        ({"rootfs": "overlay", "sysroot": "btrfs"},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        ({"rootfs": "overlay", "sysroot": "ext4"}, "FAILED: boot:rootfs_mismatch (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay"}, "FAILED: boot:rootfs_mismatch (bootc/floe/none/sb=false)"),
        ({"bootc_image": "unknown"}, "FAILED: boot:bootc_image_unknown (bootc/floe/none/sb=false)"),
        ({"bootc_digest": "sha256:" + "b" * 64}, "FAILED: boot:digest_mismatch (bootc/floe/none/sb=false)"),
        ({"login": "inactive"}, "FAILED: boot:login_unavailable (bootc/floe/none/sb=false)"),
        ({"osrelease": "snow"}, "FAILED: boot:wrong_image (bootc/floe/none/sb=false)"),
        ({"bootc": "absent"}, "FAILED: boot:bootc_absent (bootc/floe/none/sb=false)"),
        ({"bootc_digest": None}, "FAILED: boot:bootc_digest_unknown (bootc/floe/none/sb=false)"),
        ({"bootc_image": None}, "FAILED: boot:bootc_image_unknown (bootc/floe/none/sb=false)"),
        ({"login": None}, "FAILED: boot:login_unavailable (bootc/floe/none/sb=false)"),
        ({"booted": None}, "FAILED: boot:no_report (bootc/floe/none/sb=false)"),
        ({"osrelease": "snow", "login": "inactive"}, "FAILED: boot:wrong_image (bootc/floe/none/sb=false)"),
        # snosi#1130: compressed composefs objects (firn#119) and fs-verity kernel errors.
        ({"cfs_compressed_objects": "3"}, "FAILED: boot:cfs_compressed_objects_3 (bootc/floe/none/sb=false)"),
        ({"cfs_compressed_objects": None}, "FAILED: boot:cfs_check_missing (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "unknown"},
         "FAILED: boot:cfs_check_unknown (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "0"},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        ({"cfs_compressed_objects": "unknown"},  # ostree-style btrfs root has no composefs store
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        ({"cfs_compressed_objects": "n/a"},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        # An attested composefs root (overlay + btrfs /sysroot) needs the number 0.
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "n/a"},
         "FAILED: boot:cfs_check_unknown (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "garbage"},
         "FAILED: boot:cfs_check_unknown (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "-1"},
         "FAILED: boot:cfs_check_unknown (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "1x"},
         "FAILED: boot:cfs_check_unknown (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": None},
         "FAILED: boot:cfs_check_missing (bootc/floe/none/sb=false)"),
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "4"},
         "FAILED: boot:cfs_compressed_objects_4 (bootc/floe/none/sb=false)"),
        ({"fsverity_errors": "7"}, "FAILED: boot:fsverity_errors_7 (bootc/floe/none/sb=false)"),
        # Known upstream defect kernels (snosi#1130): errors are recorded with a note, not failed.
        ({"fsverity_errors": "7", "kernel": "7.2.11+deb14-amd64"},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST} fsverity_known_defect=7.2.11+deb14-amd64"),
        ({"fsverity_errors": "1", "kernel": "7.2.12+deb14-amd64"},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST} fsverity_known_defect=7.2.12+deb14-amd64"),
        ({"fsverity_errors": "0", "kernel": "7.2.11+deb14-amd64"},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        # Any other kernel with errors fails, as does an unknown, missing or look-alike kernel.
        ({"fsverity_errors": "7", "kernel": "7.2.13+deb14-amd64"}, "FAILED: boot:fsverity_errors_7 (bootc/floe/none/sb=false)"),
        ({"fsverity_errors": "7", "kernel": "7.2.11+deb14-amd64-extra"}, "FAILED: boot:fsverity_errors_7 (bootc/floe/none/sb=false)"),
        ({"fsverity_errors": "7", "kernel": "unknown"}, "FAILED: boot:fsverity_errors_7 (bootc/floe/none/sb=false)"),
        ({"fsverity_errors": "7", "kernel": None}, "FAILED: boot:fsverity_kernel_ambiguous (bootc/floe/none/sb=false)"),
        # The false-pass fixes stay, also on a known-defect kernel.
        ({"fsverity_errors": "unknown", "kernel": "7.2.11+deb14-amd64"}, "FAILED: boot:fsverity_errors_unknown (bootc/floe/none/sb=false)"),
        ({"fsverity_errors": None, "kernel": "7.2.11+deb14-amd64"}, "FAILED: boot:fsverity_errors_unknown (bootc/floe/none/sb=false)"),
        # The compressed-object check is deterministic: no kernel excuses it.
        ({"rootfs": "overlay", "sysroot": "btrfs", "cfs_compressed_objects": "2", "kernel": "7.2.11+deb14-amd64"},
         "FAILED: boot:cfs_compressed_objects_2 (bootc/floe/none/sb=false)"),
        ({"fsverity_errors": "unknown"}, "FAILED: boot:fsverity_errors_unknown (bootc/floe/none/sb=false)"),
        ({"fsverity_errors": None}, "FAILED: boot:fsverity_errors_unknown (bootc/floe/none/sb=false)"),
        ({"cfs_compressed_objects": "2", "fsverity_errors": "7"},
         "FAILED: boot:cfs_compressed_objects_2 (bootc/floe/none/sb=false)"),
        # The canary is informational: a defective kernel alone never fails the lane.
        ({"kernel_btrfs_verity_canary": "defect"},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
        ({"kernel_btrfs_verity_canary": None, "kernel": None},
         f"PASS: bootc/floe/none/sb=false image={EXPECTED_IMAGE} digest={EXPECTED_DIGEST}"),
    ],
)
def test_judge_checks_under_errexit(lane, tmp_path, change, expected):
    _judge_case(lane, tmp_path, change, expected)


def _judge_case(lane, tmp_path, change, expected):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    # Source just the declared function, not VM setup or package installation.
    judge = source[source.index("judge() {"):source.index("\n}", source.index("judge() {")) + 2]
    values = dict(line.split("=", 1) for line in GOOD_CHECKS.splitlines())
    values.update(change)
    checks = tmp_path / "checks.txt"
    checks.write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nFIRN_VERSION=firn_0.6.0\n" + fail.replace("/tmp/results", str(tmp_path))
         + "\n" + judge.replace("/tmp/results", str(tmp_path)) + "\njudge \"$CHECKS\""],
        env={**os.environ, "CELL": "bootc/floe/none/sb=false", "IMAGE": "floe", "ENC": "none",
             "IMAGE_DIGEST": EXPECTED_DIGEST, "CHECKS": str(checks)},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == (0 if expected.startswith("PASS:") else 1), result.stderr
    assert (tmp_path / "result-summary.txt").read_text() == expected + " firn=firn_0.6.0"


@pytest.mark.parametrize("status,expected_image,expected_digest", [
    (json.dumps({"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost",
                 "spec": {"image": {"image": "ghcr.io/frostyard/other:latest", "transport": "registry"}},
                 "status": {"booted": {"image": {"image": {"image": EXPECTED_IMAGE,
                                                               "transport": "registry"},
                                                      "version": "1.0", "timestamp": "2026-10-01T00:00:00Z",
                                                      "imageDigest": EXPECTED_DIGEST}}}}), EXPECTED_IMAGE, EXPECTED_DIGEST),
    (json.dumps({"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost",
                 "spec": {"image": {"image": EXPECTED_IMAGE, "transport": "registry"}},
                 "status": {"booted": {"image": {"imageDigest": EXPECTED_DIGEST}}}}), EXPECTED_IMAGE, EXPECTED_DIGEST),
    (json.dumps({"spec": {"image": {"image": EXPECTED_IMAGE}},
                 "status": {"booted": {"image": {"image": {"image": ""},
                                                      "imageDigest": EXPECTED_DIGEST}}}}), EXPECTED_IMAGE, EXPECTED_DIGEST),
    ("not JSON", "unknown", "unknown"),
    ("{}", "unknown", "unknown"),
])
@pytest.mark.parametrize("bootc_present", [True, False])
def test_guest_checks_report_bootc_status_sysroot_and_missing_binary(lane, tmp_path, status, expected_image, expected_digest, bootc_present):
    source = lane["script"]["source"]
    creation = source[source.index("CHECK_SH=$(cat <<SCRIPT"):source.index("\nC_B64=", source.index("CHECK_SH=$(cat <<SCRIPT"))]
    script = """set -euo pipefail
M_CHECK=FIRN_QA__CHECK; M_DONE=FIRN_QA__CHECKS_DONE
IMAGE=floe
systemctl() { if [[ "$1" == show ]]; then printf 0; else printf active; fi; }
timeout() { shift; "$@"; }
sleep() { SECONDS=$((SECONDS + $1)); }
sync() { return 0; }
findmnt() { if [[ "${@: -1}" == /sysroot ]]; then printf btrfs; else printf overlay; fi; }
lsblk() { return 0; }
journalctl() { printf 'kernel: ok\n'; }
dmesg() { return 0; }
id() { echo 1000; }
bootc() { if [[ "$1" == --version ]]; then printf 'bootc 1.0'; else printf '%s' "$STATUS"; fi; }
""" + ("" if bootc_present else 'command() { if [[ "$2" == bootc ]]; then return 1; fi; builtin command "$@"; }\n') + creation + "\n" + 'printf "%s" "$CHECK_SH"'
    generated = subprocess.run(["bash", "-c", script], env={**os.environ, "STATUS": status},
                               capture_output=True, text=True, check=True).stdout
    # Keep the real guest paths out of the test run.
    generated = generated.replace("/dev/ttyS0", str(tmp_path / "ttyS0")).replace(
        "/run/firn-qa-checks.txt", str(tmp_path / "checks.txt"))
    guest = subprocess.run(["bash", "-c", script.split(creation)[0] + generated],
                           env={**os.environ, "STATUS": status}, capture_output=True, text=True, check=True)
    assert f"FIRN_QA__CHECK login=active" in guest.stdout
    assert "FIRN_QA__CHECK login_unit=display-manager.service" in guest.stdout
    assert f"FIRN_QA__CHECK bootc_image={expected_image}" in guest.stdout
    assert f"FIRN_QA__CHECK bootc_digest={expected_digest}" in guest.stdout
    assert f"FIRN_QA__CHECK bootc={'bootc_1.0' if bootc_present else 'absent'}" in guest.stdout
    assert "FIRN_QA__CHECK rootfs=overlay" in guest.stdout
    assert "FIRN_QA__CHECK sysroot=btrfs" in guest.stdout
    # snosi#1130 probes: no /sysroot/composefs here, a clean log, and a non-root canary.
    assert "FIRN_QA__CHECK cfs_compressed_objects=unknown" in guest.stdout
    assert "FIRN_QA__CHECK fsverity_errors=0" in guest.stdout
    assert "FIRN_QA__CHECK kernel_btrfs_verity_canary=skipped" in guest.stdout
    assert re.search(r"^FIRN_QA__CHECK kernel=\S+$", guest.stdout, re.MULTILINE)


def test_guest_check_login_poll_has_safe_unit_budget(lane):
    source = lane["script"]["source"]
    check = source.split("CHECK_SH=$(cat <<SCRIPT\n", 1)[1].split("\nSCRIPT", 1)[0]
    unit = source.split('CUNIT="[Unit]\n', 1)[1].split('"\n        CD=', 1)[0]
    assert "is-system-running" not in check
    for unit_name in ("display-manager.service", "getty@tty1.service", "serial-getty@ttyS0.service"):
        assert f"timeout 10 systemctl is-active {unit_name}" in check
    poll = check.split("# --- BEGIN login-poll ---", 1)[1].split("# --- END login-poll ---", 1)[0]
    assert len(re.findall(r"systemctl is-active ", poll)) == 3
    assert "SERIAL_WAIT_SECONDS=30" in check
    assert "timeout 10 systemctl show -p MainPID --value serial-getty@ttyS0.service" in check
    assert r"timeout 120 bootc status --json" in check
    assert len(re.findall(r"bootc status --json", check)) == 1
    assert "LOGIN_WAIT_SECONDS=300" in check
    assert r"deadline=\$((SECONDS+LOGIN_WAIT_SECONDS))" in check
    assert r"SECONDS >= deadline" in check
    budget = re.search(r"^TimeoutStartSec=(\d+)$", unit, re.MULTILINE)
    assert budget is not None
    # One last login iteration may begin just before the deadline (three 10s
    # calls), then up to 120s for bootc status, then the bounded 30s serial
    # getty wait (its last iteration makes two 10s calls), the 2s replay delay
    # and additional startup margin.
    # The fs-verity probe adds the composefs scan (180s), the journal read (20s)
    # and the canary (60s plus a few seconds of cleanup).
    assert 300 + 3 * 10 + 120 + 180 + 20 + 60 + 15 + 30 + 2 * 10 + 10 < int(budget.group(1))
    assert "timeout 180 xargs" in check and "timeout 20 journalctl" in check and "timeout -k 5 60 bash" in check
    params = {p["name"]: p for p in lane["inputs"]["parameters"]}
    assert int(params["installed-boot-timeout-seconds"]["value"]) >= int(budget.group(1))


@pytest.mark.parametrize("display,getty,serial,bound,expected,login_unit,iterations", [
    ("inactive,inactive,active", "inactive", "inactive", 300, "active", "display-manager.service", 3),
    ("active", "inactive", "active", 300, "active", "display-manager.service", 1),
    ("inactive", "active", "active", 300, "active", "getty@tty1.service", 1),
    ("inactive", "inactive", "active", 300, "active", "serial-getty@ttyS0.service", 1),
    ("inactive", "inactive,inactive,inactive", "inactive", 4, "inactive", "none", 3),
    ("inactive", "empty", "inactive", 0, "unknown", "none", 1),
])
def test_guest_login_poll_reports_final_state_without_hanging(
    lane, tmp_path, display, getty, serial, bound, expected, login_unit, iterations,
):
    source = lane["script"]["source"]
    creation = source[source.index("CHECK_SH=$(cat <<SCRIPT"):source.index("\nC_B64=", source.index("CHECK_SH=$(cat <<SCRIPT"))]
    generated = subprocess.run(
        ["bash", "-c", 'M_CHECK=FIRN_QA__CHECK; M_DONE=FIRN_QA__CHECKS_DONE\n' + creation + '\nprintf "%s" "$CHECK_SH"'],
        capture_output=True, text=True, check=True,
    ).stdout
    poll = generated.split("# --- BEGIN login-poll ---\n", 1)[1].split("# --- END login-poll ---", 1)[0]
    poll = poll.replace("LOGIN_WAIT_SECONDS=300", f"LOGIN_WAIT_SECONDS={bound}")
    script = (
        'set -euo pipefail\nM_CHECK=FIRN_QA__CHECK\n'
        'emit() { echo "$M_CHECK $1=$2"; }\n'
        'timeout() { shift; "$@"; }\n'
        'systemctl() {\n'
        '  [[ "$1" == is-active ]] || return 2\n'
        '  mapfile -t calls < "$CALLS"\n'
        '  index=$((${#calls[@]} / 3))\n'
        '  printf "%s\\n" "$2" >> "$CALLS"\n'
        '  case "$2" in\n'
        '    display-manager.service) responses=$DISPLAY_STATES ;;\n'
        '    getty@tty1.service) responses=$GETTY_STATES ;;\n'
        '    serial-getty@ttyS0.service) responses=$SERIAL_STATES ;;\n'
        '    *) return 2 ;;\n'
        '  esac\n'
        '  IFS=, read -ra states <<< "$responses"\n'
        '  if (( index >= ${#states[@]} )); then index=$((${#states[@]} - 1)); fi\n'
        '  [[ "${states[$index]}" != empty ]] || return 1\n'
        '  printf "%s\\n" "${states[$index]}"\n'
        '  [[ "${states[$index]}" == active ]]\n'
        '}\n'
        'sleep() { [[ "$1" == 2 ]] || return 2; SECONDS=$((SECONDS + 2)); }\n'
        + poll
    )
    call_log = tmp_path / "calls"
    call_log.write_text("")
    result = subprocess.run(["bash", "-c", script], env={**os.environ, "DISPLAY_STATES": display,
                       "GETTY_STATES": getty, "SERIAL_STATES": serial, "CALLS": str(call_log)},
                            capture_output=True, text=True, check=False, timeout=5)
    assert result.returncode == 0, result.stderr
    assert f"FIRN_QA__CHECK login={expected}" in result.stdout
    assert f"FIRN_QA__CHECK login_unit={login_unit}" in result.stdout
    assert call_log.read_text().splitlines() == [
        unit for _ in range(iterations)
        for unit in ("display-manager.service", "getty@tty1.service", "serial-getty@ttyS0.service")
    ]


def _render_check(lane):
    source = lane["script"]["source"]
    creation = source[source.index("CHECK_SH=$(cat <<SCRIPT"):source.index("\nC_B64=", source.index("CHECK_SH=$(cat <<SCRIPT"))]
    return subprocess.run(
        ["bash", "-c", 'M_CHECK=FIRN_QA__CHECK; M_DONE=FIRN_QA__CHECKS_DONE\n' + creation + '\nprintf "%s" "$CHECK_SH"'],
        capture_output=True, text=True, check=True,
    ).stdout


def test_guest_check_uses_fresh_serial_opens_not_long_lived_tee(lane):
    generated = _render_check(lane)
    assert "tee" not in generated
    assert not re.search(r"^\s*exec\s*>", generated, re.MULTILINE)
    emit = generated.split("emit() {", 1)[1].split("\n}\n", 1)[0]
    assert ">> /run/firn-qa-checks.txt" in emit
    assert '> /dev/ttyS0 2>/dev/null || true' in emit
    assert "frostyard/snosi#1123" in generated
    assert generated.count("> /dev/ttyS0") >= 3  # emit plus two replays


def _run_check_tail(lane, tmp_path, getty_state, getty_pid):
    generated = _render_check(lane)
    generated = generated.replace("> /dev/ttyS0", ">> " + str(tmp_path / "ttyS0")).replace(
        "/run/firn-qa-checks.txt", str(tmp_path / "checks.txt"))  # a real tty does not truncate
    start = generated.index("emit booted ok")
    head = generated[:start]
    tail = generated[generated.index("SERIAL_WAIT_SECONDS=30"):]
    body = (
        'emit login active\nemit login_unit display-manager.service\n'
        'emit bootc_digest sha256:abc\n'
    )
    stubs = (
        'systemctl() {\n'
        '  case "$1" in\n'
        '    is-active) printf "%s\\n" "$GETTY_STATE"; [[ "$GETTY_STATE" == active ]] ;;\n'
        '    show) printf "%s\\n" "$GETTY_PID" ;;\n'
        '  esac\n'
        '}\n'
        'timeout() { shift; "$@"; }\n'
        'sleep() { echo "$1" >> "$SLEEPS"; SECONDS=$((SECONDS + $1)); }\n'
        'sync() { echo sync >> "$SLEEPS"; }\n'
    )
    sleeps = tmp_path / "sleeps"
    sleeps.write_text("")
    result = subprocess.run(
        ["bash", "-c", stubs + head + body + tail],
        env={**os.environ, "GETTY_STATE": getty_state, "GETTY_PID": getty_pid, "SLEEPS": str(sleeps)},
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result, (tmp_path / "ttyS0").read_text().splitlines(), sleeps.read_text().split()


CHECK_LINES = [
    "FIRN_QA__CHECK login=active",
    "FIRN_QA__CHECK login_unit=display-manager.service",
    "FIRN_QA__CHECK bootc_digest=sha256:abc",
]


@pytest.mark.parametrize("state,pid,max_waits", [
    ("inactive", "0", 0),   # no serial getty: do not wait at all
    ("active", "0", 30),    # no MainPID ever: bounded by 30s
    ("active", "1", 30),    # PID 1 is systemd, never agetty: bounded by 30s
])
def test_guest_final_replay_is_bounded_and_repeated(lane, tmp_path, state, pid, max_waits):
    result, serial, sleeps = _run_check_tail(lane, tmp_path, state, pid)
    block = CHECK_LINES + ["FIRN_QA__CHECKS_DONE"]
    # Each check goes out once at emit time, then the whole block twice.
    assert serial == CHECK_LINES + block + block
    assert serial[-len(block):] == block
    assert result.stdout.splitlines()[-1] == "FIRN_QA__CHECKS_DONE"
    assert (tmp_path / "checks.txt").read_text().splitlines() == CHECK_LINES
    waits = [x for x in sleeps if x == "1"]
    assert len(waits) <= max_waits
    assert sleeps[-3:] == ["sync", "2"] or sleeps[-2:] == ["sync", "2"]
    assert sleeps.count("sync") == 1
    if state == "inactive":
        assert waits == []


@pytest.mark.parametrize("serial_path", ["/nonexistent-dir/ttyS0"])
def test_guest_serial_write_failure_never_stops_script(lane, tmp_path, serial_path):
    generated = _render_check(lane).replace("/dev/ttyS0", serial_path).replace(
        "/run/firn-qa-checks.txt", str(tmp_path / "checks.txt"))
    head = generated[:generated.index("emit booted ok")]
    tail = generated[generated.index("SERIAL_WAIT_SECONDS=30"):]
    script = ('systemctl() { return 3; }\ntimeout() { shift; "$@"; }\nsleep() { :; }\nsync() { :; }\n'
              + head + "emit a b\n" + tail + '\necho reached-end\n')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "reached-end" in result.stdout


@pytest.mark.parametrize("console,code", [
    ("FIRN_QA__INSTALL_FAIL rc=23", "firn_rc_23"),
    ("FIRN_QA__INSTALL_FAIL no writable disk", "firn_rc_unknown"),
    ("still installing", "timeout"),
])
def test_install_wait_failure_writes_step_and_code(lane, tmp_path, console, code):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    block = source[source.index('if ! wait_for "${M_OK}"'):source.index('echo "Install reported success."')]
    script = ('set -euo pipefail\nCELL=bootc/floe/none/sb=false\nFIRN_VERSION=firn_0.6.0\n'
              + fail.replace("/tmp/results", str(tmp_path))
              + '\nM_OK=FIRN_QA__INSTALL_OK; M_FAIL=FIRN_QA__INSTALL_FAIL; INSTALL_TIMEOUT=1\n'
              + 'console_log() { printf "%s\\n" "$CONSOLE"; }\nwait_for() { return 1; }\n'
              + block)
    result = subprocess.run(["bash", "-c", script], env={**os.environ, "CONSOLE": console},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert console in result.stderr
    assert (tmp_path / "result-summary.txt").read_text() == f"FAILED: install:{code} (bootc/floe/none/sb=false) firn=firn_0.6.0"


def test_required_image_digest_is_forwarded_and_validated(lane):
    params = {p["name"]: p for p in lane["inputs"]["parameters"]}
    assert set(params["image-digest"]) == {"name"}
    assert {e["name"]: e["value"] for e in lane["script"]["env"]}["IMAGE_DIGEST"] == "{{inputs.parameters.image-digest}}"
    workflow = yaml.safe_load((TEMPLATE.parents[2] / "argo/firn-install-test.yaml").read_text())
    assert "image-digest" not in {p["name"] for p in workflow["spec"]["arguments"]["parameters"]}
    cell_task = workflow["spec"]["templates"][0]["dag"]["tasks"][0]
    passed = {p["name"]: p["value"] for p in cell_task["arguments"]["parameters"]}
    assert passed["image-digest"] == "{{item.image-digest}}"
    cells = cell_task["withItems"]
    assert len(cells) == 6
    assert all(cell["image-digest"] == f"REPLACE_{cell['product'].upper()}_DIGEST" for cell in cells)
    assert {cell["product"] for cell in cells} == {"floe", "snow", "snowfield"}
    assert {cell["family"] for cell in cells} == {"bootc"}


def test_bootc_only_submit_file_pins_three_secure_unencrypted_cells():
    submit = TEMPLATE.parents[2] / "argo/bootc-only-iso-install-test.yaml"
    workflow = yaml.safe_load(submit.read_text(encoding="utf-8"))
    assert workflow["kind"] == "Workflow"
    assert workflow["metadata"]["generateName"] == "bootc-only-iso-install-"
    spec = workflow["spec"]
    assert spec["activeDeadlineSeconds"] == 21600
    assert spec["serviceAccountName"] == "argo"
    assert spec["entrypoint"] == "main"
    assert spec["arguments"]["parameters"] == [
        {"name": "iso-url", "value": "REPLACE_ISO_URL"},
        {"name": "iso-sha256", "value": "REPLACE_ISO_SHA256"},
    ]
    tasks = spec["templates"][0]["dag"]["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["templateRef"] == {"name": "run-firn-install-tests", "template": "run-firn-install-tests"}
    cells = task["withItems"]
    assert len(cells) == 3
    assert {cell["product"] for cell in cells} == {"snow", "floe", "sundog"}
    assert all(cell == {
        "family": "bootc", "product": cell["product"], "encryption": "none",
        "secureboot": "true", "image-digest": f"REPLACE_{cell['product'].upper()}_DIGEST",
    } for cell in cells)
    assert {p["name"]: p["value"] for p in task["arguments"]["parameters"]} == {
        "iso-url": "{{workflow.parameters.iso-url}}",
        "iso-sha256": "{{workflow.parameters.iso-sha256}}",
        "image-digest": "{{item.image-digest}}",
        "family": "{{item.family}}",
        "image": "{{item.product}}",
        "encryption": "{{item.encryption}}",
        "secureboot": "{{item.secureboot}}",
    }
    assert "Snowfield: untested and not part of this run." in submit.read_text(encoding="utf-8")


@pytest.mark.parametrize("phase,operation,expected", [
    ("create", "init ", "init_failed"),
    ("create", "query ", "pool_unavailable"),
    ("create", "config device add fq-test vtpm", "device_attach_failed"),
    ("create", "config device add fq-test installer", "device_attach_failed"),
    ("create", "config set ", "config_failed"),
    ("create", "start ", "start_failed"),
    ("reboot", "stop ", "stop_failed"),
    ("reboot", "config device remove ", "device_detach_failed"),
    ("reboot", "config set ", "config_failed"),
    ("reboot", "start ", "restart_failed"),
])
def test_incus_failure_records_step_and_runs_cleanup(lane, tmp_path, phase, operation, expected):
    source = lane["script"]["source"]
    if phase == "create":
        block = source[source.index('echo "Creating VM'):source.index('echo "Waiting for the installer')]
    else:
        block = source[source.index('incus stop "${VM}"'):source.index('if ! wait_for "${M_DONE}"')]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    cleanup = source[source.index("cleanup() {"):source.index("trap cleanup EXIT") + len("trap cleanup EXIT")]
    version = "not_reached" if phase == "create" else "firn_0.6.0"
    script = (f'set -euo pipefail\nCELL=bootc/floe/none/sb=false\nFIRN_VERSION={version}\nVM=fq-test\n'
              'SECUREBOOT=false; TARGET_DISK_SIZE=40GiB; VM_CPUS=4; VM_MEMORY=8GiB\n'
              'ISO_PATH=/tmp/test.iso; U_B64=a; D_B64=b; M_CHECK=FIRN_QA__CHECK; M_DONE=FIRN_QA__CHECKS_DONE\n'
              + fail.replace('/tmp/results', str(tmp_path)) + '\n' + cleanup + '\n'
              'incus() {\n'
              '  if [[ "$1" == delete ]]; then printf "%s\\n" "$*" >> "$CLEANUP_LOG"; return 0; fi\n'
              '  if [[ "$*" == "$FAIL_OPERATION"* ]]; then return 9; fi\n'
              '  if [[ "$1" == query ]]; then printf "%s" \'{"expanded_devices":{"root":{"pool":"default"}}}\'; fi\n'
              '}\n' + block)
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "FAIL_OPERATION": operation, "CLEANUP_LOG": str(tmp_path / "cleanup")},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1, result.stderr
    assert (tmp_path / "result-summary.txt").read_text() == (
        f"FAILED: vm:{expected} (bootc/floe/none/sb=false) firn={version}"
    )
    assert "delete --force fq-test" in (tmp_path / "cleanup").read_text()


@pytest.mark.parametrize("failure,code", [
    ("apt", "dependencies"),
    ("pip", "virt_firmware_install"),
    ("lookup", "virt_fw_vars_unavailable"),
])
def test_mok_setup_failure_records_result_and_runs_cleanup(lane, tmp_path, failure, code):
    source = lane["script"]["source"]
    # Execute the actual setup in script order, stopping before ISO download.
    setup = source[:source.index('M_BEGIN="FIRN_QA__INSTALL_BEGIN"')]
    setup = setup.replace("/tmp/results", str(tmp_path))
    setup = setup.replace("/var/lib/snosi-lab/iso", str(tmp_path / "iso"))
    script = (
        'apt-get() { if [[ "$FAILURE" == apt && "$1" == install && "$*" == *git* ]]; then return 3; fi; return 0; }\n'
        'pip() { if [[ "$FAILURE" == pip ]]; then return 4; fi; return 0; }\n'
        'command() { if [[ "$FAILURE" == lookup && "$2" == virt-fw-vars ]]; then return 1; fi; return 0; }\n'
        'incus() { printf "%s\\n" "$*" >> "$CLEANUP_LOG"; }\n'
        + setup
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "FAMILY": "bootc", "IMAGE": "floe", "ENC": "none", "SECUREBOOT": "true",
             "FAILURE": failure, "CLEANUP_LOG": str(tmp_path / "cleanup")},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 1, result.stderr
    assert (tmp_path / "result-summary.txt").read_text() == f"FAILED: mok:{code} (bootc/floe/none/sb=true) firn=not_reached"
    assert "delete --force" in (tmp_path / "cleanup").read_text()


def test_mok_uuid_failure_uses_fail_helper(lane, tmp_path):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    uuid_line = source[source.index('MOK_GUID="$(python3 -c'):source.index('virt-fw-vars --inplace', source.index('MOK_GUID="$(python3 -c'))]
    result = subprocess.run(
        ["bash", "-c", 'set -euo pipefail\nCELL=bootc/floe/none/sb=true\n'
         + 'FIRN_VERSION=firn_0.6.0\n' + fail.replace("/tmp/results", str(tmp_path))
         + '\npython3() { return 9; }\n' + uuid_line],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert (tmp_path / "result-summary.txt").read_text() == "FAILED: mok:uuid (bootc/floe/none/sb=true) firn=firn_0.6.0"


def test_console_collection_preserves_underscore_check_keys(lane, tmp_path):
    source = lane["script"]["source"]
    collect = source[source.index('console_log | grep -aoE "${M_CHECK}'):
                     source.index('echo "--- post-install checks ---"')]
    result = subprocess.run(
        ["bash", "-c", 'set -euo pipefail\nM_CHECK=FIRN_QA__CHECK\n'
         + 'console_log() { printf "%s\\n" "FIRN_QA__CHECK bootc_image=ghcr.io/frostyard/floe:latest" '
           '"FIRN_QA__CHECK bootc_digest=sha256:aaa"; }\n'
         + collect.replace('/tmp/results', str(tmp_path))],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "checks.txt").read_text() == (
        "bootc_digest=sha256:aaa\nbootc_image=ghcr.io/frostyard/floe:latest\n"
    )


def _probe_functions(lane):
    generated = _render_check(lane)
    return generated.split("fsverity_count() {", 1)[0], "fsverity_count() {" + generated.split(
        "fsverity_count() {", 1)[1].split("emit cfs_compressed_objects", 1)[0]


@pytest.mark.parametrize("journal,expected", [
    ("kernel: boot\n", "0"),
    ("kernel: fs-verity (dm-0, inode 1): FILE CORRUPTED! pos=0, level=-1\n"
     "kernel: fs-verity (dm-0, inode 2): FILE CORRUPTED! pos=4096, level=-1\n", "2"),
    ("kernel: fsverity_msg: 5 callbacks suppressed\n"
     "kernel: fs-verity (dm-0, inode 2): FILE CORRUPTED! pos=0, level=-1\n"
     "kernel: net: 9 callbacks suppressed\n", "6"),
])
def test_fsverity_count_adds_corrupted_lines_and_suppressed_callbacks(lane, tmp_path, journal, expected):
    _, functions = _probe_functions(lane)
    script = 'timeout() { shift; "$@"; }\njournalctl() { printf "%s" "$JOURNAL"; }\n' + functions + "\nfsverity_count"
    result = subprocess.run(["bash", "-c", script], env={**os.environ, "JOURNAL": journal},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("journal_script", [
    "return 1", "true",
    "echo '-- No entries --'",          # exit 0 and a banner: no kernel entry was read
    "printf '\\n-- No entries --  \\n\\n'",
])
def test_fsverity_count_is_unknown_when_the_kernel_log_is_unreadable_or_empty(lane, journal_script):
    assert "journalctl -q -k -b --no-pager" in _render_check(lane)
    _, functions = _probe_functions(lane)
    script = ('timeout() { shift; "$@"; }\njournalctl() { ' + journal_script + '; }\n' + functions
              + '\necho "$(fsverity_count || echo unknown)"')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert result.stdout.strip() == "unknown"


FILEFRAG = r"""#!/bin/bash
# Fake `filefrag -v`: an object whose name starts with "c" has an encoded extent.
shift
for f in "$@"; do
  echo "Filesystem type is: 9123683e"
  echo "File size of $f is 8192 (2 blocks of 4096 bytes)"
  echo " ext:     logical_offset:        physical_offset: length:   expected: flags:"
  case "$(basename "$f")" in
    c*) echo "   0:        0..       1:      100..       101:      2:             last,encoded,eof" ;;
    m*) echo "   0:        0..       0:      100..       100:      1:"
        echo "   1:        1..       1:      200..       200:      1:             encoded"
        echo "   2:        2..       2:      300..       300:      1:             last,encoded,eof" ;;
    *) echo "   0:        0..       1:      100..       101:      2:             last,eof" ;;
  esac
  echo "$f: 1 extent found"
done
"""


def _run_cfs(lane, tmp_path, sysroot_fs="btrfs", with_filefrag=True, with_dir=True, names=(), findmnt_rc=0):
    _, functions = _probe_functions(lane)
    objects = tmp_path / "objects"
    if with_dir:
        objects.mkdir()
        for name in names:
            (objects / name[:2]).mkdir(parents=True, exist_ok=True)
            (objects / name[:2] / name).write_text("x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if with_filefrag:
        (bin_dir / "filefrag").write_text(FILEFRAG)
        (bin_dir / "filefrag").chmod(0o755)
    body = functions[functions.index("cfs_compressed_objects() {"):].replace("/sysroot/composefs/objects", str(objects))
    script = (f'PATH="{bin_dir}:$PATH"\nfindmnt() {{ printf "%s" "$SYSROOT_FS"; return {findmnt_rc}; }}\n'
              + ("" if with_filefrag else 'command() { if [[ "$2" == filefrag ]]; then return 1; fi; builtin command "$@"; }\n')
              + body + "\ncfs_compressed_objects")
    return subprocess.run(["bash", "-c", script], env={**os.environ, "SYSROOT_FS": sysroot_fs},
                          capture_output=True, text=True, check=False)


@pytest.mark.parametrize("names,expected", [
    ((), "0"),
    (("aa11", "bb22"), "0"),
    (("aa11", "cc33", "cd44", "mm55", "zz66"), "3"),  # an object counts once, however many encoded extents
])
def test_cfs_compressed_objects_counts_files_with_an_encoded_extent(lane, tmp_path, names, expected):
    result = _run_cfs(lane, tmp_path, names=names)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("kwargs,expected", [
    ({"sysroot_fs": "ext4", "names": ("cc33",)}, "n/a"),
    ({"with_filefrag": False, "names": ("cc33",)}, "unknown"),
    ({"with_dir": False}, "unknown"),
    # findmnt itself failing (or printing nothing) is not proof that /sysroot is not btrfs.
    ({"findmnt_rc": 1, "sysroot_fs": "", "names": ("cc33",)}, "unknown"),
    ({"findmnt_rc": 1, "sysroot_fs": "ext4", "names": ("cc33",)}, "unknown"),
    ({"sysroot_fs": "", "names": ("cc33",)}, "unknown"),
])
def test_cfs_compressed_objects_reports_na_or_unknown(lane, tmp_path, kwargs, expected):
    result = _run_cfs(lane, tmp_path, **kwargs)
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("counts,body_rc,expected", [
    (("0", "0"), 0, "clean"),
    (("0", "0"), 1, "skipped"),    # a step failed (a tool is missing, mount denied, timeout)
    (("0", "2"), 0, "defect"),
    (("0", "2"), 124, "defect"),   # the log message wins even if the read itself failed
    (("unknown", "unknown"), 0, "skipped"),
    (("3", "3"), 0, "clean"),      # earlier errors in the log do not count
])
def test_kernel_canary_verdict_is_defect_clean_or_skipped(lane, tmp_path, counts, body_rc, expected):
    generated = _render_check(lane)
    block = generated[generated.index("CANARY=skipped"):generated.index("# --- END fsverity-probe ---")]
    state = tmp_path / "calls"
    state.write_text("0")
    script = (
        'timeout() { return "$BODY_RC"; }\nsleep() { :; }\ncanary_body() { :; }\n'
        'canary_cleanup() { echo cleaned >> "$LOG"; }\n'
        'emit() { echo "$1=$2"; }\n'
        'dmesg_count() { n=$(<"$STATE"); echo $((n + 1)) > "$STATE"; IFS=, read -ra c <<< "$COUNTS"; echo "${c[$n]}"; }\n'
        + block
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                            env={**os.environ, "BODY_RC": str(body_rc), "STATE": str(state),
                                 "COUNTS": ",".join(counts), "LOG": str(tmp_path / "log")})
    assert result.returncode == 0, result.stderr
    assert f"kernel_btrfs_verity_canary={expected}" in result.stdout.splitlines()
    assert (tmp_path / "log").read_text() == "cleaned\n"   # always cleans up


def test_kernel_canary_runs_after_the_error_count_and_never_judges(lane):
    source = lane["script"]["source"]
    check = _render_check(lane)
    assert check.index("emit fsverity_errors") < check.index("canary_body()")
    assert check.index("emit kernel_btrfs_verity_canary") < check.index("SERIAL_WAIT_SECONDS=30")
    judge = source[source.index("judge() {"):source.index("\n}", source.index("judge() {"))]
    assert "kernel_btrfs_verity_canary" not in judge
    # The canary only reads the kernel log, and cleans up what it made.
    assert "dmesg -c" not in check and "dmesg -C" not in check and "journalctl --rotate" not in check
    assert "compress-force=zstd" in check and "0x40806685" in check and "hash-alg=sha512" in check
    assert "echo 3 > /proc/sys/vm/drop_caches" in check


def test_judge_records_the_known_defect_note_in_the_checks_file(lane, tmp_path):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    judge = source[source.index("judge() {"):source.index("\n}", source.index("judge() {")) + 2]
    values = dict(line.split("=", 1) for line in GOOD_CHECKS.splitlines())
    values.update(fsverity_errors="3", kernel="7.2.12+deb14-amd64")
    checks = tmp_path / "checks.txt"
    checks.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nFIRN_VERSION=firn_0.6.0\n" + fail.replace("/tmp/results", str(tmp_path))
         + "\n" + judge.replace("/tmp/results", str(tmp_path)) + "\njudge \"$CHECKS\""],
        env={**os.environ, "CELL": "c", "IMAGE": "floe", "ENC": "none", "IMAGE_DIGEST": EXPECTED_DIGEST,
             "CHECKS": str(checks)}, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "fsverity_known_defect=7.2.12+deb14-amd64" in checks.read_text().splitlines()
    assert "known-defect kernel 7.2.12+deb14-amd64" in result.stderr   # visible note
    # One list per template, with the two kernels of snosi#1130.
    assert source.count('known_defect_kernels="7.2.11+deb14-amd64 7.2.12+deb14-amd64"') == 1


def test_probe_emits_the_kernel_before_the_canary_in_every_run(lane):
    check = _render_check(lane)
    assert 'emit kernel "$(uname -r)"' in check
    assert check.count("emit kernel ") == 1
    assert check.index('emit kernel "') < check.index("emit fsverity_errors") < check.index("canary_body()")


KNOWN_A, KNOWN_B, OTHER_KERNEL = "7.2.11+deb14-amd64", "7.2.12+deb14-amd64", "7.3.1+deb14-amd64"


@pytest.mark.parametrize("kernels,errors,expected", [
    # Exactly 1 distinct, well-formed value is needed for the exemption; identical duplicates are fine.
    ([KNOWN_A], "2", "PASS-NOTE:" + KNOWN_A),
    ([KNOWN_A, KNOWN_A], "2", "PASS-NOTE:" + KNOWN_A),
    ([KNOWN_A, OTHER_KERNEL], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([OTHER_KERNEL, KNOWN_A], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([KNOWN_A, KNOWN_B], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([KNOWN_A + " " + KNOWN_B], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([KNOWN_A + " junk"], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([KNOWN_A + " "], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([" " + KNOWN_A], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([""], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    (["a" * 65], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([KNOWN_A + ";x"], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([], "2", "FAILED: boot:fsverity_kernel_ambiguous"),
    ([OTHER_KERNEL, OTHER_KERNEL], "2", "FAILED: boot:fsverity_errors_2"),
    # Without errors the kernel identity is not consulted.
    ([KNOWN_A, OTHER_KERNEL], "0", "PASS"),
    ([KNOWN_A + " " + KNOWN_B], "0", "PASS"),
])
def test_judge_needs_one_unambiguous_kernel_for_the_exemption(lane, tmp_path, kernels, errors, expected):
    source = lane["script"]["source"]
    fail = source[source.index("fail() {"):source.index("\n}", source.index("fail() {")) + 2]
    judge = source[source.index("judge() {"):source.index("\n}", source.index("judge() {")) + 2]
    values = dict(line.split("=", 1) for line in GOOD_CHECKS.splitlines())
    values.update(fsverity_errors=errors)
    lines = [f"{k}={v}" for k, v in values.items() if k != "kernel"] + [f"kernel={k}" for k in kernels]
    checks = tmp_path / "checks.txt"
    checks.write_text("".join(line + "\n" for line in lines))
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nFIRN_VERSION=firn_0.6.0\n" + fail.replace("/tmp/results", str(tmp_path))
         + "\n" + judge.replace("/tmp/results", str(tmp_path)) + "\njudge \"$CHECKS\""],
        env={**os.environ, "CELL": "c", "IMAGE": "floe", "ENC": "none", "IMAGE_DIGEST": EXPECTED_DIGEST,
             "CHECKS": str(checks)}, text=True, capture_output=True, check=False)
    summary = (tmp_path / "result-summary.txt").read_text()
    if expected.startswith("PASS"):
        assert result.returncode == 0, result.stderr
        if expected == "PASS":
            assert "fsverity_known_defect" not in summary
        else:
            note = expected.split(":", 1)[1]
            assert f" fsverity_known_defect={note} firn=" in summary
            assert checks.read_text().splitlines().count(f"fsverity_known_defect={note}") == 1
    else:
        assert result.returncode == 1
        assert summary.startswith(expected + " (c)")
        assert "fsverity_known_defect" not in summary
