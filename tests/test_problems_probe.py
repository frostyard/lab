"""Offline contract tests for the report-only problems probe (plan T857 phase 1).

The probe proves on a real installed VM that the snosi-health collector and
systemd-coredump record 4 local problems. It is report-only: it never fails the
run for a product finding, and it never passes when it proved nothing.
"""
import base64
import hashlib
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from test_snow_bootc_lifecycle import N, NEXT, NONCE, REPO, SOURCE, manifest, run_script

BOOT_ID = "01234567-89ab-4cde-8f01-23456789abcd"
SECOND_BOOT_ID = "11234567-89ab-4cde-8f01-23456789abcd"
LOCAL_ID = "0123456789abcdef"
WEEK = "2026-W45"
STAMP = "2026-11-05T04:10:00Z"
UNIT = "snow-qa-problem-local.service"
LISTING = (
    "%s  unit_failed  a local unit failed (exit-code)  seen 2 time(s)  reportable: no\n"
    "%s  update_stage_failed  OS update to 20260925000000 not staged (network)  seen 1 time(s)  reportable: yes\n"
    "%s  crash  sleep crashed (SIGSEGV)  seen 1 time(s)  reportable: yes\n"
    "%s  app_problem  org.frostyard.nsl 0.0.0-rc.857 error lab.injected_t857  seen 1 time(s)  reportable: yes\n"
    % (STAMP, STAMP, STAMP, STAMP))
V1_SHA256 = "6c053ee3a9b4fd399d1d011b2be833dc6b17f25e727161fc576b0ce64cc14c3a"


def scripts():
    return yaml.safe_load(SOURCE.read_text())["data"]


# --------------------------------------------------------------- fixtures

@pytest.fixture
def scripts_dir(tmp_path, monkeypatch):
    """qa.py, problems.py and the frozen v1 copy, as the ConfigMap mounts them."""
    folder = tmp_path / "scripts"
    folder.mkdir()
    data = scripts()
    for name in ("qa.py", "problems.py", "problem-report-v1.json"):
        (folder / name).write_text(data[name])
    monkeypatch.syspath_prepend(str(folder))
    monkeypatch.delitem(sys.modules, "qa", raising=False)
    return folder


@pytest.fixture
def problems(scripts_dir):
    spec = importlib.util.spec_from_file_location("snow_problems", scripts_dir / "problems.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def qa(scripts_dir):
    return importlib.import_module("qa")


def probe_manifest(path="fresh", product="snow"):
    """The Snow manifest with the problems_probe key, for any product."""
    m = manifest()
    repo = f"ghcr.io/frostyard/{product}"
    m.update(family=product, product=product, image_n=f"{repo}@sha256:{N}",
             image_n_plus_1=f"{repo}@sha256:{NEXT}",
             version_tag_n=f"{repo}:{m['version_n']}",
             version_tag_n_plus_1=f"{repo}:{m['version_n_plus_1']}",
             target_ref=f"{repo}:qa-controlled")
    m["problems_probe"] = {"path": path}
    return m


def normalized(tmp_path, m, qa):
    path = tmp_path / "normalized.json"
    path.write_bytes(qa.canonical(m))
    return path


# ---------------------------------------------------------------- manifest

@pytest.mark.parametrize("product", ["snow", "floe", "snowfield", "sundog"])
@pytest.mark.parametrize("path", ["fresh", "update"])
def test_problems_probe_normalizes_for_every_product_and_path(qa, product, path):
    m = probe_manifest(path, product)
    assert qa.normalize(m) is m
    assert qa.normalize(m)["problems_probe"] == {"path": path}
    assert "problems_probe" not in qa.FIELDS


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra="fresh"),
    lambda p: p.update(path="both"),
    lambda p: p.update(path=""),
    lambda p: p.update(path=True),
    lambda p: p.update(path=1),
    lambda p: p.pop("path"),
    lambda p: p.update(**{"path": "fresh", "extra": 1}),
])
def test_problems_probe_rejects_unsafe_input(qa, change):
    m = probe_manifest()
    change(m["problems_probe"])
    with pytest.raises(qa.GateError, match=r"^[a-z_]{1,40}$"):
        qa.normalize(m)


def test_problems_probe_never_travels_with_the_other_probes(qa):
    m = probe_manifest()
    m["sysext_probe"] = {"features": ["notesd"], "units": ["frostmail.service"], "first_start": []}
    with pytest.raises(qa.GateError, match="^manifest_fields$"):
        qa.normalize(m)
    m = probe_manifest()
    m["first_login_probe"] = {"session_wait": 120, "app_wait": 60}
    with pytest.raises(qa.GateError, match="^manifest_fields$"):
        qa.normalize(m)


