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
    assert 'verity' not in source
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
    ],
)
def test_judge_checks_under_errexit(lane, tmp_path, change, expected):
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
def test_guest_checks_report_bootc_status_sysroot_and_missing_binary(lane, status, expected_image, expected_digest, bootc_present):
    source = lane["script"]["source"]
    creation = source[source.index("CHECK_SH=$(cat <<SCRIPT"):source.index("\nC_B64=", source.index("CHECK_SH=$(cat <<SCRIPT"))]
    script = """set -euo pipefail
M_CHECK=FIRN_QA__CHECK; M_DONE=FIRN_QA__CHECKS_DONE
IMAGE=floe
systemctl() { printf active; }
timeout() { shift; "$@"; }
findmnt() { if [[ "${@: -1}" == /sysroot ]]; then printf btrfs; else printf overlay; fi; }
lsblk() { return 0; }
bootc() { if [[ "$1" == --version ]]; then printf 'bootc 1.0'; else printf '%s' "$STATUS"; fi; }
""" + ("" if bootc_present else 'command() { if [[ "$2" == bootc ]]; then return 1; fi; builtin command "$@"; }\n') + creation + "\n" + 'printf "%s" "$CHECK_SH"'
    generated = subprocess.run(["bash", "-c", script], env={**os.environ, "STATUS": status},
                               capture_output=True, text=True, check=True).stdout
    # Remove guest console redirection; run the actual generated body.
    generated = generated.replace("exec > >(tee /dev/console /dev/ttyS0 2>/dev/null) 2>&1", "")
    guest = subprocess.run(["bash", "-c", script.split(creation)[0] + generated],
                           env={**os.environ, "STATUS": status}, capture_output=True, text=True, check=True)
    assert f"FIRN_QA__CHECK login=active" in guest.stdout
    assert "FIRN_QA__CHECK login_unit=display-manager.service" in guest.stdout
    assert f"FIRN_QA__CHECK bootc_image={expected_image}" in guest.stdout
    assert f"FIRN_QA__CHECK bootc_digest={expected_digest}" in guest.stdout
    assert f"FIRN_QA__CHECK bootc={'bootc_1.0' if bootc_present else 'absent'}" in guest.stdout
    assert "FIRN_QA__CHECK rootfs=overlay" in guest.stdout
    assert "FIRN_QA__CHECK sysroot=btrfs" in guest.stdout


def test_guest_check_login_poll_has_safe_unit_budget(lane):
    source = lane["script"]["source"]
    check = source.split("CHECK_SH=$(cat <<SCRIPT\n", 1)[1].split("\nSCRIPT", 1)[0]
    unit = source.split('CUNIT="[Unit]\n', 1)[1].split('"\n        CD=', 1)[0]
    assert "is-system-running" not in check
    for unit_name in ("display-manager.service", "getty@tty1.service", "serial-getty@ttyS0.service"):
        assert f"timeout 10 systemctl is-active {unit_name}" in check
    assert len(re.findall(r"systemctl is-active ", check)) == 3
    assert r"timeout 120 bootc status --json" in check
    assert len(re.findall(r"bootc status --json", check)) == 1
    assert "LOGIN_WAIT_SECONDS=300" in check
    assert r"deadline=\$((SECONDS+LOGIN_WAIT_SECONDS))" in check
    assert r"SECONDS >= deadline" in check
    budget = re.search(r"^TimeoutStartSec=(\d+)$", unit, re.MULTILINE)
    assert budget is not None
    # One last iteration may begin just before the deadline (three 10s calls),
    # followed by up to 120s for bootc status and additional startup margin.
    assert 300 + 3 * 10 + 120 + 10 < int(budget.group(1))


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