def test_problems_probe_version_rule_follows_the_path(qa):
    # fresh never stages N+1: the versions only have to differ.
    m = probe_manifest("fresh")
    m["version_n"], m["version_n_plus_1"] = "20260924000000", "20260923000000"
    m["version_tag_n"] = f"{REPO}:20260924000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260923000000"
    assert qa.normalize(m)["version_n"] == "20260924000000"
    m["version_n_plus_1"] = "20260924000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260924000000"
    with pytest.raises(qa.GateError, match="^version_distinct$"):
        qa.normalize(m)
    # update stages N+1: N must be older.
    m = probe_manifest("update")
    m["version_n"], m["version_n_plus_1"] = "20260924000000", "20260923000000"
    m["version_tag_n"] = f"{REPO}:20260924000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260923000000"
    with pytest.raises(qa.GateError, match="^version_order$"):
        qa.normalize(m)
    m["version_n_plus_1"] = "20260924000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260924000000"
    with pytest.raises(qa.GateError, match="^version_order$"):
        qa.normalize(m)
    # The five-boot rule is unchanged without the key.
    m = manifest()
    m["version_n_plus_1"] = "20260923000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260923000000"
    with pytest.raises(qa.GateError, match="^version_order$"):
        qa.normalize(m)


# ------------------------------------------------------------- the guest

def collector_document(**overrides):
    """A problems.json document the collector could write after the injections."""
    document = {
        "format": 1, "schema": "frostyard.problem/1", "generated": STAMP,
        "image": {"id": "snow", "version": "20260924000000"}, "secure": True,
        "client": "snosi-health/20260924000000",
        "problems": [
            # A failed local unit: no unit name, no shipped flag, not reportable.
            {"id": LOCAL_ID, "kind": "unit_failed", "detail": {"result": "exit-code"}, "week": WEEK,
             "first_seen": STAMP, "last_seen": STAMP, "occurrences": 2, "reportable": False},
            {"id": "0123456789abcdee", "kind": "crash", "detail": {"exe": "sleep", "signal": "SIGSEGV"},
             "week": WEEK, "first_seen": STAMP, "last_seen": STAMP, "occurrences": 1, "reportable": True},
            {"id": "0123456789abcded", "kind": "update_stage_failed",
             "detail": {"target_version": "20260925000000", "error_class": "network",
                        "bootc_version": "1.17.3"},
             "week": WEEK, "first_seen": STAMP, "last_seen": STAMP, "occurrences": 1, "reportable": True},
            {"id": "0123456789abcdec", "kind": "app_problem",
             "detail": {"app_id": "org.frostyard.nsl", "app_version": "0.0.0-rc.857", "runtime": "go",
                        "component": "cli", "class": "error", "code": "lab.injected_t857"},
             "week": WEEK, "first_seen": STAMP, "last_seen": STAMP, "occurrences": 1, "reportable": True},
        ]}
    document.update(overrides)
    return document


def collector_details(**overrides):
    details = {"format": 1, "generated": STAMP,
               "problems": {LOCAL_ID: {"kind": "unit_failed", "local": {"unit": UNIT},
                                       "extra": {"units": [UNIT]}, "messages": ["a local failure"]}}}
    details.update(overrides)
    return details


class Child:
    """A killed child: the probe only needs its pid and its wait result."""

    def __init__(self, args, preexec_fn=None):
        self.args = args
        self.preexec_fn = preexec_fn
        self.pid = 4242

    def wait(self, timeout=None):
        return -11


class JournalSocket:
    """The journal native-protocol socket, recording the one datagram."""

    sent = []

    def __init__(self, family, kind):
        self.family = family

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def connect(self, path):
        self.path = path

    def send(self, data):
        JournalSocket.sent.append(data)


def stub_problems(qa, monkeypatch, *, failed_unit=True, stage="network", crash=True, app=True,
                  listing_rc=0, active=True, status=True):
    """Every guest command of the probe, replaced; calls are returned for checks."""
    calls = []

    def probe_run(args, timeout=120):
        calls.append(args)
        if args[:2] == ["systemctl", "get-default"]:
            return (0, b"multi-user.target\n") if status else (1, b"")
        if args[:2] == ["systemctl", "is-active"]:
            return 0, b"active\n" if active else b"inactive\n"
        if args[:2] == ["systemctl", "is-enabled"]:
            return 0, b"enabled\n"
        if args[:2] == ["systemctl", "is-failed"]:
            return (1, b"failed\n") if failed_unit else (0, b"active\n")
        if args[:2] == ["systemctl", "show"]:
            if "preset-reconcile.service" in args:
                return 0, b"Result=success\n"
            return 0, (b"FirmwareTimestampMonotonic=1000000\nLoaderTimestampMonotonic=200000\n"
                       b"InitRDTimestampMonotonic=900000\nUserspaceTimestampMonotonic=4000000\n"
                       b"ActiveEnterTimestampMonotonic=9400000\n")
        if args[0] == "systemctl":
            return (1 if args[1] == "start" and args[2] != "snosi-health.service" else 0), b""
        if args[0] == "unshare":
            return (0, b"") if stage else (1, b"")
        if args[0] == "journalctl":
            held = crash if "MESSAGE_ID=fc2e22bc6ee647b6b90729ab34a250b1" in args else app
            return 0, b'{"MESSAGE":"injected"}\n' if held else b""
        if args[0] == "runuser":
            return listing_rc, LISTING.encode()
        return 0, b""

    monkeypatch.setattr(qa, "problems_run", probe_run)
    monkeypatch.setattr(qa.subprocess, "Popen", Child)
    monkeypatch.setattr(qa.os, "kill", lambda pid, sig: calls.append(["kill", pid, sig]))
    monkeypatch.setattr(qa.socket, "socket", JournalSocket)
    JournalSocket.sent = []

    def fake_bootc(args, **kwargs):
        assert args[:2] == ["bootc", "status"], args
        status_doc = {"spec": {"image": {"image": f"{REPO}:qa-controlled", "transport": "registry"}},
                      "status": {"booted": {"image": {"imageDigest": "sha256:" + N,
                                                      "version": "20260923000000"}},
                                 "staged": None, "rollback": None}}
        return subprocess.CompletedProcess(args, 0, json.dumps(status_doc).encode(), b"")

    monkeypatch.setattr(qa.subprocess, "run", fake_bootc)
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: None)
    return calls


def guest_environment(qa, tmp_path, monkeypatch, *, path="fresh", collector=True,
                      problems_file=True, details_file=True, document=None, details=None,
                      problems_mode=0o644, details_gid=None, **stubs):
    """The installed VM's tree, its credentials and its boot id, below a temporary root."""
    m = probe_manifest(path)
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    for name, value in {"phase": "problems", "nonce": NONCE, "image_n": m["image_n"],
                        "image_n_plus_1": m["image_n_plus_1"],
                        "target_ref": m["target_ref"]}.items():
        (credentials / name).write_text(value + "\n")
    boot = tmp_path / "boot_id"
    boot.write_text(BOOT_ID + "\n")
    root = tmp_path / "root"
    (root / "etc/systemd/system").mkdir(parents=True)
    (root / "var/log/journal").mkdir(parents=True)
    (root / "proc/sys/kernel").mkdir(parents=True)
    (root / "proc/sys/kernel/core_pattern").write_text("|/usr/lib/systemd/systemd-coredump %P\n")
    (root / "run/snosi").mkdir(parents=True)
    (root / "run/snosi/update-check").write_text("outcome=failed\nerror_class=network\n")
    if collector:
        (root / "usr/bin").mkdir(parents=True)
        (root / "usr/bin/snosi-health").write_text("#!/bin/bash\n")
    if problems_file or details_file:
        (root / "var/lib/snosi-health").mkdir(parents=True)
    if problems_file:
        target = root / "var/lib/snosi-health/problems.json"
        target.write_text(json.dumps(document if document is not None else collector_document()))
        target.chmod(problems_mode)
    if details_file:
        target = root / "var/lib/snosi-health/details.json"
        target.write_text(json.dumps(details if details is not None else collector_details()))
        target.chmod(0o640)
    monkeypatch.setattr(qa, "PROBLEMS_ROOT", root)
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized(tmp_path, m, qa))
    monkeypatch.setattr(qa, "GUEST_PATHS", {**qa.GUEST_PATHS, "boot_id": boot})
    calls = stub_problems(qa, monkeypatch, **stubs)
    return m, credentials, root, calls


def payload(line):
    assert line.startswith("SNOW_PROBLEMS_V1 "), line[:40]
    encoded = line.split(" ", 1)[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def test_guest_problems_records_the_four_injected_problems(qa, tmp_path, monkeypatch):
    m, credentials, root, calls = guest_environment(qa, tmp_path, monkeypatch)
    record = payload(qa.problems(credentials))
    assert set(record) == qa.PROBLEMS_RECORD_FIELDS
    assert record["phase"] == "problems" and record["nonce"] == NONCE
    assert record["boot_id"] == BOOT_ID and record["booted_digest"] == N and record["path"] == "fresh"
    # Boot timing, as systemd-analyze computes it, from the 5 monotonic stamps.
    # firmware = Firmware - Loader, loader = Loader, kernel = InitRD, initrd =
    # Userspace - InitRD, userspace = target - Userspace, target = ActiveEnter.
    assert record["boot"] == {"target": "multi-user.target", "firmware_ms": 800, "loader_ms": 200,
                              "kernel_ms": 900, "initrd_ms": 3100, "userspace_ms": 5400,
                              "target_ms": 9400}
    assert record["collector"] is True
    assert record["timer"] == {"enabled": "enabled", "active": "active"}
    assert record["preset_reconcile"] == "success"
    assert record["coredump_user"] is False      # no systemd-coredump account in this test tree
    assert record["journal_dir"] is True and record["core_pattern"] is True
    assert record["injected"] == {"unit": True, "stage": {"outcome": "failed", "error_class": "network"},
                                  "crash": True, "app": True}
    assert record["collector_runs"] == [0, 0]
    # problems.json: its mode, owner, size, parsed document and no local unit name.
    assert record["problems"]["mode"] == "0644"
    assert record["problems"]["uid"] == os.getuid() and record["problems"]["size"] > 0
    assert record["problems"]["doc"]["schema"] == "frostyard.problem/1"
    assert record["problems"]["leak"] is False
    assert record["details"]["local_unit_id"] == LOCAL_ID
    assert record["details"]["mode"] == "0640"
    assert record["listing"] == {"rc": 0, "unit": True, "stage": True, "crash": True, "app": True}
    # The one unit is written into the installed /etc, mode 0644 and never /usr.
    unit = root / "etc/systemd/system" / UNIT
    assert unit.read_text().startswith("[Unit]\nDescription=snow-qa: injected local failure")
    assert unit.stat().st_mode & 0o777 == 0o644 and not (root / "usr/lib/systemd/system").exists()
    # The stager ran in a network namespace of its own, and the journal got one datagram.
    assert ["unshare", "--net", "--", "/usr/libexec/bootc-update-stage"] in calls
    assert any(call[:2] == ["kill", 4242] for call in calls)
    fields = dict(line.split("=", 1) for line in JournalSocket.sent[0].decode().splitlines())
    assert fields["FROSTYARD_CODE"] == "lab.injected_t857" and fields["PRIORITY"] == "3"
    assert fields["MESSAGE_ID"] == qa.PROBLEMS_APP_ID
    # Two collector runs, the second one after every injection.
    assert calls.count(["systemctl", "start", "snosi-health.service"]) == 2


def test_guest_problems_without_a_collector_skips_every_injection(qa, tmp_path, monkeypatch):
    m, credentials, root, calls = guest_environment(qa, tmp_path, monkeypatch, collector=False,
                                                   problems_file=False, details_file=False)
    record = payload(qa.problems(credentials))
    assert record["collector"] is False
    assert record["injected"] is None and record["collector_runs"] is None
    assert record["problems"] is None and record["details"] is None and record["listing"] is None
    # A pre-T857 image still reports its boot time.
    assert record["boot"]["target"] == "multi-user.target"
    assert not (root / "etc/systemd/system" / UNIT).exists()
    assert ["systemctl", "start", "snosi-health.service"] not in calls
    assert ["unshare", "--net", "--", "/usr/libexec/bootc-update-stage"] not in calls


def test_guest_problems_without_a_problems_file_records_none(qa, tmp_path, monkeypatch):
    m, credentials, root, calls = guest_environment(qa, tmp_path, monkeypatch, problems_file=False)
    record = payload(qa.problems(credentials))
    assert record["collector"] is True
    assert record["problems"] is None
    assert record["details"] is not None and record["listing"] is not None


def test_guest_problems_keeps_a_too_large_document_as_none(qa, tmp_path, monkeypatch):
    big = collector_document()
    big["problems"] = [dict(big["problems"][1], id="%016x" % number) for number in range(200)]
    m, credentials, root, calls = guest_environment(qa, tmp_path, monkeypatch, document=big)
    record = payload(qa.problems(credentials))
    # 200 records are far above the 6500 byte document bound: never a partial parse.
    assert record["problems"]["doc"] is None and record["problems"]["size"] > 6500
    assert record["problems"]["leak"] is False


def test_guest_problems_reports_a_leaked_local_unit_name(qa, tmp_path, monkeypatch):
    leaky = collector_document()
    leaky["problems"][0]["detail"] = {"result": "exit-code", "unit": UNIT}
    m, credentials, root, calls = guest_environment(qa, tmp_path, monkeypatch, document=leaky)
    record = payload(qa.problems(credentials))
    assert record["problems"]["leak"] is True


# ------------------------------------------------------------ the record channel

def qa_token_bound(problems):
    """The serial line bound of one problems record."""
    return problems.PROBLEMS_TOKEN_MAX + 64


def record_line(problems, record):
    return "SNOW_PROBLEMS_V1 " + base64.urlsafe_b64encode(
        problems.canonical(record).strip()).decode().rstrip("=")


def full_record(**overrides):
    record = dict(phase="problems", nonce=NONCE, boot_id=BOOT_ID, booted_digest=N, path="fresh",
                  boot={"target": "multi-user.target", "firmware_ms": 800, "loader_ms": 200,
                        "kernel_ms": 900, "initrd_ms": 3100, "userspace_ms": 5000, "target_ms": 9400},
                  collector=True, timer={"enabled": "enabled", "active": "active"},
                  preset_reconcile="success", coredump_user=True, journal_dir=True, core_pattern=True,
                  injected={"unit": True, "stage": {"outcome": "failed", "error_class": "network"},
                            "crash": True, "app": True},
                  collector_runs=[0, 0],
                  problems={"mode": "0644", "uid": 0, "gid": 0, "size": 4096,
                            "doc": collector_document(), "leak": False},
                  details={"mode": "0640", "uid": 0, "group": "sudo", "local_unit_id": LOCAL_ID},
                  listing={"rc": 0, "unit": True, "stage": True, "crash": True, "app": True})
    record.update(overrides)
    return record


def serial_with(problems, *records):
    return "\n".join(["boot message unrelated"] + [record_line(problems, item) for item in records]) + "\n"


def test_phase_accepts_the_one_complete_record_and_writes_it(problems, qa, tmp_path):
    record = full_record()
    serial = tmp_path / "serial"
    serial.write_text(serial_with(problems, record))
    destination = tmp_path / "problems.json"
    assert problems.phase(probe_manifest(), "problems", NONCE, "none", str(serial),
                          str(destination)) is None
    assert json.loads(destination.read_text()) == record
    assert destination.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("change,code", [
    (lambda r: r.update(nonce="f" * 32), "record_identity"),
    (lambda r: r.update(phase="login-1"), "record_identity"),
    (lambda r: r.update(booted_digest=NEXT), "booted_image"),
    (lambda r: r.update(path="update"), "probed_path"),
    (lambda r: r.update(collector="yes"), "record_fields"),
    (lambda r: r.update(collector_runs=[0]), "record_runs"),
    (lambda r: r.update(timer={"enabled": "enabled"}), "record_timer"),
    (lambda r: r.update(boot={"target": "multi-user.target"}), "record_boot"),
])
def test_phase_rejects_a_foreign_or_malformed_record(problems, tmp_path, change, code):
    record = full_record()
    change(record)
    serial = tmp_path / "serial"
    serial.write_text(serial_with(problems, record))
    with pytest.raises(problems.GateError, match="^%s$" % code):
        problems.phase(probe_manifest(), "problems", NONCE, "none", str(serial),
                       str(tmp_path / "out.json"))


def test_phase_accepts_the_update_path_only_with_the_next_image(problems, tmp_path):
    record = full_record(path="update", booted_digest=NEXT)
    serial = tmp_path / "serial"
    serial.write_text(serial_with(problems, record))
    problems.phase(probe_manifest("update"), "problems", NONCE, "none", str(serial),
                   str(tmp_path / "out.json"))
    # The update path proves the first boot of N+1, never the installed N.
    serial.write_text(serial_with(problems, full_record(path="update", booted_digest=N)))
    with pytest.raises(problems.GateError, match="^booted_image$"):
        problems.phase(probe_manifest("update"), "problems", NONCE, "none", str(serial),
                       str(tmp_path / "out.json"))
    # And the update record is never the fresh path's proof.
    serial.write_text(serial_with(problems, full_record(path="update", booted_digest=NEXT)))
    with pytest.raises(problems.GateError, match="^probed_path$"):
        problems.phase(probe_manifest("fresh"), "problems", NONCE, "none", str(serial),
                       str(tmp_path / "out.json"))


def test_phase_refuses_a_reused_boot_id_and_two_records(problems, tmp_path):
    serial = tmp_path / "serial"
    serial.write_text(serial_with(problems, full_record()))
    with pytest.raises(problems.GateError, match="^boot_identity$"):
        problems.phase(probe_manifest(), "problems", NONCE, BOOT_ID, str(serial),
                       str(tmp_path / "out.json"))
    # Two different records contradict each other; identical copies are one.
    second = full_record(nonce="f" * 32)
    serial.write_text(serial_with(problems, full_record(), second))
    with pytest.raises(problems.GateError, match="^record_count$"):
        problems.phase(probe_manifest(), "problems", NONCE, "none", str(serial),
                       str(tmp_path / "out.json"))
    serial.write_text(serial_with(problems, full_record(), full_record()))
    problems.phase(probe_manifest(), "problems", NONCE, "none", str(serial),
                   str(tmp_path / "out.json"))


def test_phase_never_reads_a_kernel_log_chunk_as_a_record(problems, tmp_path):
    # The host joins the kmsg chunks first (ADR-0014); a slice of only chunk
    # carrier lines holds no record.
    line = record_line(problems, full_record())
    chunks = [("SNOW_QA_KMSG1 0123456789abcdef 1/1 aaaaaaaa %s" %
               base64.urlsafe_b64encode(chunk).decode().rstrip("=")).encode()
              for chunk in [line[:40].encode()]]
    serial = tmp_path / "serial"
    serial.write_bytes(b"\n".join(chunks) + b"\n")
    with pytest.raises(problems.GateError, match="^record_count$"):
        problems.phase(probe_manifest(), "problems", NONCE, "none", str(serial),
                       str(tmp_path / "out.json"))


def test_phase_refuses_a_token_above_the_bound(problems, tmp_path):
    record = full_record()
    record["problems"]["doc"] = collector_document(padding="x" * 9000)
    line = record_line(problems, record)
    assert len(line) > qa_token_bound(problems)
    serial = tmp_path / "serial"
    serial.write_text(line + "\n")
    with pytest.raises(problems.GateError, match="^record_encoding$"):
        problems.phase(probe_manifest(), "problems", NONCE, "none", str(serial),
                       str(tmp_path / "out.json"))


def test_phase_main_prints_only_a_bounded_code_on_error(problems, qa, tmp_path, capsys):
    serial = tmp_path / "serial"
    serial.write_text("SNOW_PROBLEMS_V1 not-a-record!\n")
    manifest_path = normalized(tmp_path, probe_manifest(), qa)
    assert problems.main(["phase", str(manifest_path), "problems", NONCE, "none", str(serial),
                          str(tmp_path / "out.json")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "qa_failed:record_encoding"


# --------------------------------------------------------------- the report

def run_report(problems, qa, tmp_path, record, product="snow", capsys=None):
    path = tmp_path / "problems.json"
    path.write_bytes(problems.canonical(record))
    manifest_path = normalized(tmp_path, probe_manifest("fresh", product), qa)
    assert problems.main(["report", str(manifest_path), str(path)]) == 0
    out = capsys.readouterr().out.splitlines()
    return out[0], out[1:]


def test_report_of_a_full_pass_is_ok(problems, qa, tmp_path, capsys):
    summary, detail = run_report(problems, qa, tmp_path, full_record(), capsys=capsys)
    assert summary == "problems=ok"
    assert len(detail) == 6
    assert detail[0] == ("path=fresh collector=yes timer=enabled/active preset_reconcile=success "
                         "coredump_user=yes core_pattern=yes journal_dir=yes")
    assert detail[1] == ("file=0644 owner=0:0 schema=valid records=4 reportable=3 "
                         "details=0640 group=sudo")
    assert detail[2] == ("injected unit=yes stage=failed/network crash=yes app=yes "
                         "collector_runs=0,0")
    assert detail[3] == "recorded unit=yes stage=yes crash=yes app=yes leak=no"
    assert detail[4] == "list rc=0 unit=yes stage=yes crash=yes app=yes"
    assert detail[5] == ("boot target=multi-user.target firmware_ms=800 loader_ms=200 kernel_ms=900 "
                         "initrd_ms=3100 userspace_ms=5000 target_ms=9400")


@pytest.mark.parametrize("product", ["snow", "snowfield", "sundog"])
def test_report_never_requires_the_listing_on_a_desktop_product(problems, qa, tmp_path, capsys, product):
    record = full_record(listing={"rc": 1, "unit": False, "stage": False, "crash": False, "app": False})
    summary, _ = run_report(problems, qa, tmp_path, record, product=product, capsys=capsys)
    assert summary == "problems=ok"


def test_report_requires_the_listing_on_floe(problems, qa, tmp_path, capsys):
    record = full_record(listing={"rc": 1, "unit": False, "stage": False, "crash": False, "app": False})
    summary, detail = run_report(problems, qa, tmp_path, record, product="floe", capsys=capsys)
    assert summary.startswith("problems=missing-") and summary.endswith("list")
    assert detail[4] == "list rc=1 unit=no stage=no crash=no app=no"


def test_report_of_a_pre_t857_image_is_unproven(problems, qa, tmp_path, capsys):
    record = full_record(collector=False, injected=None, collector_runs=None, problems=None,
                         details=None, listing=None, coredump_user=False, core_pattern=False,
                         journal_dir=False, timer={"enabled": "other", "active": "other"},
                         boot=None)
    summary, detail = run_report(problems, qa, tmp_path, record, capsys=capsys)
    assert summary == "problems=unproven-no-collector"
    # The boot time is evidence even when nothing else could be read.
    assert detail[5] == "boot target=none"
    assert detail[2] == "injected unit=no stage=none/none crash=no app=no collector_runs=none"
    assert detail[4] == "list rc=none unit=no stage=no crash=no app=no"


def test_report_of_a_too_large_document_is_unproven(problems, qa, tmp_path, capsys):
    record = full_record(problems={"mode": "0644", "uid": 0, "gid": 0, "size": 65536,
                                   "doc": None, "leak": False})
    summary, detail = run_report(problems, qa, tmp_path, record, capsys=capsys)
    assert summary == "problems=unproven-too-large"
    assert detail[1].startswith("file=0644 owner=0:0 schema=none records=0 reportable=0")


@pytest.mark.parametrize("change,failed", [
    (lambda r: r["problems"].update(mode="0600"), "file"),
    (lambda r: r["problems"].update(uid=1000), "file"),
    (lambda r: r["problems"]["doc"]["problems"][1].update(signal="SIGSEGV\n"), "schema"),
    (lambda r: r["problems"]["doc"].update(extra=1), "schema"),
    (lambda r: r["timer"].update(enabled="disabled"), "timer"),
    (lambda r: r.update(coredump_user=False), "coredump"),
    (lambda r: r.update(journal_dir=False), "journal"),
    (lambda r: r["details"].update(group="users"), "details"),
    (lambda r: r["details"].update(mode="0644"), "details"),
    (lambda r: r["problems"].update(leak=True), "unit"),
    (lambda r: r["problems"]["doc"].update(problems=r["problems"]["doc"]["problems"][1:]), "unit"),
    (lambda r: r["problems"]["doc"].update(
        problems=[e for e in r["problems"]["doc"]["problems"] if e["id"] != "0123456789abcded"]), "stage"),
    (lambda r: r["problems"]["doc"]["problems"][1].update(detail={"exe": "sh", "signal": "SIGSEGV"}),
     "crash"),
    (lambda r: r["problems"]["doc"].update(problems=r["problems"]["doc"]["problems"][:3]), "app"),
    (lambda r: r["problems"]["doc"]["problems"][3].update(reportable=False), "app"),
])
def test_report_names_every_missing_check(problems, qa, tmp_path, capsys, change, failed):
    record = full_record()
    change(record)
    summary, _ = run_report(problems, qa, tmp_path, record, capsys=capsys)
    assert summary == "problems=missing-" + failed


def test_report_names_every_failed_check_in_a_fixed_order(problems, qa, tmp_path, capsys):
    record = full_record(problems=None, details=None, journal_dir=False)
    summary, detail = run_report(problems, qa, tmp_path, record, capsys=capsys)
    assert summary == "problems=missing-file+schema+journal+details+unit+stage+crash+app"
    assert detail[1] == "file=none owner=none schema=none records=0 reportable=0 details=none group=none"


# ---------------------------------------------------------- the schema validator

def test_the_frozen_v1_copy_is_core_at_the_pinned_sha256(scripts_dir):
    assert (scripts_dir / "problem-report-v1.json").read_text() == scripts()["problem-report-v1.json"]
    assert hashlib.sha256((scripts_dir / "problem-report-v1.json").read_bytes()).hexdigest() == V1_SHA256


def test_the_validator_accepts_the_collectors_document(problems):
    root = json.loads((Path(problems.__file__).with_name("problem-report-v1.json")).read_bytes())
    assert problems.document_ok(collector_document(), root) is True
    # Every reportable record validates as the envelope snosi would send.
    for entry in collector_document()["problems"]:
        problems.problem_ok(entry, root, collector_document())


def test_the_validator_refuses_an_unknown_keyword(problems):
    root = json.loads((Path(problems.__file__).with_name("problem-report-v1.json")).read_bytes())
    # An unknown keyword is refused, never ignored: this validator implements
    # exactly the v1 subset, so it can never accept more than core's schema.
    root["$defs"]["image_version"]["minProperties"] = 1
    with pytest.raises(problems.GateError, match="^schema_keyword$"):
        problems.validate(root["$defs"]["image_version"], "20260924000000", root)
    assert problems.document_ok(collector_document(), root) is False


def test_the_validator_refuses_an_extra_field(problems):
    root = json.loads((Path(problems.__file__).with_name("problem-report-v1.json")).read_bytes())
    with pytest.raises(problems.GateError, match="^schema_required$|^schema_type$"):
        problems.validate({"type": "object", "required": ["a"]}, {}, root)
    document = collector_document()
    document["problems"][0]["extra"] = "x"
    assert problems.document_ok(document, root) is False
    document = collector_document()
    document["problems"][1]["detail"]["frames"] = ["main.main"]
    assert problems.document_ok(document, root) is False


def test_the_validator_reads_ecma_262_dollar_anchors(problems):
    root = json.loads((Path(problems.__file__).with_name("problem-report-v1.json")).read_bytes())
    # ECMA-262 `$` matches only at the very end; Python's `$` also matches before a newline.
    with pytest.raises(problems.GateError, match="^schema_pattern$"):
        problems.validate(root["properties"]["week"], WEEK + "\n", root)
    with pytest.raises(problems.GateError, match="^schema_pattern$"):
        problems.validate(root["$defs"]["image_version"], "20260924000000\n", root)
    document = collector_document()
    document["problems"][1]["week"] = WEEK + "\n"
    assert problems.document_ok(document, root) is False


# ------------------------------------------------------------- run.sh wiring

def plan_block():
    return re.search(r"(?ms)^problems_mode=\$\(python3.*?^fi\n", run_script()).group()


def test_run_script_plan_for_both_paths(tmp_path):
    for path, plan in (("fresh", "problems"), ("update", "installed-n stage problems")):
        work = tmp_path / path
        work.mkdir()
        (work / "normalized.json").write_bytes(
            json.dumps(probe_manifest(path)).encode())
        script = ("set -Eeuo pipefail\nfail() { exit 9; }\n"
                  f"WORK={work} sysext_mode=0 login_mode=0\n" + plan_block() +
                  '\nprintf "%s\\n" "${phase_plan[*]}"\n')
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == plan


def report_block():
    return re.search(r'(?ms)^if \[\[ "\$problems_mode" == 1 \]\]; then\n.*?^fi\n', run_script()).group()


def run_report_block(tmp_path, stdout):
    block = report_block().replace("/opt/snow-qa/problems.py", str(tmp_path / "problems.py"))
    (tmp_path / "problems.py").write_text(stdout)
    work, out, evidence = tmp_path / "work", tmp_path / "results", tmp_path / "evidence"
    for folder in (work, out, evidence):
        folder.mkdir(exist_ok=True)
    (out / "checks.txt").write_text("")
    script = (
        'set -Eeuo pipefail\n'
        'fail() { printf "FAIL:%s\\n" "$1"; exit 9; }\n'
        'blocked() { printf "BLOCKED:%s\\n" "$1"; exit 0; }\n'
        'persist() { cp -- "$1" "$2"; }\n'
        f'WORK={work} OUT={out} EVIDENCE={evidence} problems_mode=1\n' + block)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result, work, out, evidence


@pytest.mark.parametrize("summary,verdict", [
    ("ok", ""),
    ("missing-file+schema", ""),
    ("unproven-no-collector", "BLOCKED:problems_unproven-no-collector"),
    ("unproven-too-large", "BLOCKED:problems_unproven-too-large"),
])
def test_run_script_reports_the_problems_summary_and_blocks_only_unproven(tmp_path, summary, verdict):
    stub = 'print("problems=%s")\nprint("path=fresh collector=yes")\n' % summary
    result, _, out, evidence = run_report_block(tmp_path, stub)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == verdict
    assert (out / "checks.txt").read_text().splitlines()[-1] == "problems=" + summary
    assert (out / "problems.txt").read_text().splitlines()[0] == "problems=" + summary
    assert (evidence / "problems.txt").read_text() == (out / "problems.txt").read_text()


@pytest.mark.parametrize("stdout", [
    'print("problems=OK")\n',
    'print("problems=")\n',
    'print("something else")\n',
    'import sys\nsys.exit(1)\n',
])
def test_run_script_problems_report_fails_closed_on_a_bad_summary(tmp_path, stdout):
    result, _, _, evidence = run_report_block(tmp_path, stdout)
    assert result.stdout.strip() == "FAIL:problems_report"
    assert not (evidence / "problems.txt").exists()


def test_run_script_carries_the_problems_error_and_record_needles():
    script = run_script()
    assert "problems_guest_error" in script and "SNOW_PROBLEMS_ERR" in script
    assert '"$needle" == SNOW_PROBLEMS_V1 ]] && vm_stopped' in script
    # The phase error parser never reads a chunk carrier line as a code.
    assert "b'SNOW_PROBLEMS_ERR' in line and b'SNOW_QA_KMSG1' not in line" in script
    # The report is persisted best effort, like the first-login report.
    assert 'persist "$OUT/problems.txt" "$EVIDENCE/problems.txt" || true' in script


def test_manual_template_publishes_the_problems_output():
    path = SOURCE.parent / "run-snow-bootc-lifecycle.yaml"
    template = yaml.safe_load(path.read_text())
    entry = next(t for t in template["spec"]["templates"] if t["name"] == template["spec"]["entrypoint"])
    outputs = {o["name"]: o for o in entry["outputs"]["parameters"]}
    assert outputs["problems"]["valueFrom"]["path"] == "/tmp/results/problems.txt"
    assert outputs["problems"]["valueFrom"]["default"] == "evidence unavailable"
