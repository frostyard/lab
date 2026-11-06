"""Offline contract tests for the report-only report probe (plan T857 phase 2).

The probe proves on a real installed Floe VM that the snosi problem-report sender
sends nothing while reporting is off and, when it is on, sends the injected
problem and 1 weekly count to a lab intake. It is report-only: it never fails the
run for a product finding, and it never passes when it proved nothing.
"""
import base64
import importlib
import importlib.util
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from test_snow_bootc_lifecycle import N, NEXT, NONCE, REPO, SOURCE, manifest, run_script

BOOT_ID = "01234567-89ab-4cde-8f01-23456789abcd"
WEEK = "2026-W45"
APP_ID = "11111111-1111-4111-8111-111111111111"
COUNT_ID = "22222222-2222-4222-8222-222222222222"
PEER = "10.0.0.9"
INTAKE_URL = "http://10.0.0.5:8787"
ENDPOINT = INTAKE_URL + "/v1/report"
SENDER = "/usr/libexec/snosi-health-report"
CODE = "lab.injected_t857"


def scripts():
    return yaml.safe_load(SOURCE.read_text())["data"]


# --------------------------------------------------------------- fixtures

@pytest.fixture
def scripts_dir(tmp_path, monkeypatch):
    """Every key of the ConfigMap, as the pod mounts them."""
    folder = tmp_path / "scripts"
    folder.mkdir()
    data = scripts()
    for name in ("qa.py", "problems.py", "report.py", "report-guest.py", "intake-lab.py",
                 "intake-setup.sh", "problem-report-v1.json"):
        (folder / name).write_text(data[name])
    monkeypatch.syspath_prepend(str(folder))
    for module in ("qa", "problems"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    return folder


@pytest.fixture
def qa(scripts_dir):
    return importlib.import_module("qa")


@pytest.fixture
def report(scripts_dir):
    spec = importlib.util.spec_from_file_location("snow_report", scripts_dir / "report.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guest(scripts_dir):
    spec = importlib.util.spec_from_file_location("snow_report_guest", scripts_dir / "report-guest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lab(scripts_dir):
    spec = importlib.util.spec_from_file_location("snow_intake_lab", scripts_dir / "intake-lab.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guest_module(guest, qa):
    """report-guest.py as qa.py runs it: with its own `qa`, and no real system."""
    guest.qa = qa
    return guest


def probe_manifest(intake="lab", product="floe"):
    """The manifest with the report_probe key, for any product."""
    m = manifest()
    repo = f"ghcr.io/frostyard/{product}"
    m.update(family=product, product=product, image_n=f"{repo}@sha256:{N}",
             image_n_plus_1=f"{repo}@sha256:{NEXT}",
             version_tag_n=f"{repo}:{m['version_n']}",
             version_tag_n_plus_1=f"{repo}:{m['version_n_plus_1']}",
             target_ref=f"{repo}:qa-controlled")
    m["report_probe"] = {"intake": intake}
    return m


def normalized(tmp_path, m, qa):
    path = tmp_path / "normalized.json"
    path.write_bytes(qa.canonical(m))
    return path


# ---------------------------------------------------------------- manifest

@pytest.mark.parametrize("intake", ["lab", "production"])
def test_report_probe_normalizes_for_a_floe_manifest(qa, intake):
    m = probe_manifest(intake)
    assert qa.normalize(m) is m
    assert qa.normalize(m)["report_probe"] == {"intake": intake}
    assert "report_probe" not in qa.FIELDS


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra="lab"),
    lambda p: p.update(intake="both"),
    lambda p: p.update(intake=""),
    lambda p: p.update(intake=True),
    lambda p: p.pop("intake"),
    lambda p: p.update(**{"intake": "lab", "endpoint": "http://x"}),
])
def test_report_probe_rejects_unsafe_input(qa, change):
    m = probe_manifest()
    change(m["report_probe"])
    with pytest.raises(qa.GateError, match=r"^[a-z_]{1,40}$"):
        qa.normalize(m)


@pytest.mark.parametrize("product", ["snow", "floe", "snowfield", "sundog"])
@pytest.mark.parametrize("intake", ["lab", "production"])
def test_report_probe_normalizes_for_every_product(qa, product, intake):
    # Floe carries the snosi sender; the desktops carry chairlift, which sends
    # from the user's own session. Both are the same manifest key.
    m = probe_manifest(intake, product)
    assert qa.normalize(m) is m
    assert qa.normalize(m)["report_probe"] == {"intake": intake}


@pytest.mark.parametrize("other", [
    {"problems_probe": {"path": "fresh"}},
    {"first_login_probe": {"session_wait": 120, "app_wait": 60}},
    {"sysext_probe": {"features": ["notesd"], "units": ["frostmail.service"], "first_start": []}},
])
def test_report_probe_never_travels_with_the_other_probes(qa, other):
    m = probe_manifest()
    m.update(other)
    with pytest.raises(qa.GateError, match="^manifest_fields$"):
        qa.normalize(m)


def test_report_probe_never_stages_n_plus_1(qa):
    # The report boot is the installed N: the versions only have to differ.
    m = probe_manifest()
    m["version_n"], m["version_n_plus_1"] = "20260924000000", "20260923000000"
    m["version_tag_n"] = f"ghcr.io/frostyard/floe:20260924000000"
    m["version_tag_n_plus_1"] = f"ghcr.io/frostyard/floe:20260923000000"
    assert qa.normalize(m)["version_n"] == "20260924000000"
    m["version_n_plus_1"] = "20260924000000"
    m["version_tag_n_plus_1"] = "ghcr.io/frostyard/floe:20260924000000"
    with pytest.raises(qa.GateError, match="^version_distinct$"):
        qa.normalize(m)


def test_the_report_marker_is_a_complete_record_line(qa):
    # The host joins kernel-log chunks into record lines; the report line is one
    # of them, with its own bound, and its error line carries a bounded code.
    assert (b"SNOW_REPORT_V1", rb"[A-Za-z0-9_-]{1,%d}" % (qa.REPORT_TOKEN_MAX + 64)) in qa.COMPLETE_LINES
    assert (b"SNOW_REPORT_ERR", rb"[a-z_]{1,40}") in qa.COMPLETE_LINES
    assert b"SNOW_REPORT_V1" in qa.MARKER_LINE and b"SNOW_REPORT_ERR" in qa.MARKER_LINE
    assert qa.main(["report"]) == 1


# ---------------------------------------------------------------- the guest

def guest_document(kind, report_id, **detail):
    document = {"schema": "frostyard.problem/1", "report_id": report_id, "week": WEEK,
                "image": {"id": "floe", "version": "20260924000000"}, "secure": True,
                "client": "snosi-health/20260924000000", "kind": kind, "detail": detail}
    if kind == "app_problem":
        document["detail"] = dict({"app_id": "org.frostyard.nsl", "app_version": "0.0.0-rc.857",
                                   "runtime": "go", "component": "cli", "class": "error",
                                   "code": CODE}, **detail)
    if kind == "count":
        document["detail"] = {"install_age": "<1w", "channel": "latest"}
    return document


def sent_line(report_id, kind, status="accepted", http_status=202, problem_id="0123456789abcdef"):
    return json.dumps({"status": status, "http_status": http_status, "problem_id": problem_id,
                       "report": {"kind": kind, "report_id": report_id}}) + "\n"


COLLECTOR_CRASH = {"format": 1, "problems": [
    {"id": "0123456789abcdef", "kind": "crash", "detail": {"exe": "sleep", "signal": "SIGSEGV"}},
    {"id": "0123456789abcdee", "kind": "app_problem", "detail": {}}]}


def collector_document(crash=True, app=True):
    """A problems.json the collector could write after the 2 injections."""
    entries = []
    if crash:
        entries.append({"id": "0123456789abcdef", "kind": "crash",
                        "detail": {"exe": "sleep", "signal": "SIGSEGV"}})
    if app:
        entries.append({"id": "0123456789abcdee", "kind": "app_problem", "detail": {}})
    return json.dumps({"format": 1, "problems": entries}).encode()


def guest_environment(qa, monkeypatch, module, tmp_path, *, intake="lab", sender=True,
                      condition="no", off_message=True, on=True, enabled=True, endpoint=True,
                      triggered=True, result="success", sent_lines=(), documents=(),
                      status=202, intake_credential=INTAKE_URL, crash=True):
    """One installed Floe VM: its tree, its credentials and its commands, stubbed."""
    m = probe_manifest(intake)
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    for name, value in {"phase": "report", "nonce": NONCE, "image_n": m["image_n"],
                        "image_n_plus_1": m["image_n_plus_1"],
                        "target_ref": m["target_ref"]}.items():
        (credentials / name).write_text(value + "\n")
    boot = tmp_path / "boot_id"
    boot.write_text(BOOT_ID + "\n")
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized(tmp_path, m, qa))
    monkeypatch.setattr(qa, "GUEST_PATHS", {**qa.GUEST_PATHS, "boot_id": boot})
    sender_path = tmp_path / "snosi-health-report"
    if sender:
        sender_path.write_text("#!/bin/bash\n")
    monkeypatch.setattr(module, "SENDER", str(sender_path))
    monkeypatch.setattr(module, "CONF_DIR", tmp_path / "etc-snosi-health")
    conf = tmp_path / "reporting.conf"
    monkeypatch.setattr(module, "CONF", conf)
    monkeypatch.setattr(module, "SENT", tmp_path / "sent.jsonl")
    monkeypatch.setattr(module, "BODY", str(tmp_path / "snow-report.json"))
    problems = tmp_path / "problems.json"
    problems.write_bytes(collector_document(crash=crash))
    monkeypatch.setattr(module, "PROBLEMS", str(problems))
    credential = tmp_path / "snow-intake"
    credential.write_text(intake_credential + "\n")
    monkeypatch.setattr(module, "INTAKE_CREDENTIAL", str(credential))
    calls, posts = [], []
    state = {"invocations": 0}

    def run(args, timeout=120):
        calls.append(args)
        if args[0] == "systemctl":
            if args[1] in ("start", "restart"):
                if args[1] == "restart":
                    state["invocations"] += 1
                return 0, b""
            if args[1] == "is-enabled":
                return 0, b"enabled\n"
            # 1 property per call: systemctl show -p <name> --value <unit>.
            key = args[3] if len(args) > 3 else ""
            if key == "ConditionResult":
                return 0, (condition + "\n").encode()
            if key == "InvocationID":
                return 0, (("%032x" % (1 + state["invocations"])).encode() + b"\n")
            if key == "ActiveState":
                return 0, b"inactive\n"
            return 0, (result + "\n").encode()
        if args[0] == str(sender_path):
            return 1, (b"problem reporting is off\n" if off_message else b"")
        if args[0] == "/usr/bin/snosi-health":
            if args[1] == "reporting":
                if args[2] == "off":
                    return 0, b""
                if args[2] == "on":
                    conf.write_text(("enabled=1\n" if enabled else "")
                                    + ("endpoint=" + ENDPOINT + "\n" if endpoint else ""))
                    if on:
                        # The sender writes its log only once reporting is on.
                        (tmp_path / "sent.jsonl").write_text("".join(sent_lines))
                    return (0 if on else 1), b""
                return 0, b"reporting: on\n"
            return 0, b"".join(json.dumps(doc).encode() + b"\n" for doc in documents)
        if args[0] == "curl":
            return 0, str(status).encode()
        return 0, b""

    monkeypatch.setattr(qa, "problems_run", run)
    monkeypatch.setattr(qa, "problems_app", lambda: True)
    monkeypatch.setattr(qa, "problems_crash", lambda: True)
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: None)

    def fake_post(url, timeout=30):
        posts.append(url)
        return 204, b""

    monkeypatch.setattr(module, "post", fake_post)
    published = []
    monkeypatch.setattr(qa, "publish", published.append)

    def fake_status(args, **kwargs):
        assert args[:2] == ["bootc", "status"], args
        document = {"spec": {"image": {"image": f"{m['target_ref']}", "transport": "registry"}},
                    "status": {"booted": {"image": {"imageDigest": "sha256:" + N,
                                                   "version": "20260923000000"}},
                               "staged": None, "rollback": None}}
        return subprocess.CompletedProcess(args, 0, json.dumps(document).encode(), b"")

    monkeypatch.setattr(qa.subprocess, "run", fake_status)
    return m, credentials, calls, posts, published


def payload(line):
    assert line.startswith("SNOW_REPORT_V1 "), line[:40]
    encoded = line.split(" ", 1)[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def test_guest_report_records_the_off_and_on_states(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, calls, posts, published = guest_environment(
        qa, monkeypatch, guest_module, tmp_path, documents=(guest_document("count", COUNT_ID),),
        sent_lines=(sent_line(APP_ID, "app_problem"),
                    sent_line(COUNT_ID, "count", problem_id="0123456789abcdee")))
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert set(record) == guest_module.REPORT_RECORD_FIELDS
    assert record["phase"] == "report" and record["nonce"] == NONCE
    assert record["boot_id"] == BOOT_ID and record["booted_digest"] == N
    assert record["intake"] == "lab" and record["sender"] is True
    assert record["injected"] == {"app": True, "crash": True}
    assert record["off"]["conf_absent"] is True and record["off"]["condition"] == "no"
    assert record["off"]["send_off"] is True and record["off"]["sent_absent"] is True
    assert record["off"]["endpoint"] is True and record["off"]["status_rc"] == 0
    assert record["consent"] == {"on_rc": 0, "enabled": True, "endpoint": True, "status": "reporting: on"}
    assert record["timer"] == {"triggered": True, "result": "success", "waited": 0, "enabled": "enabled"}
    assert record["dry"] == ["count"]
    assert [entry["report_id"] for entry in record["sent"]] == [APP_ID, COUNT_ID]
    assert record["sent"][0]["kind"] == "app_problem" and record["sent"][0]["status"] == "accepted"
    assert record["production"] is None
    # The marks surround the consent change, and only the lab intake gets them.
    assert posts == [INTAKE_URL + "/lab/mark/off-begin", INTAKE_URL + "/lab/mark/on-begin",
                     INTAKE_URL + "/lab/mark/on-end"]
    # The administrator's endpoint line was appended while reporting was off.
    assert ("endpoint=" + ENDPOINT) in (tmp_path / "reporting.conf").read_text()
    # 2 collector runs after the injections, like the problems probe.
    assert calls.count(["systemctl", "start", "snosi-health.service"]) == 2


def test_guest_report_without_a_sender_records_nothing_else(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, calls, posts, published = guest_environment(
        qa, monkeypatch, guest_module, tmp_path, sender=False)
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert record["sender"] is False and record["intake"] == "lab"
    assert all(record[name] is None for name in ("injected", "off", "consent", "timer", "sent", "dry"))
    assert posts == [] and calls == []
    assert not (tmp_path / "reporting.conf").exists()


@pytest.mark.parametrize("credential", [
    "http://10.0.0.5:8788", "https://10.0.0.5:8787", "http://intake:8787",
    "http://10.0.0.5:8787/", "http://10.0.0.5:8787\nhttp://10.0.0.6:8787",
])
def test_guest_report_refuses_a_foreign_intake_credential(qa, monkeypatch, guest_module, tmp_path, credential):
    m, credentials, calls, posts, published = guest_environment(
        qa, monkeypatch, guest_module, tmp_path, intake_credential=credential)
    assert guest_module.main(credentials) == 1
    assert published == ["SNOW_REPORT_ERR intake_credential"]
    assert posts == []


def test_guest_report_refuses_a_manifest_without_the_report_probe(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, calls, posts, published = guest_environment(qa, monkeypatch, guest_module, tmp_path)
    plain = manifest()
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized(tmp_path, plain, qa))
    assert guest_module.main(credentials) == 1
    assert published == ["SNOW_REPORT_ERR report_manifest"]


def test_guest_report_never_proves_production_sends_a_crash(qa, monkeypatch, guest_module, tmp_path):
    """The production path sends 1 app_problem document and nothing else."""
    m, credentials, calls, posts, published = guest_environment(
        qa, monkeypatch, guest_module, tmp_path, intake="production",
        documents=(guest_document("app_problem", APP_ID), guest_document("count", COUNT_ID)))
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert record["intake"] == "production" and record["production"]["http_status"] == 202
    assert record["production"]["report_id"] == APP_ID
    assert all(record[name] is None for name in ("off", "consent", "timer", "sent", "dry", "injected"))
    assert posts == []      # no mark on the production path
    curl = next(args for args in calls if args[0] == "curl")
    # curl sends only that one document, never a redirect, and only over https.
    assert "=https" in curl and "-L" not in curl and curl[-1] == guest_module.PRODUCTION_URL
    assert curl[curl.index("--data-binary") + 1] == "@" + guest_module.BODY


@pytest.mark.parametrize("documents", [
    (),
    ({"schema": "frostyard.problem/1", "kind": "app_problem", "detail": {"code": "other"}},),
    ({"schema": "frostyard.problem/1", "kind": "count", "detail": {}},),
])
def test_guest_report_refuses_an_unexpected_production_document(qa, monkeypatch, guest_module, tmp_path, documents):
    m, credentials, calls, posts, published = guest_environment(
        qa, monkeypatch, guest_module, tmp_path, intake="production", documents=documents)
    assert guest_module.main(credentials) == 1
    assert published == ["SNOW_REPORT_ERR production_document"]
    assert not any(args[0] == "curl" for args in calls)


def test_guest_report_records_the_production_status_it_saw(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, calls, posts, published = guest_environment(
        qa, monkeypatch, guest_module, tmp_path, intake="production", status=429,
        documents=(guest_document("app_problem", APP_ID),))
    assert guest_module.main(credentials) == 0
    assert payload(published[0])["production"] == {"http_status": 429, "report_id": APP_ID}


# ------------------------------------------------------------- report.py record

def full_record(**overrides):
    record = dict(phase="report", nonce=NONCE, boot_id=BOOT_ID, booted_digest=N, sender=True,
                  sender_version=None, intake="lab", session=None,
                  injected={"app": True, "crash": True}, crash_collected=True,
                  off={"conf_absent": True, "service_rc": 1, "condition": "no", "send_rc": 1,
                       "send_off": True, "status_rc": 0, "endpoint": True, "again_rc": 1,
                       "again_result": "success", "sent_absent": True, "dry_rc": 0},
                  consent={"on_rc": 0, "enabled": True, "endpoint": True, "status": "reporting: on"},
                  timer={"triggered": True, "result": "success", "waited": 20, "enabled": "enabled"},
                  sent=[{"status": "accepted", "http_status": 202, "problem_id": "0123456789abcdef",
                         "kind": "app_problem", "report_id": APP_ID},
                        {"status": "accepted", "http_status": 202, "problem_id": "0123456789abcdee",
                         "kind": "count", "report_id": COUNT_ID}],
                  dry=["app_problem", "count"], cli=None, production=None)
    record.update(overrides)
    return record


def production_record(**overrides):
    record = full_record(sender=True, intake="production", injected=None, crash_collected=None,
                         off=None, consent=None, timer=None, sent=None, dry=None,
                         production={"http_status": 202, "report_id": APP_ID})
    record.update(overrides)
    return record


def envelope(report_id, *, bearer=True, event=None, body=None, headers=None):
    """One GlitchTip envelope as the mock writes it: header, item header, event."""
    event = event if event is not None else {"event_id": report_id.replace("-", ""),
                                             "message": "a report", "user": {"ip_address": None}}
    body = body if body is not None else "\n".join([
        json.dumps({"event_id": report_id.replace("-", "")}),
        json.dumps({"type": "event", "length": 1}),
        json.dumps(event)]) + "\n"
    return {"seq": 9, "path": "/api/1/envelope/", "bearer_ok": bearer,
            "headers": headers if headers is not None else
            {"Content-Type": "application/x-sentry-envelope", "Authorization": "bearer_ok: true"},
            "body": body}


def requests_log(*, before_posts=(), after_posts=(), marks_in_order=True, drop=()):
    """The proxy's log: the 3 marks and the reports it forwarded."""
    entries, seq = [], 0

    def add(entry):
        nonlocal seq
        seq += 1
        entries.append(dict(entry, seq=seq))

    mark = lambda name: add({"type": "mark", "method": "POST", "path": "/lab/mark/" + name,
                             "peer": PEER, "status": 204, "name": name, "body": ""})
    send = lambda document, status=202: add({"type": "request", "method": "POST",
                                             "path": "/v1/report", "peer": PEER, "status": status,
                                             "name": None, "body": json.dumps(document)})
    if "off-begin" not in drop:
        mark("off-begin")
    for document in before_posts:
        send(document)
    if "on-begin" not in drop:
        mark("on-begin")
    for document in after_posts:
        send(document)
    if "on-end" not in drop:
        mark("on-end")
    if not marks_in_order:
        # The guest reached the intake in the wrong order: off-begin came last.
        numbers = [entry["seq"] for entry in entries]
        for entry, number in zip(entries, reversed(numbers)):
            entry["seq"] = number
    return entries


def lab_logs(tmp_path, requests, envelopes):
    requests_path = tmp_path / "intake-requests.jsonl"
    requests_path.write_text("".join(json.dumps(entry) + "\n" for entry in requests))
    glitchtip_path = tmp_path / "intake-glitchtip.jsonl"
    glitchtip_path.write_text("".join(json.dumps(entry) + "\n" for entry in envelopes))
    return requests_path, glitchtip_path


def ok_logs(tmp_path, **kwargs):
    document_app = guest_document("app_problem", APP_ID)
    document_count = guest_document("count", COUNT_ID)
    requests = requests_log(after_posts=(document_app, document_count), **kwargs)
    envelopes = [envelope(APP_ID)]
    return lab_logs(tmp_path, requests, envelopes)


def run_report(report, qa, tmp_path, record, *, logs=None, capsys=None):
    path = tmp_path / "report.json"
    path.write_bytes(report.canonical(record))
    manifest_path = normalized(tmp_path, probe_manifest(record["intake"] if record["intake"] == "production" else "lab"), qa)
    if record["intake"] == "production":
        assert report.main(["report", str(manifest_path), str(path)]) == 0
    else:
        requests_path, glitchtip_path = logs if logs is not None else ok_logs(tmp_path)
        assert report.main(["report", str(manifest_path), str(path), str(requests_path),
                            str(glitchtip_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    return out[0], out[1:]


# ---------------------------------------------------------------- the report

def test_report_of_a_full_lab_pass_is_ok(report, qa, tmp_path, capsys):
    summary, detail = run_report(report, qa, tmp_path, full_record(), capsys=capsys)
    assert summary == "report=ok"
    assert detail == [
        "intake=lab sender=yes",
        "marks off-begin=yes on-begin=yes on-end=yes",
        "posted total=2 accepted=2 app_problem=1 count=1 crash=0",
        "report_ids app_problem=%s count=%s" % (APP_ID, COUNT_ID),
        "envelopes=1 bearer_ok=yes",
        "off conf_absent=yes condition=no send_off=yes sent_absent=yes dry=2",
        "consent on_rc=0 enabled=yes endpoint=yes",
        "timer triggered=yes result=success waited=20 enabled=enabled",
        "sent accepted=2 of 2",
    ]


def test_report_of_a_pre_t857_image_is_unproven(report, qa, tmp_path, capsys):
    record = full_record(sender=False, injected=None, crash_collected=None, off=None,
                         consent=None, timer=None, sent=None, dry=None)
    summary, detail = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=unproven-no-sender"
    assert detail[0] == "intake=lab sender=no"
    assert detail[5] == "off conf_absent=no condition=none send_off=no sent_absent=no dry=0"


@pytest.mark.parametrize("logs", ["missing-mark", "wrong-order"])
def test_report_without_the_marks_is_an_unproven_intake(report, qa, tmp_path, capsys, logs):
    document = guest_document("app_problem", APP_ID)
    requests = (requests_log(after_posts=(document,), drop=("on-end",)) if logs == "missing-mark"
                else requests_log(after_posts=(document,), marks_in_order=False))
    summary, _ = run_report(report, qa, tmp_path, full_record(),
                            logs=lab_logs(tmp_path, requests, [envelope(APP_ID)]), capsys=capsys)
    assert summary == "report=unproven-intake"


@pytest.mark.parametrize("change,failed", [
    # The off state: a guest off check failed, or a report went out too early.
    (lambda r: r["off"].update(condition="yes"), "off"),
    (lambda r: r["off"].update(conf_absent=False), "off"),
    (lambda r: r["off"].update(sent_absent=False), "off"),
    (lambda r: r["off"].update(send_off=False), "off"),
    (lambda r: r.update(timer={"triggered": False, "result": "success", "waited": 300,
                               "enabled": "enabled"}), "timer"),
    (lambda r: r.update(timer={"triggered": True, "result": "exit-code", "waited": 20,
                               "enabled": "enabled"}), "timer"),
    (lambda r: r["consent"].update(enabled=False), "consent"),
    (lambda r: r["consent"].update(endpoint=False), "consent"),
    (lambda r: r["consent"].update(on_rc=1), "consent"),
    (lambda r: r["sent"][0].update(report_id=COUNT_ID), "match"),
    (lambda r: r["sent"][0].update(status="rejected"), "match"),
])
def test_report_names_every_missing_check(report, qa, tmp_path, capsys, change, failed):
    record = full_record()
    change(record)
    summary, _ = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=missing-" + failed


def test_report_fails_when_a_report_is_posted_while_reporting_is_off(report, qa, tmp_path, capsys):
    document_app = guest_document("app_problem", APP_ID)
    document_count = guest_document("count", COUNT_ID)
    requests = requests_log(before_posts=(document_count,), after_posts=(document_app,))
    summary, detail = run_report(report, qa, tmp_path, full_record(),
                                 logs=lab_logs(tmp_path, requests, [envelope(APP_ID)]), capsys=capsys)
    assert summary == "report=missing-off"
    assert detail[2] == "posted total=2 accepted=2 app_problem=1 count=1 crash=0"


@pytest.mark.parametrize("posts,summary", [
    ((guest_document("app_problem", APP_ID),), "missing-count"),
    ((guest_document("app_problem", APP_ID), guest_document("count", COUNT_ID),
      guest_document("count", "33333333-3333-4333-8333-333333333333")), "missing-count"),
    ((guest_document("count", COUNT_ID),), "missing-app"),
    ((guest_document("count", COUNT_ID), guest_document("app_problem", APP_ID, code="other")), "missing-app"),
])
def test_report_counts_the_accepted_reports_by_kind(report, qa, tmp_path, capsys, posts, summary):
    requests = requests_log(after_posts=posts)
    envelopes = [envelope(document["report_id"]) for document in posts
                 if document["kind"] != "count"]
    record = full_record(sent=[{"status": "accepted", "http_status": 202,
                                "problem_id": "0123456789abcdef", "kind": document["kind"],
                                "report_id": document["report_id"]} for document in posts])
    got, _ = run_report(report, qa, tmp_path, record,
                        logs=lab_logs(tmp_path, requests, envelopes), capsys=capsys)
    assert got == "report=" + summary


def test_report_never_accepts_a_sent_crash(report, qa, tmp_path, capsys):
    crash = guest_document("crash", APP_ID, exe="sleep", signal="SIGSEGV")
    posts = (crash, guest_document("count", COUNT_ID))
    requests = requests_log(after_posts=posts)
    summary, detail = run_report(report, qa, tmp_path, full_record(),
                                 logs=lab_logs(tmp_path, requests, [envelope(APP_ID, headers={})]),
                                 capsys=capsys)
    assert summary == "report=missing-app+crash"
    assert detail[2].endswith("crash=1")


@pytest.mark.parametrize("body,status", [
    ("not json", 202),
    ("[]", 202),
    (json.dumps(guest_document("count", COUNT_ID)), 400),
])
def test_report_refuses_a_document_the_intake_did_not_accept(report, qa, tmp_path, capsys, body, status):
    document_app = guest_document("app_problem", APP_ID)
    requests = requests_log(after_posts=(document_app,))
    requests = [dict(entry) for entry in requests]
    for entry in requests:
        if entry["type"] == "request":
            entry["body"] = body
            entry["status"] = status
    summary, detail = run_report(report, qa, tmp_path, full_record(),
                                 logs=lab_logs(tmp_path, requests, [envelope(APP_ID)]), capsys=capsys)
    # An unaccepted body is no report: no count, no app, no envelope, no match.
    assert summary == "report=missing-count+app+schema+forward+match"
    assert "accepted=0" in detail[2]


@pytest.mark.parametrize("envelopes,summary", [
    ([], "missing-forward"),
    ([envelope(COUNT_ID)], "missing-forward"),
    ([envelope(APP_ID, bearer=False)], "missing-forward"),
    ([envelope(APP_ID), envelope(APP_ID)], "missing-forward"),
    ([envelope(APP_ID, body="{}\n")], "missing-forward"),
])
def test_report_requires_1_authenticated_envelope_per_report(report, qa, tmp_path, capsys, envelopes, summary):
    document_app = guest_document("app_problem", APP_ID)
    document_count = guest_document("count", COUNT_ID)
    requests = requests_log(after_posts=(document_app, document_count))
    got, _ = run_report(report, qa, tmp_path, full_record(),
                        logs=lab_logs(tmp_path, requests, envelopes), capsys=capsys)
    assert got == "report=" + summary


@pytest.mark.parametrize("event,headers", [
    ({"event_id": APP_ID.replace("-", ""), "user": {"ip_address": PEER}}, {}),
    ({"event_id": APP_ID.replace("-", ""), "message": PEER}, {}),
    ({"event_id": APP_ID.replace("-", "")}, {"X-Forwarded-For": PEER}),
])
def test_report_refuses_a_client_ip_in_what_reached_glitchtip(report, qa, tmp_path, capsys, event, headers):
    document_app = guest_document("app_problem", APP_ID)
    document_count = guest_document("count", COUNT_ID)
    requests = requests_log(after_posts=(document_app, document_count))
    envelopes = [envelope(APP_ID, event=event, headers=headers)]
    summary, _ = run_report(report, qa, tmp_path, full_record(),
                            logs=lab_logs(tmp_path, requests, envelopes), capsys=capsys)
    assert summary == "report=missing-ip"


def test_report_names_every_failed_check_in_a_fixed_order(report, qa, tmp_path, capsys):
    record = full_record(off=None, consent=None, timer=None, sent=[])
    requests = requests_log(drop=("on-end",), after_posts=(guest_document("crash", APP_ID,
                             exe="sleep", signal="SIGSEGV"),))
    summary, _ = run_report(report, qa, tmp_path, record,
                            logs=lab_logs(tmp_path, requests, []), capsys=capsys)
    # Without the end mark the probe never reached the intake: nothing to judge.
    assert summary == "report=unproven-intake"
    requests = requests_log(after_posts=(guest_document("crash", APP_ID, exe="sleep",
                                                        signal="SIGSEGV"),))
    summary, detail = run_report(report, qa, tmp_path, record,
                                 logs=lab_logs(tmp_path, requests, []), capsys=capsys)
    # The check names come in the report's own fixed order, never the log's.
    assert summary == "report=missing-off+timer+consent+count+app+crash+forward+match"
    assert detail[2].endswith("crash=1")


def test_report_of_a_production_send_is_accepted(report, qa, tmp_path, capsys):
    summary, detail = run_report(report, qa, tmp_path, production_record(), capsys=capsys)
    assert summary == "report=production-accepted"
    assert detail == ["intake=production sender=yes",
                      "report_id=%s kind=app_problem" % APP_ID, "http_status=202"]


@pytest.mark.parametrize("production", [
    {"http_status": 500, "report_id": APP_ID},
    {"http_status": 202, "report_id": None},
    None,
])
def test_report_of_a_production_send_that_was_not_accepted(report, qa, tmp_path, capsys, production):
    summary, detail = run_report(report, qa, tmp_path, production_record(production=production),
                                 capsys=capsys)
    assert summary == "report=missing-production"
    assert detail[2].startswith("http_status=")


def test_report_of_a_production_run_without_a_sender_is_unproven(report, qa, tmp_path, capsys):
    summary, _ = run_report(report, qa, tmp_path, production_record(sender=False, production=None),
                            capsys=capsys)
    assert summary == "report=unproven-no-sender"


def test_report_refuses_a_broken_intake_log(report, qa, tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_bytes(report.canonical(full_record()))
    requests_path = tmp_path / "requests.jsonl"
    requests_path.write_text("{not json\n")
    glitchtip_path = tmp_path / "glitchtip.jsonl"
    glitchtip_path.write_text("")
    manifest_path = normalized(tmp_path, probe_manifest(), qa)
    assert report.main(["report", str(manifest_path), str(path), str(requests_path),
                        str(glitchtip_path)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:report_logs"


# ------------------------------------------------------------ the record channel

def record_line(report, record):
    return "SNOW_REPORT_V1 " + base64.urlsafe_b64encode(
        report.canonical(record).strip()).decode().rstrip("=")


def test_phase_accepts_the_one_record_and_writes_it(report, qa, tmp_path):
    record = full_record()
    serial = tmp_path / "serial"
    serial.write_text("boot noise\n" + record_line(report, record) + "\n")
    destination = tmp_path / "report.json"
    assert report.phase(probe_manifest(), "report", NONCE, "none", str(serial),
                        str(destination)) is None
    assert json.loads(destination.read_text()) == record
    assert destination.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("change,code", [
    (lambda r: r.update(nonce="f" * 32), "record_identity"),
    (lambda r: r.update(phase="problems"), "record_identity"),
    (lambda r: production_record(), "probed_intake"),
    (lambda r: r.update(booted_digest=NEXT), "booted_image"),
    (lambda r: r.update(sender="yes"), "record_fields"),
    (lambda r: r.update(off={"conf_absent": True}), "record_off"),
    (lambda r: r["timer"].update(waited=301), "record_timer"),
    (lambda r: r["sent"][0].update(report_id="not-a-uuid"), "record_sent"),
    (lambda r: r["consent"].update(status="x" * 161), "record_consent"),
    (lambda r: r["production"] if False else r.update(production={"http_status": 202, "report_id": APP_ID}),
     "record_fields"),
])
def test_phase_rejects_a_foreign_or_malformed_record(report, qa, tmp_path, change, code):
    record = full_record()
    record = change(record) or record
    serial = tmp_path / "serial"
    serial.write_text(record_line(report, record) + "\n")
    with pytest.raises(report.GateError, match="^%s$" % code):
        report.phase(probe_manifest(), "report", NONCE, "none", str(serial),
                     str(tmp_path / "out.json"))


def test_phase_refuses_a_reused_boot_id_and_two_records(report, qa, tmp_path):
    serial = tmp_path / "serial"
    serial.write_text(record_line(report, full_record()) + "\n")
    with pytest.raises(report.GateError, match="^boot_identity$"):
        report.phase(probe_manifest(), "report", NONCE, BOOT_ID, str(serial),
                     str(tmp_path / "out.json"))
    serial.write_text(record_line(report, full_record()) + "\n"
                      + record_line(report, full_record(nonce="f" * 32)) + "\n")
    with pytest.raises(report.GateError, match="^record_count$"):
        report.phase(probe_manifest(), "report", NONCE, "none", str(serial),
                     str(tmp_path / "out.json"))
    # Identical copies are one record, never two.
    serial.write_text(record_line(report, full_record()) + "\n"
                      + record_line(report, full_record()) + "\n")
    report.phase(probe_manifest(), "report", NONCE, "none", str(serial),
                 str(tmp_path / "out.json"))


def test_phase_refuses_a_token_above_the_bound(report, qa, tmp_path):
    record = full_record(padding="x" * 13000)
    line = record_line(report, record)
    assert len(line) > report.REPORT_TOKEN_MAX + 64
    serial = tmp_path / "serial"
    serial.write_text(line + "\n")
    with pytest.raises(report.GateError, match="^record_encoding$"):
        report.phase(probe_manifest(), "report", NONCE, "none", str(serial),
                     str(tmp_path / "out.json"))


def test_phase_main_prints_only_a_bounded_code_on_error(report, qa, tmp_path, capsys):
    serial = tmp_path / "serial"
    serial.write_text("SNOW_REPORT_V1 not-a-record!\n")
    manifest_path = normalized(tmp_path, probe_manifest(), qa)
    assert report.main(["phase", str(manifest_path), "report", NONCE, "none", str(serial),
                        str(tmp_path / "out.json")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "qa_failed:record_encoding"


def test_the_guest_and_the_host_agree_on_the_record_keys(guest, report):
    assert guest.REPORT_RECORD_FIELDS == report.RECORD_FIELDS


# ------------------------------------------------------------- run.sh wiring

def plan_block():
    return re.search(r"(?ms)^sysext_mode=\$\(python3.*?^fi\n", run_script()).group()


@pytest.mark.parametrize("name,expected", [
    ("report-lab", "report"),
    ("report-production", "report"),
    ("plain", "installed-n stage boot-n-plus-1 rollback boot-n"),
    ("problems", "problems"),
])
def test_run_script_plans_the_report_boot(tmp_path, name, expected):
    work = tmp_path / name
    work.mkdir()
    documents = {"report-lab": {"product": "floe", "report_probe": {"intake": "lab"}},
                 "report-production": {"product": "floe", "report_probe": {"intake": "production"}},
                 "plain": {"product": "snow"}, "problems": {"product": "snow",
                                                            "problems_probe": {"path": "fresh"}}}
    (work / "normalized.json").write_text(json.dumps(documents[name]))
    script = ("set -Eeuo pipefail\nfail() { exit 9; }\n"
              f"WORK={work} sysext_mode=0 login_mode=0\n" + plan_block() +
              '\nprintf "%s\\n" "${phase_plan[*]}"\n')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def report_intake_block():
    return re.search(r'(?ms)^report_b64=""\n.*?^fi\n', run_script()).group()


@pytest.mark.parametrize("intake,label", [("lab", "lab"), ("production", "production")])
def test_run_script_reads_the_report_intake_from_the_manifest(tmp_path, intake, label):
    work = tmp_path / label
    work.mkdir()
    (work / "normalized.json").write_text(
        json.dumps({"product": "floe", "report_probe": {"intake": intake}}))
    script = ("set -Eeuo pipefail\nreport_mode=1\n"
              f'WORK={work} VM=snow-qa-test report_mode=1\n' +
              'fail() { exit 9; }\nblocked() { exit 8; }\n'
              'report_intake=$(python3 -c \'import json,sys; print(json.load(open(sys.argv[1]))'
              '.get("report_probe", {}).get("intake", "none"))\' "$WORK/normalized.json")\n'
              'python3() { command python3 "$@"; }\n'
              'printf "%s\\n" "$report_intake"\n')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == intake
    # The lab intake container is created and deleted only for the lab value.
    block = report_intake_block()
    assert 'if [[ "$report_intake" == lab ]]; then' in block
    assert 'incus launch images:debian/trixie "$intake_name" --ephemeral' in block
    assert 'blocked intake_unavailable' in block and 'blocked intake_setup' in block
    assert "timeout 300 incus launch" in block and "timeout 1200 incus exec" in block
    assert 'incus file push /opt/snow-qa/intake-lab.py "$INTAKE/opt/intake/lab.py"' in block
    assert "--env INTAKE_COMMIT=" in block and "--env NODE_VERSION=" in block
    assert "--env NODE_SHA256=" in block
    assert '-- bash -s < /opt/snow-qa/intake-setup.sh' in block
    assert "572fdd876d0f358f8b36a6f292a7a938b01ebc32" in block
    assert "v24.22.1" in block
    assert "6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6" in block
    # The report guest code is compressed without comments or docstrings,
    # AST-checked, and bounded.
    assert "node.body = body[1:] or [ast.Pass()]" in block
    assert "if ast.dump(ast.parse(stripped)) != ast.dump(tree):" in block
    assert "lzma.PRESET_EXTREME" in block
    assert '[[ ${#report_b64} -lt 60000 ]] || fail unit_size' in block
    assert 'fail report_payload' in block


def test_run_script_injects_the_report_credentials_only_on_the_report_boot():
    loop = re.search(r"(?ms)^for phase in \"\$\{phase_plan\[@\]\}\"; do\n.*?^done\n",
                     run_script()).group()
    assert 'if [[ "$phase" == report ]]; then' in loop
    guarded = re.search(r'(?ms)if \[\[ "\$phase" == report \]\]; then\n.*?\n      fi\n', loop).group()
    # The report guest code, the timer drop-in that fires the sender, and 1 URL.
    assert "snow-report.z=$report_b64" in guarded
    assert "systemd.unit-dropin.snosi-health-report.timer=" in guarded
    assert "RandomizedDelaySec=0" in guarded and "OnActiveSec=20" in guarded
    assert "io.systemd.credential:snow-intake=http://${INTAKE_IP}:8787" in guarded
    assert 'if [[ "$report_intake" == lab ]]; then' in guarded
    # The other boots keep exactly today's credentials.
    assert loop.count("$report_credential") == 1
    assert "$td$login_credential$report_credential\"" in loop
    assert loop.count("raw.qemu=") == 1
    assert 'report) guest_mode=report; guest_after=" multi-user.target";;' in loop


def test_run_script_waits_for_the_report_record_and_its_error():
    script = run_script()
    assert 'await SNOW_REPORT_V1 "$((budget + 300))" SNOW_REPORT_ERR' in script
    assert 'fail "report_guest_error:$code"' in script
    assert "SNOW_REPORT_ERR ([a-z_]{1,40})" in script
    assert "b'SNOW_REPORT_ERR' in line and b'SNOW_QA_KMSG1' not in line" in script
    assert '"$needle" == SNOW_REPORT_V1 ]] && vm_stopped' in script
    # The report boot uses the installed_boot budget, like a fresh probe boot.
    selection = re.search(r'(?m)^  case "\$phase" in installed-n\|login-1\|report\).*?;; esac', script, re.S)
    assert selection and "installed_boot" in selection.group()


def report_error_parser():
    script = run_script()
    block = re.search(r"observed_failure\" == SNOW_REPORT_ERR \]\]; then\n.*?"
                      r"code=\$\(python3 - \"\$WORK/serial\" <<'PY'\n(.*?)\nPY\n", script, re.S)
    assert block
    return block.group(1)


@pytest.mark.parametrize("serial,code", [
    ("SNOW_REPORT_ERR credential\n", "credential"),
    ("SNOW_REPORT_ERR credential\r\n", "credential"),
    ("boot noise\nSNOW_REPORT_ERR intake_credential\n", "intake_credential"),
    ("SNOW_REPORT_ERR credential[   12.345678] kernel text\n", None),
    ("SNOW_REPORT_ERR credential\nSNOW_REPORT_V1 abc\n", None),
    ("SNOW_REPORT_ERR credential\nSNOW_REPORT_ERR report_manifest\n", None),
    ("SNOW_REPORT_ERR not a code\n", None),
])
def test_report_error_parser_accepts_only_one_complete_code(tmp_path, serial, code):
    parser = tmp_path / "parse.py"
    parser.write_text(report_error_parser())
    path = tmp_path / "serial"
    path.write_bytes(serial.encode())
    result = subprocess.run([sys.executable, str(parser), str(path)], capture_output=True, text=True)
    assert result.returncode == (0 if code else 1)
    assert result.stdout.strip() == (code or "")


def report_block():
    """The report at the end of the run, not the lab intake setup before the boots."""
    return re.search(r'(?ms)^if \[\[ "\$report_mode" == 1 \]\]; then\n'
                     r'  # The report-probe report:.*?^fi\n', run_script()).group()


def run_report_block(tmp_path, stdout):
    block = report_block().replace("/opt/snow-qa/report.py", str(tmp_path / "report.py"))
    (tmp_path / "report.py").write_text(stdout)
    work, out, evidence = tmp_path / "work", tmp_path / "results", tmp_path / "evidence"
    for folder in (work, out, evidence):
        folder.mkdir(exist_ok=True)
    (work / "report.json").write_text("{}")
    (out / "checks.txt").write_text("")
    script = (
        'set -Eeuo pipefail\n'
        'fail() { printf "FAIL:%s\\n" "$1"; exit 9; }\n'
        'blocked() { printf "BLOCKED:%s\\n" "$1"; exit 0; }\n'
        'persist() { cp -- "$1" "$2"; }\n'
        f'WORK={work} OUT={out} EVIDENCE={evidence} report_mode=1 report_intake=production\n' + block)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result, work, out, evidence


@pytest.mark.parametrize("summary,verdict", [
    ("ok", ""),
    ("missing-off+count", ""),
    ("production-accepted", ""),
    ("unproven-no-sender", "BLOCKED:report_unproven-no-sender"),
    ("unproven-intake", "BLOCKED:report_unproven-intake"),
])
def test_run_script_reports_the_report_summary_and_blocks_only_unproven(tmp_path, summary, verdict):
    stub = 'print("report=%s")\nprint("intake=lab sender=yes")\n' % summary
    result, _, out, evidence = run_report_block(tmp_path, stub)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == verdict
    assert (out / "checks.txt").read_text().splitlines()[-1] == "report=" + summary
    assert (out / "report.txt").read_text().splitlines()[0] == "report=" + summary
    assert (evidence / "report.txt").read_text() == (out / "report.txt").read_text()


@pytest.mark.parametrize("stdout", [
    'print("report=OK")\n',
    'print("report=")\n',
    'print("something else")\n',
    'import sys\nsys.exit(1)\n',
])
def test_run_script_report_fails_closed_on_a_bad_summary(tmp_path, stdout):
    result, _, _, evidence = run_report_block(tmp_path, stdout)
    assert result.stdout.strip() == "FAIL:report_report"
    assert not (evidence / "report.txt").exists()


def test_run_script_passes_the_intake_logs_to_the_lab_report(tmp_path):
    block = report_block()
    assert 'report_logs=("$WORK/intake-requests.jsonl" "$WORK/intake-glitchtip.jsonl")' in block
    assert '${report_logs[@]+"${report_logs[@]}"}' in block
    # The 2 logs are pulled from the container, bounded, and kept as evidence.
    loop = re.search(r"(?ms)^for phase in \"\$\{phase_plan\[@\]\}\"; do\n.*?^done\n",
                     run_script()).group()
    assert 'incus file pull "$INTAKE/var/lib/intake/$intake_log.jsonl"' in loop
    assert "(( intake_size <= 1048576 )) || fail report_logs" in loop
    assert 'persist "$WORK/intake-$intake_log.jsonl" "$EVIDENCE/intake-$intake_log.jsonl"' in loop


def test_run_script_tears_the_intake_down_and_keeps_its_report_best_effort():
    script = run_script()
    finish = script.split("finish() {", 1)[1].split("\n    trap finish EXIT", 1)[0]
    # The container holds this run's bearer: it is never kept, also not with a VM.
    assert 'if [[ -n "${INTAKE:-}" ]]; then' in finish
    assert 'timeout 120 incus delete --force "$INTAKE"' in finish
    assert 'REASON="${REASON};intake_left=${intake_left}"' in finish
    assert finish.index("intake_left=") < finish.index('if [[ "$teardown_failed" == 1 ]]')
    # The report never shapes the verdict when it is missing.
    assert 'persist "$OUT/report.txt" "$EVIDENCE/report.txt" || true' in finish
    assert ': > "$OUT/report.txt" || true' in script
    assert script.count('persist "$OUT/report.txt" "$EVIDENCE/report.txt" || true') == 1
    assert 'persist "$OUT/report.txt" "$EVIDENCE/report.txt" || fail evidence_write' in script


def test_manual_template_publishes_the_report_output():
    path = SOURCE.parent / "run-snow-bootc-lifecycle.yaml"
    template = yaml.safe_load(path.read_text())
    entry = next(t for t in template["spec"]["templates"] if t["name"] == template["spec"]["entrypoint"])
    outputs = {o["name"]: o for o in entry["outputs"]["parameters"]}
    assert outputs["report"]["valueFrom"]["path"] == "/tmp/results/report.txt"
    assert outputs["report"]["valueFrom"]["default"] == "evidence unavailable"


def test_run_script_accepts_the_longest_summary(tmp_path):
    longest = "missing-off+timer+consent+count+app+crash+schema+ip+forward+match"
    result, _, out, _ = run_report_block(tmp_path, 'print("report=%s")\n' % longest)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert (out / "checks.txt").read_text().splitlines()[-1] == "report=" + longest


# ----------------------------------------------------------- the setup script

def test_intake_setup_is_valid_bash_and_pins_its_inputs(tmp_path):
    setup = scripts()["intake-setup.sh"]
    path = tmp_path / "intake-setup.sh"
    path.write_text(setup)
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0
    # The 3 inputs come from the runner, and each one is shape-checked here.
    for name in ("INTAKE_COMMIT", "NODE_VERSION", "NODE_SHA256"):
        assert '"${%s:?}"' % name in setup
    # Each input is shape-checked before it is used.
    assert '[0-9a-f]{40}' in setup and 'v[0-9]+' in setup and '[0-9a-f]{64}' in setup
    assert "sha256sum -c -" in setup
    assert '[[ "$(git -C /opt/intake/src rev-parse HEAD)" == "$INTAKE_COMMIT" ]]' in setup
    assert "npm ci --no-audit --no-fund" in setup
    # The secrets are random per run, private, and never printed.
    assert "openssl rand -hex 24" in setup and "openssl rand -hex 16" in setup
    assert "chmod 0600 /opt/intake/src/.dev.vars" in setup
    assert "chmod 0600 /opt/intake/bearer /opt/intake/dsn-key" in setup
    assert "echo" not in setup.split("chmod 0600 /opt/intake/bearer", 1)[1].split("systemd-run")[0]
    # The Worker runs with the mock's CA, and the setup waits, bounded, for health.
    assert "NODE_EXTRA_CA_CERTS=/opt/intake/tls/cert.pem" in setup
    assert "wrangler dev --local --ip 127.0.0.1 --port 8788" in setup
    assert "curl -fsS --max-time 5 http://127.0.0.1:8787/v1/health" in setup
    assert "end=$((SECONDS + 180))" in setup


def test_intake_setup_passes_shellcheck_when_it_is_installed(tmp_path):
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    path = tmp_path / "intake-setup.sh"
    path.write_text(scripts()["intake-setup.sh"])
    result = subprocess.run(["shellcheck", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------- intake-lab.py

class Upstream(BaseHTTPRequestHandler):
    """A stand-in for the Worker: it keeps the one request it saw."""

    seen = []
    delay = 0

    def log_message(self, *args):
        pass

    def answer(self):
        # A slow Worker: the proxy must have numbered the request on arrival.
        time.sleep(Upstream.delay)
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""
        Upstream.seen.append({"method": self.command, "path": self.path,
                              "headers": dict(self.headers.items()), "body": data})
        payload = b'{"ok":true}' if self.path != "/v1/report" else b'{"accepted":true}'
        self.send_response(202 if self.path == "/v1/report" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = answer
    do_POST = answer


@pytest.fixture
def intake(lab, tmp_path, monkeypatch):
    """Both servers on free ports, logging into a temporary directory."""
    state = tmp_path / "intake"
    state.mkdir()
    monkeypatch.setattr(lab, "STATE", state)
    monkeypatch.setattr(lab, "REQUESTS", state / "requests.jsonl")
    monkeypatch.setattr(lab, "GLITCHTIP", state / "glitchtip.jsonl")
    monkeypatch.setattr(lab, "CERT", tmp_path / "missing-cert.pem")
    monkeypatch.setattr(lab, "KEY", tmp_path / "missing-key.pem")
    monkeypatch.setattr(lab, "SECRETS", ["a" * 48, "b" * 32])
    lab.SEQUENCE[0] = 0
    Upstream.seen = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    proxy = lab.server(lab.Proxy, ("127.0.0.1", 0), tls=False)
    mock = lab.server(lab.Mock, ("127.0.0.1", 0))
    monkeypatch.setattr(lab, "UPSTREAM", "http://127.0.0.1:%d" % upstream.server_address[1])
    threads = [threading.Thread(target=server.serve_forever, daemon=True)
               for server in (upstream, proxy, mock)]
    for thread in threads:
        thread.start()
    yield {"lab": lab, "state": state, "proxy": "http://127.0.0.1:%d" % proxy.server_address[1],
           "mock": "http://127.0.0.1:%d" % mock.server_address[1]}
    for server in (upstream, proxy, mock):
        server.shutdown()
        server.server_close()


def request(url, data=b"", headers=None, method="POST"):
    """(status, body) of 1 request; an error status is not an exception here."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_the_proxy_adds_the_edge_headers_and_keeps_the_rest_off(intake):
    lab = intake["lab"]
    status, body = request(intake["proxy"] + "/v1/report", data=b'{"kind":"count"}',
                           headers={"Content-Type": "application/json",
                                    "Authorization": "Bearer secret",
                                    "CF-Connecting-IP": "1.2.3.4",
                                    "User-Agent": "snosi-health/1"})
    assert status == 202 and body == b'{"accepted":true}'
    seen = Upstream.seen[-1]
    assert seen["method"] == "POST" and seen["path"] == "/v1/report"
    assert seen["body"] == b'{"kind":"count"}'
    # The content type and the 3 headers an edge adds reach the Worker; nothing
    # the guest sent about itself does, and urllib adds only its own framing.
    # Header names are case-insensitive, so this compares them that way.
    received = {name.lower(): value for name, value in seen["headers"].items()}
    for name in ("cf-connecting-ip", "x-forwarded-for", "true-client-ip"):
        assert received[name] == "127.0.0.1", name
    assert received["content-type"] == "application/json"
    assert "authorization" not in received
    assert received.get("user-agent") != "snosi-health/1"
    entry = lines(intake["state"] / "requests.jsonl")[-1]
    assert entry["type"] == "request" and entry["method"] == "POST"
    assert entry["path"] == "/v1/report" and entry["status"] == 202
    assert entry["peer"] == "127.0.0.1" and entry["name"] is None
    assert json.loads(entry["body"]) == {"kind": "count"}


def test_the_proxy_forwards_health_and_logs_it(intake):
    status, body = request(intake["proxy"] + "/v1/health", method="GET")
    assert status == 200 and json.loads(body) == {"ok": True}
    entry = lines(intake["state"] / "requests.jsonl")[-1]
    assert entry["type"] == "request" and entry["method"] == "GET"
    assert entry["status"] == 200 and entry["body"] == ""


def test_the_proxy_logs_the_marks_and_refuses_anything_else(intake):
    for name in ("off-begin", "on-begin", "on-end"):
        status, body = request(intake["proxy"] + "/lab/mark/" + name)
        assert status == 204 and body == b""
    assert request(intake["proxy"] + "/lab/mark/Bad_Name")[0] == 404
    assert request(intake["proxy"] + "/lab/mark/" + "x" * 17)[0] == 404
    assert request(intake["proxy"] + "/v1/other")[0] == 404
    assert request(intake["proxy"] + "/v1/report/extra")[0] == 404
    entries = lines(intake["state"] / "requests.jsonl")
    assert [entry["name"] for entry in entries[:3]] == ["off-begin", "on-begin", "on-end"]
    assert all(entry["type"] == "mark" and entry["status"] == 204 for entry in entries[:3])
    # 1 sequence number for both logs, in the order the requests arrived.
    assert [entry["seq"] for entry in entries] == sorted(entry["seq"] for entry in entries)
    assert entries[0]["seq"] == 1


def test_the_mock_keeps_the_envelope_and_replaces_the_bearer(intake):
    body = (json.dumps({"event_id": "a" * 32}) + "\n" + json.dumps({"type": "event"}) + "\n"
            + json.dumps({"event_id": "a" * 32, "user": {"ip_address": None}}) + "\n")
    status, answer = request(intake["mock"] + "/api/1/envelope/", data=body.encode(),
                             headers={"Authorization": "Bearer " + "a" * 48,
                                      "Content-Type": "application/x-sentry-envelope"})
    assert status == 200 and json.loads(answer) == {"id": "a" * 32}
    entry = lines(intake["state"] / "glitchtip.jsonl")[-1]
    assert entry["bearer_ok"] is True and entry["path"] == "/api/1/envelope/"
    assert entry["headers"]["Authorization"] == "bearer_ok: true"
    assert json.loads(entry["body"].splitlines()[0]) == {"event_id": "a" * 32}


@pytest.mark.parametrize("credential,ok", [
    ("Bearer " + "a" * 48, True),
    # The DSN key in X-Sentry-Auth-like text is not the bearer: only the bearer counts.
    ("Sentry sentry_key=" + "a" * 48 + ", sentry_version=7", False),
    ("Bearer " + "a" * 48 + "x", False),
    ("Bearer wrong", False),
    ("", False),
])
def test_the_mock_answers_200_only_for_this_runs_credential(intake, credential, ok):
    headers = {"Authorization": credential} if credential else {}
    status, _ = request(intake["mock"] + "/api/1/envelope/", data=b"{}\n", headers=headers)
    assert status == (200 if ok else 404)
    assert lines(intake["state"] / "glitchtip.jsonl")[-1]["bearer_ok"] is ok


def test_the_mock_refuses_any_other_path(intake):
    assert request(intake["mock"] + "/api/1/store/", data=b"{}\n")[0] == 404
    assert request(intake["mock"] + "/", data=b"{}\n")[0] == 404
    assert lines(intake["state"] / "glitchtip.jsonl") == []


def test_the_mock_answers_x_for_an_envelope_without_an_event_id(intake):
    status, answer = request(intake["mock"] + "/api/1/envelope/", data=b"not json\n",
                             headers={"Authorization": "Bearer " + "a" * 48})
    assert status == 200 and json.loads(answer) == {"id": "x"}


def test_the_proxy_reports_an_unreachable_worker_as_502(intake, monkeypatch):
    lab = intake["lab"]
    monkeypatch.setattr(lab, "UPSTREAM", "http://127.0.0.1:1")
    status, _ = request(intake["proxy"] + "/v1/report", data=b"{}")
    assert status == 502
    assert lines(intake["state"] / "requests.jsonl")[-1]["status"] == 502


def test_the_mock_logs_a_cut_read_as_text(intake):
    # The log keeps text, never bytes: an envelope above the bound is cut, not dropped.
    big = b"x" * (intake["lab"].ENVELOPE_MAX + 100)
    status, _ = request(intake["mock"] + "/api/1/envelope/", data=big,
                        headers={"Authorization": "Bearer " + "a" * 48})
    assert status == 200
    entry = lines(intake["state"] / "glitchtip.jsonl")[-1]
    assert len(entry["body"]) == intake["lab"].ENVELOPE_MAX


def test_qa_dispatches_the_report_program_from_its_credential(qa, monkeypatch, tmp_path, capsys):
    """qa.py runs report-guest.py from the snow-report.z credential, AST-stripped."""
    import ast
    import lzma
    source = yaml.safe_load(SOURCE.read_text())["data"]["report-guest.py"]
    credential = tmp_path / "snow-report.z"
    credential.write_bytes(lzma.compress(ast.unparse(ast.parse(source)).encode(),
                                        preset=9 | lzma.PRESET_EXTREME))
    monkeypatch.setattr(qa, "REPORT_CODE", str(credential))
    published = []
    monkeypatch.setattr(qa, "publish", published.append)
    # A missing credential directory is an I/O error: no marker, only the bounded code.
    assert qa.main(["report", str(tmp_path / "missing-credentials")]) == 1
    assert published == [] and capsys.readouterr().err.strip() == "qa_failed:io_error"
    # The credential of another probe is a gate error: the guest publishes its code.
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "phase").write_text("problems\n")
    (credentials / "nonce").write_text(NONCE + "\n")
    assert qa.main(["report", str(credentials)]) == 1
    assert published == ["SNOW_REPORT_ERR credential"]


def test_the_mock_never_logs_the_dsn_key_and_ignores_it_as_a_bearer(intake):
    # The Worker sends its DSN key in X-Sentry-Auth on every envelope; that key
    # must not count as the bearer, and it must not reach the evidence log.
    status, _ = request(intake["mock"] + "/api/1/envelope/", data=b"{}\n",
                        headers={"X-Sentry-Auth": "Sentry sentry_version=7, sentry_key=" + "b" * 32})
    assert status == 404
    entry = lines(intake["state"] / "glitchtip.jsonl")[-1]
    assert entry["bearer_ok"] is False
    assert "b" * 32 not in json.dumps(entry)


def test_the_node_tarball_name_keeps_the_v_of_the_version():
    # dev-snow-bootc-lifecycle-fnn87: nodejs.org names the tarball
    # node-v24.22.1-linux-x64.tar.xz; a stripped "v" gives a 404.
    setup = scripts()["intake-setup.sh"]
    assert "/node-${NODE_VERSION}-linux-x64.tar.xz" in setup
    assert "NODE_VERSION#v" not in setup


def test_a_failed_intake_setup_keeps_its_last_lines_as_evidence():
    run = scripts()["run.sh"]
    assert '> "$WORK/intake-setup.log" 2>&1 || {' in run
    assert 'persist "$WORK/intake-setup.txt" "$EVIDENCE/intake-setup.txt" || true' in run
    assert "set -x" not in scripts()["intake-setup.sh"]


def test_report_ignores_the_loopback_readiness_check_and_the_mock_host(report, qa, tmp_path, capsys):
    # dev-snow-bootc-lifecycle-5pcgp: the setup's own GET /v1/health comes from
    # 127.0.0.1, and the mock's Host header is 127.0.0.1:8443. Neither is a client
    # IP, so a clean run must not end missing-ip.
    document_app = guest_document("app_problem", APP_ID)
    document_count = guest_document("count", COUNT_ID)
    requests = [{"type": "request", "method": "GET", "path": "/v1/health", "peer": "127.0.0.1",
                 "status": 200, "name": None, "body": "", "seq": 0}]
    requests += requests_log(after_posts=(document_app, document_count))
    headers = {"Content-Type": "application/x-sentry-envelope", "Authorization": "bearer_ok: true",
               "Host": "127.0.0.1:8443"}
    summary, _ = run_report(report, qa, tmp_path, full_record(),
                            logs=lab_logs(tmp_path, requests, [envelope(APP_ID, headers=headers)]),
                            capsys=capsys)
    assert summary == "report=ok"


def test_report_without_a_guest_peer_is_not_ip_clean(report):
    # Only loopback peers: nothing proves the guest's address stayed out.
    assert report.ip_clean([{"peer": "127.0.0.1"}], []) is False


# ------------------------------------------------------- the desktop sender
# Snow, Snowfield and Sundog have no snosi sender: chairlift sends, from the
# test-only user's own session (chairlift docs/specs/problem-reporting.md v0.15.0).

SHIPPED = """# ChairLift configuration. Administrator-owned files override the defaults.
general:
  window_width: 1280
problems_page:
  problems_system_group:
    enabled: true
  problems_apps_group:
    enabled: true
    notes: keep
  problems_reporting_group:
    enabled: true
    # endpoint: https://report.frostyard.org/v1/report
help_page:
  help_resources_group:
    enabled: true
    website: https://frostyard.org
"""

SESSION = {"type": "wayland", "class": "user", "state": "active", "desktop": "KDE"}


def sent_log_entries():
    """What chairlift records after 1 app problem and 1 count: successes only."""
    return [{"report_id": APP_ID, "kind": "app_problem", "problem_id": "0123456789abcdef",
             "week": WEEK, "sent_at": "2026-11-05T04:10:00Z"},
            {"report_id": COUNT_ID, "kind": "count", "problem_id": "",
             "week": WEEK, "sent_at": "2026-11-05T04:10:01Z"}]


def preview_text(documents):
    """A dry run's stdout: indented JSON documents, one after the other."""
    return "".join(json.dumps(document, indent=2) + "\n" for document in documents)


class Desktop:
    """The installed desktop, its user and its files, below a temporary tree."""

    def __init__(self, module, home, admin, shipped, cli, calls):
        self.module, self.home, self.admin, self.shipped = module, home, admin, shipped
        self.cli, self.calls = cli, calls

    def consent(self):
        return Path(self.home) / ".config/chairlift/reporting.json"

    def reports(self):
        return Path(self.home) / ".local/state/chairlift/reports.json"


def desktop_environment(qa, monkeypatch, module, tmp_path, *, intake="lab", product="snow",
                        version="0.15.0", session=SESSION, documents=(),
                        status=202, layout=SHIPPED, admin=None, account=True,
                        dry_rc=0, dry_sent=False, crash=True, sent_lines=None,
                        sender_output=b"sent 2 reports\n"):
    """1 installed desktop: chairlift, its config, the user's home and its commands."""
    m = probe_manifest(intake, product)
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    for name, value in {"phase": "report", "nonce": NONCE, "image_n": m["image_n"],
                        "image_n_plus_1": m["image_n_plus_1"],
                        "target_ref": m["target_ref"]}.items():
        (credentials / name).write_text(value + "\n")
    boot = tmp_path / "boot_id"
    boot.write_text(BOOT_ID + "\n")
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized(tmp_path, m, qa))
    monkeypatch.setattr(qa, "GUEST_PATHS", {**qa.GUEST_PATHS, "boot_id": boot})
    cli = tmp_path / "chairlift"
    cli.write_text("#!/bin/bash\n")
    monkeypatch.setattr(module, "CLI", str(cli))
    shipped = tmp_path / "share" / "config.yml"
    shipped.parent.mkdir()
    shipped.write_text(layout)
    monkeypatch.setattr(module, "CLI_SHIPPED", str(shipped))
    admin_path = tmp_path / "etc" / "chairlift" / "config.yml"
    if admin is not None:
        admin_path.parent.mkdir(parents=True)
        admin_path.write_text(admin)
    monkeypatch.setattr(module, "CLI_ADMIN", str(admin_path))
    monkeypatch.setattr(module, "BODY", str(tmp_path / "snow-report.json"))
    problems = tmp_path / "problems.json"
    problems.write_bytes(collector_document(crash=crash))
    monkeypatch.setattr(module, "PROBLEMS", str(problems))
    credential = tmp_path / "snow-intake"
    credential.write_text(INTAKE_URL + "\n")
    monkeypatch.setattr(module, "INTAKE_CREDENTIAL", str(credential))
    home = tmp_path / "home"
    home.mkdir()
    entry = pwd.struct_passwd(("snowqa", "x", os.getuid(), os.getgid(), "", str(home), "/bin/bash"))
    monkeypatch.setattr(qa, "login_account", lambda: entry if account else None)
    monkeypatch.setattr(qa, "login_display_manager", lambda: "sddm")
    monkeypatch.setattr(qa, "login_session", lambda wait: session)
    monkeypatch.setattr(qa, "problems_app", lambda: True)
    monkeypatch.setattr(qa, "problems_crash", lambda: True)
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: None)
    calls, posts = [], []
    entries = sent_log_entries() if sent_lines is None else list(sent_lines)

    def consent_text():
        try:
            return (Path(home) / ".config/chairlift/reporting.json").read_text()
        except OSError:
            return None

    def run(args, timeout=120):
        calls.append(args)
        if args[0] == "dpkg-query":
            # Only the real package name answers (run zf6pf asked for "chairlift").
            if args[-1] != "frostyard-chairlift":
                return 1, b""
            return (0, (version + "\n").encode()) if version else (1, b"")
        if args[0] == "systemd-run":
            consent = consent_text()
            allowed = consent is not None and '"enabled":true' in consent
            if "--dry-run" in args:
                if dry_sent:
                    # Evidence that this run sent something: chairlift's log entry.
                    state = home / ".local/state/chairlift"
                    state.mkdir(parents=True, exist_ok=True)
                    (state / "reports.json").write_text(
                        json.dumps({"format": 1, "sent": sent_log_entries()}))
                return (dry_rc, preview_text(documents).encode()) if allowed else (dry_rc, b"")
            if not allowed:
                return 0, b"problem reporting is off\n"
            state = home / ".local/state/chairlift"
            state.mkdir(parents=True, exist_ok=True)
            (state / "reports.json").write_text(
                json.dumps({"format": 1, "sent": entries, "count_week": WEEK}))
            return 0, sender_output
        if args[0] == "curl":
            return 0, str(status).encode()
        return 0, b""

    monkeypatch.setattr(qa, "problems_run", run)

    def fake_post(url, timeout=30):
        posts.append(url)
        return 204, b""

    monkeypatch.setattr(module, "post", fake_post)
    published = []
    monkeypatch.setattr(qa, "publish", published.append)

    def fake_status(args, **kwargs):
        document = {"spec": {"image": {"image": m["target_ref"], "transport": "registry"}},
                    "status": {"booted": {"image": {"imageDigest": "sha256:" + N,
                                                   "version": "20260923000000"}},
                               "staged": None, "rollback": None}}
        return subprocess.CompletedProcess(args, 0, json.dumps(document).encode(), b"")

    monkeypatch.setattr(qa.subprocess, "run", fake_status)
    desktop = Desktop(module, home, admin_path, shipped, cli, calls)
    return m, credentials, desktop, posts, published


def test_guest_desktop_report_records_the_chairlift_path(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path,
        documents=(guest_document("app_problem", APP_ID), guest_document("count", COUNT_ID)))
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert set(record) == guest_module.REPORT_RECORD_FIELDS
    assert record["sender"] is True and record["sender_version"] == "0.15.0"
    assert record["session"] == SESSION and record["intake"] == "lab"
    assert record["injected"] == {"app": True, "crash": True}
    # A desktop has no snosi sender state: the 3 triples are null.
    assert record["off"] is None and record["consent"] is None and record["timer"] is None
    assert record["cli"] == {"off_rc": [0, 0], "off_consent": True, "sent_absent": True,
                             "config": True, "on_rc": 0, "output": ["sent 2 reports"]}
    # chairlift logs only successes and no HTTP status: 0 means "not recorded".
    assert [(entry["kind"], entry["report_id"], entry["status"], entry["http_status"])
            for entry in record["sent"]] == [("app_problem", APP_ID, "accepted", 0),
                                             ("count", COUNT_ID, "accepted", 0)]
    assert record["production"] is None
    assert posts == [INTAKE_URL + "/lab/mark/off-begin", INTAKE_URL + "/lab/mark/on-begin",
                     INTAKE_URL + "/lab/mark/on-end"]
    # The sender ran in the user's own session, as the user, 4 times: 2 off, 1 on.
    senders = [args for args in desktop.calls if args[0] == "systemd-run"]
    assert len(senders) == 3
    assert senders[-1] == ["systemd-run", "--machine=snowqa@.host", "--user", "--wait", "--pipe",
                           "--quiet", "--collect", "--", "/bin/sh", "-c",
                           guest_module.BOTH_STREAMS, str(desktop.cli)]
    # 2 collector runs after the injection, like on Floe.
    assert desktop.calls.count(["systemctl", "start", "snosi-health.service"]) == 2


def test_guest_desktop_report_writes_the_admin_endpoint(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, desktop, posts, published = desktop_environment(qa, monkeypatch, guest_module, tmp_path)
    assert guest_module.main(credentials) == 0
    written = desktop.admin.read_text()
    lines = written.splitlines()
    # The line goes directly after the group line, indented 2 more, and the
    # administrator's own commented example stays where it was.
    group = lines.index("  problems_reporting_group:")
    assert lines[group + 1] == "    endpoint: " + ENDPOINT
    assert "    # endpoint: https://report.frostyard.org/v1/report" in lines
    assert "  problems_apps_group:" in lines and "    notes: keep" in lines
    stripped = [line.strip() for line in lines]
    assert "help_page:" in stripped and "website: https://frostyard.org" in stripped
    assert desktop.admin.stat().st_mode & 0o777 == 0o644
    assert desktop.admin.parent.stat().st_mode & 0o777 == 0o755


def test_the_admin_config_is_authoritative_as_a_whole(qa, monkeypatch, guest_module, tmp_path):
    admin = ("problems_page:\n"
             "  problems_reporting_group:\n"
             "    enabled: true\n"
             "    endpoint: https://wrong.example/v1/report\n"
             "    notes: admin\n"
             "other_page:\n"
             "  keep: yes\n")
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, admin=admin)
    assert guest_module.main(credentials) == 0
    written = desktop.admin.read_text()
    # The /etc file is the whole source: no merge with /usr/share, and its own
    # endpoint is replaced, not doubled.
    assert written == ("problems_page:\n"
                       "  problems_reporting_group:\n"
                       "    endpoint: " + ENDPOINT + "\n"
                       "    enabled: true\n"
                       "    notes: admin\n"
                       "other_page:\n"
                       "  keep: yes\n")
    assert "general:" not in written


@pytest.mark.parametrize("layout", [
    # No problems_page at all.
    "general:\n  window_width: 1280\n",
    # A group outside problems_page.
    "problems_page:\n  other_group:\n    enabled: true\nproblems_reporting_group:\n  enabled: true\n",
    # 2 groups under problems_page: never guess which one is the sender's.
    ("problems_page:\n  problems_reporting_group:\n    enabled: true\n"
     "  problems_reporting_group:\n    enabled: false\n"),
])
def test_guest_desktop_refuses_a_foreign_config_layout(qa, monkeypatch, guest_module, tmp_path, layout):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, layout=layout)
    assert guest_module.main(credentials) == 1
    assert published == ["SNOW_REPORT_ERR chairlift_config"]
    assert not desktop.admin.exists()


def test_guest_desktop_report_writes_the_consent_for_the_user(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, desktop, posts, published = desktop_environment(qa, monkeypatch, guest_module, tmp_path)
    assert guest_module.main(credentials) == 0
    body = json.loads(desktop.consent().read_text())
    assert body == {"format": 1, "enabled": True, "mode": "auto",
                    "decided_at": body["decided_at"], "schema": "frostyard.problem/1"}
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", body["decided_at"])
    folder = desktop.consent().parent
    assert folder.stat().st_mode & 0o777 == 0o700
    assert desktop.consent().stat().st_mode & 0o777 == 0o600
    assert folder.stat().st_uid == os.getuid()
    assert desktop.consent().stat().st_uid == os.getuid()
    assert desktop.consent().stat().st_gid == os.getgid()
    # No temporary file is left behind next to it.
    assert [entry.name for entry in folder.iterdir()] == ["reporting.json"]


def test_guest_desktop_report_without_a_session_is_unproven(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, session=None)
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert record["sender"] is True and record["session"] is None
    assert record["cli"] is None and record["sent"] is None and record["injected"] is None
    # Nothing was sent and no mark was made: the host reports unproven-session.
    assert posts == [] and not any(args[0] == "systemd-run" for args in desktop.calls)


def test_guest_desktop_record_bounds_a_localized_desktop_name(qa, monkeypatch, guest_module, tmp_path):
    session = {"type": "wayland", "class": "user", "state": "active",
               "desktop": "KDE\x1b[0m Plasmä " + "x" * 80}
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, session=session)
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    # Only printable ASCII, at most 64 characters: the host bound is never a surprise.
    desktop = record["session"]["desktop"]
    assert desktop.startswith("KDE[0m Plasm") and desktop.count("x") >= 51
    assert all(32 <= ord(ch) <= 126 for ch in desktop)
    assert len(desktop) == 64
    assert set(record["session"]) == {"type", "class", "state", "desktop"}


def test_guest_desktop_report_without_an_account_is_unproven(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, account=False)
    assert guest_module.main(credentials) == 0
    assert payload(published[0])["session"] is None


@pytest.mark.parametrize("version,sender", [
    ("0.15.0", True), ("0.15.1", True), ("0.16", True), ("1.0.0", True),
    ("0.14.9", False), ("0.0.1", False), ("", False), ("broken", False),
])
def test_a_desktop_sender_needs_chairlift_0_15_0(qa, monkeypatch, guest_module, tmp_path, version, sender):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, version=version)
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert record["sender"] is sender
    assert record["sender_version"] == (version or None)
    if not sender:
        assert record["session"] is None and record["cli"] is None
        assert not any(args[0] == "systemd-run" for args in desktop.calls)


def test_guest_desktop_production_posts_the_one_injected_preview(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, intake="production",
        documents=(guest_document("app_problem", APP_ID), guest_document("count", COUNT_ID)))
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert record["intake"] == "production" and record["session"] == SESSION
    assert record["production"] == {"http_status": 202, "report_id": APP_ID}
    assert record["cli"]["on_rc"] == 0 and record["cli"]["sent_absent"] is True
    assert record["cli"]["config"] is False and record["cli"]["off_rc"] == []
    # No mark, and the sent log is never written: the dry run changes nothing.
    assert posts == [] and not desktop.reports().exists()
    # The consent exists for the dry run, and the probe removes it again.
    assert not desktop.consent().exists()
    # Only the dry run reaches the sender, and only its 1 document is posted.
    senders = [args for args in desktop.calls if args[0] == "systemd-run"]
    assert len(senders) == 1 and senders[0][-1] == "--dry-run"
    assert guest_module.STDOUT_ONLY in senders[0]
    curl = next(args for args in desktop.calls if args[0] == "curl")
    assert curl[curl.index("--data-binary") + 1] == "@" + guest_module.BODY
    assert json.loads(Path(guest_module.BODY).read_text())["report_id"] == APP_ID
    # The admin endpoint is not touched on the production path.
    assert not desktop.admin.exists()


@pytest.mark.parametrize("documents", [
    (),
    ({"schema": "frostyard.problem/1", "kind": "app_problem", "detail": {"code": "other"}},),
    (guest_document("app_problem", APP_ID), guest_document("app_problem", APP_ID)),
])
def test_guest_desktop_production_refuses_an_unexpected_preview(qa, monkeypatch, guest_module, tmp_path, documents):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, intake="production", documents=documents)
    assert guest_module.main(credentials) == 1
    assert published == ["SNOW_REPORT_ERR production_document"]
    assert not any(args[0] == "curl" for args in desktop.calls)
    assert not desktop.consent().exists()


def test_the_sent_log_of_chairlift_is_parsed_and_bounded(guest_module, tmp_path):
    home = tmp_path / "home"
    state = home / ".local/state/chairlift"
    state.mkdir(parents=True)
    entries = [{"report_id": APP_ID, "kind": "app_problem", "problem_id": "0123456789abcdef"},
               {"report_id": COUNT_ID, "kind": "count", "problem_id": ""},
               {"report_id": "not-a-uuid", "kind": "Bad Kind!", "problem_id": "zz"},
               {"report_id": COUNT_ID, "kind": "crash"}]
    entries += [{"report_id": APP_ID, "kind": "app_problem"} for _ in range(40)]
    (state / "reports.json").write_text(json.dumps({"format": 1, "sent": entries}))
    kept = guest_module.cli_sent(home)
    assert len(kept) == 32
    assert kept[0] == {"status": "accepted", "http_status": 0, "problem_id": "0123456789abcdef",
                       "kind": "app_problem", "report_id": APP_ID}
    # A count has no problem_id, and a foreign value is never guessed at.
    assert kept[1]["problem_id"] is None and kept[1]["kind"] == "count"
    assert kept[2] == {"status": "accepted", "http_status": 0, "problem_id": None,
                       "kind": "other", "report_id": None}
    assert kept[3]["problem_id"] is None and kept[3]["kind"] == "crash"


@pytest.mark.parametrize("content", [b"not json", b"[]", b'{"sent": "one"}', b"{}"])
def test_a_broken_sent_log_is_no_send(guest_module, tmp_path, content):
    home = tmp_path / "home"
    state = home / ".local/state/chairlift"
    state.mkdir(parents=True)
    (state / "reports.json").write_bytes(content)
    assert guest_module.cli_sent(home) == []


def test_previews_reads_the_indented_documents(guest_module):
    documents = (guest_document("app_problem", APP_ID), guest_document("count", COUNT_ID))
    parsed = guest_module.previews(preview_text(documents).encode())
    assert parsed == list(documents)
    # 1 unparsable preview after a good one never becomes a document.
    assert guest_module.previews((json.dumps(documents[0], indent=2) + "\nnot json\n").encode()) == [documents[0]]


def test_the_endpoint_edit_leaves_a_nested_endpoint_alone(guest_module, tmp_path, monkeypatch):
    layout = ("problems_page:\n"
              "  problems_reporting_group:\n"
              "    enabled: true\n"
              "  problems_system_group:\n"
              "    nested:\n"
              "      endpoint: keep-me\n"
              "help_page:\n"
              "  keep: yes\n")
    shipped = tmp_path / "share.yml"
    shipped.write_text(layout)
    admin = tmp_path / "etc/config.yml"
    monkeypatch.setattr(guest_module, "CLI_ADMIN", str(admin))
    monkeypatch.setattr(guest_module, "CLI_SHIPPED", str(shipped))
    assert guest_module.write_endpoint("http://10.0.0.5:8787/v1/report") is True
    written = admin.read_text().splitlines()
    # Only an endpoint line of the reporting group is replaced: a deeper key of
    # another group keeps its own value.
    assert written[2] == "    endpoint: http://10.0.0.5:8787/v1/report"
    assert "      endpoint: keep-me" in written


# ------------------------------------------------- report.py on a desktop

def desktop_record(**overrides):
    record = full_record(sender_version="0.15.0", session=dict(SESSION), crash_collected=True,
                         off=None, consent=None, timer=None, dry=None,
                         cli={"off_rc": [0, 0], "off_consent": True, "sent_absent": True,
                              "config": True, "on_rc": 0, "output": ["sent 2 reports"]},
                         sent=[{"status": "accepted", "http_status": 0,
                                "problem_id": "0123456789abcdef", "kind": "app_problem",
                                "report_id": APP_ID},
                               {"status": "accepted", "http_status": 0, "problem_id": None,
                                "kind": "count", "report_id": COUNT_ID}])
    record.update(overrides)
    return record


def test_report_of_a_desktop_pass_is_ok(report, qa, tmp_path, capsys):
    summary, detail = run_report(report, qa, tmp_path, desktop_record(), capsys=capsys)
    assert summary == "report=ok"
    assert detail == [
        "intake=lab sender=yes",
        "session type=wayland desktop=KDE",
        "sender version=0.15.0",
        "marks off-begin=yes on-begin=yes on-end=yes",
        "posted total=2 accepted=2 app_problem=1 count=1 crash=0",
        "report_ids app_problem=%s count=%s" % (APP_ID, COUNT_ID),
        "envelopes=1 bearer_ok=yes",
        "cli off_rc=0,0 off_consent=yes sent_absent=yes config=yes on_rc=0",
        "sent accepted=2 of 2",
    ]


def test_report_of_a_desktop_without_a_session_is_unproven(report, qa, tmp_path, capsys):
    record = desktop_record(session=None, cli=None, sent=None, injected=None)
    summary, detail = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=unproven-session"
    assert detail[1] == "session type=none desktop=none"
    assert detail[7] == "cli off_rc=none off_consent=no sent_absent=no config=no on_rc=none"


@pytest.mark.parametrize("change,failed", [
    (lambda r: r["cli"].update(off_rc=[0, 1]), "off"),
    (lambda r: r["cli"].update(off_rc=[1, 0]), "off"),
    (lambda r: r["cli"].update(off_consent=False), "off"),
    (lambda r: r["cli"].update(sent_absent=False), "off"),
    (lambda r: r["cli"].update(on_rc=1), "cli"),
    (lambda r: r["cli"].update(config=False), "cli"),
    (lambda r: r["sent"][0].update(report_id=COUNT_ID), "match"),
])
def test_report_names_every_failed_desktop_check(report, qa, tmp_path, capsys, change, failed):
    record = desktop_record()
    change(record)
    summary, _ = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=missing-" + failed


def test_report_of_a_desktop_that_sent_before_the_consent_is_a_finding(report, qa, tmp_path, capsys):
    requests = requests_log(before_posts=(guest_document("count", COUNT_ID),),
                            after_posts=(guest_document("app_problem", APP_ID),))
    summary, _ = run_report(report, qa, tmp_path, desktop_record(),
                            logs=lab_logs(tmp_path, requests, [envelope(APP_ID)]), capsys=capsys)
    assert summary == "report=missing-off"


def test_report_of_every_failed_desktop_check_fits_the_bound(report, qa, tmp_path, capsys):
    record = desktop_record(cli={"off_rc": [1, 1], "off_consent": False, "sent_absent": False,
                                 "config": False, "on_rc": 1, "output": []},
                            sent=[])
    requests = requests_log(after_posts=(guest_document("crash", APP_ID, exe="sleep",
                                                        signal="SIGSEGV"),))
    summary, _ = run_report(report, qa, tmp_path, record,
                            logs=lab_logs(tmp_path, requests, []), capsys=capsys)
    assert summary == "report=missing-off+cli+count+app+crash+forward+match"
    assert len(summary) <= 96


def test_report_of_a_desktop_production_send_is_accepted(report, qa, tmp_path, capsys):
    record = desktop_record(intake="production", sent=None, dry=None,
                            production={"http_status": 202, "report_id": APP_ID},
                            cli={"off_rc": [], "off_consent": False, "sent_absent": True,
                                 "config": False, "on_rc": 0, "output": ["a preview"]})
    summary, detail = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=production-accepted"
    assert detail == ["intake=production sender=yes", "session type=wayland desktop=KDE",
                      "sender version=0.15.0", "report_id=%s kind=app_problem" % APP_ID,
                      "http_status=202"]


def test_report_of_a_desktop_production_run_without_a_session_is_unproven(report, qa, tmp_path, capsys):
    record = desktop_record(intake="production", sent=None, dry=None, session=None, cli=None,
                            production=None)
    summary, _ = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=unproven-session"


@pytest.mark.parametrize("change,code", [
    (lambda r: r.update(sender_version="0.15.0 "), "record_sender"),
    (lambda r: r.update(session={"type": "wayland"}), "record_session"),
    (lambda r: r["session"].update(type="wa\nyl"), "record_session"),
    (lambda r: r.update(cli={"off_rc": [0, 0, 0], "off_consent": True, "sent_absent": True,
                             "config": True, "on_rc": 0, "output": []}), "record_cli"),
    (lambda r: r["cli"].update(on_rc=True), "record_cli"),
    (lambda r: r["cli"].update(output=["x"] * 21), "record_cli"),
    (lambda r: r["cli"].update(output=["\x01"]), "record_cli"),
    # A chairlift record never carries the snosi triples, and a Floe record
    # never carries a version, a session or a cli.
    (lambda r: r.update(off=full_record()["off"]), "record_fields"),
    (lambda r: full_record(session=dict(SESSION)), "record_fields"),
    (lambda r: full_record(cli={"off_rc": [], "off_consent": False, "sent_absent": True,
                                "config": True, "on_rc": 0, "output": []}), "record_fields"),
])
def test_phase_rejects_a_malformed_desktop_record(report, qa, tmp_path, change, code):
    record = desktop_record()
    record = change(record) or record
    serial = tmp_path / "serial"
    serial.write_text(record_line(report, record) + "\n")
    with pytest.raises(report.GateError, match="^%s$" % code):
        report.phase(probe_manifest(), "report", NONCE, "none", str(serial),
                     str(tmp_path / "out.json"))


def test_phase_accepts_a_desktop_record_for_every_desktop_product(report, qa, tmp_path):
    for product in ("snow", "snowfield", "sundog"):
        record = desktop_record()
        serial = tmp_path / "serial"
        serial.write_text(record_line(report, record) + "\n")
        assert report.phase(probe_manifest("lab", product), "report", NONCE, "none", str(serial),
                            str(tmp_path / "out.json")) is None


# --------------------------------------------------- run.sh on a desktop boot

def report_plan_block():
    return re.search(r"(?ms)^  phase_plan=\(report\)\n.*?report_login=1\n", run_script()).group()


@pytest.mark.parametrize("product,login", [("floe", "0"), ("snow", "1"),
                                           ("snowfield", "1"), ("sundog", "1")])
def test_run_script_plans_the_login_setup_only_on_a_desktop_report_boot(tmp_path, product, login):
    work = tmp_path / product
    work.mkdir()
    (work / "normalized.json").write_text(json.dumps({"product": product}))
    script = ("set -Eeuo pipefail\nfail() { exit 9; }\n" f"WORK={work}\n" + report_plan_block() +
              '\nprintf "%s\\n" "$report_login"\n')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == login


def login_setup_block():
    return re.search(r'(?ms)^  login_setup=0\n.*?\n  fi\n', run_script()).group()


@pytest.mark.parametrize("phase,report_login,setup", [
    ("login-1", "0", "1"), ("login-2", "0", "0"), ("report", "1", "1"),
    ("report", "0", "0"), ("installed-n", "1", "0"),
])
def test_run_script_reuses_the_login_setup_for_a_desktop_report_boot(tmp_path, phase, report_login, setup):
    script = ("set -Eeuo pipefail\n" f"phase={phase} report_login={report_login}\n" +
              login_setup_block() + '\nprintf "%s\\n" "$login_setup"\n')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == setup
    # The unit and the drop-in are the first-login probe's own code, never a copy.
    run = run_script()
    assert run.count("login_b64=") == 1
    assert run.count("systemd.extra-unit.snow-login-setup.service=$lb") == 1
    assert 'gd=$(printf \'[Unit]\\nWants=snow-guest.service snow-login-setup.service\'' in run
    guarded = re.search(r'(?ms)if \[\[ "\$login_setup" == 1 \]\]; then\n.*?\n  else\n', run).group()
    assert "snow-login-setup.service" in guarded and "login_b64=" in guarded


@pytest.mark.parametrize("out,expected", [
    (b"problem reporting is off\n", True),
    (b"main.go:52: main: process start\nproblem reporting is off\n", True),
    (b"problem reporting is off: the report format changed; turn reporting on in ChairLift to agree again\n", False),
    (b"Ask each time: nothing is sent without a window\n", False),
    (b"", False),
])
def test_guest_desktop_off_needs_chairlifts_exact_off_line(guest_module, out, expected):
    # chairlift v0.15.0 prints "problem reporting is off" on stderr for a missing or
    # off consent; the word "consent" is in none of its lines (cli.go).
    assert guest_module.says_off(out) is expected


def test_guest_desktop_sender_keeps_stderr_except_for_the_dry_run(guest_module):
    # The reason line and the log are on stderr; the previews alone are on stdout.
    assert guest_module.BOTH_STREAMS.endswith("2>&1")
    assert guest_module.STDOUT_ONLY.endswith("2>/dev/null")
    assert '"$0" --send-pending-reports "$@"' in guest_module.BOTH_STREAMS


# ------------------------------------------------------- the SMBIOS bounds
# The SMBIOS table is bounded at 64 KiB IN TOTAL (dev-snow-bootc-lifecycle-6swrx:
# a desktop report boot carried about 68500 characters and the firmware dropped
# every credential, so the guest unit never ran). Each value is bounded on its
# own, and this section bounds their sum.

def qa_payload_block():
    return re.search(r"(?ms)^python3 -c 'import ast,lzma.*?^\s*qa_b64=\$\(base64 -w0 \"\$WORK/qa\.z\"\) "
                     r"\|\| fail qa_payload\n", run_script()).group()


def report_payload_block():
    return re.search(r"(?ms)^  python3 -c 'import ast,lzma.*?^\s*\[\[ \$\{#report_b64\} -lt 60000 \]\] "
                     r"\|\| fail unit_size\n", run_script()).group()


def credential_block():
    """The boot's guest unit, its drop-ins and its credentials, to the 1 raw.qemu."""
    return re.search(r'(?ms)^  guestunit="\[Unit\].*?raw\.qemu="\$smbios" >/dev/null 2>&1 '
                     r'\|\| fail phase_injection\n', run_script()).group()


def raw_qemu(tmp_path, phase, product="snow", intake="lab", report_login=0, guest_mode="guest",
             guest_after=" multi-user.target"):
    """(length, smbios) of the real raw.qemu string run.sh builds for 1 boot kind."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    data = scripts()
    for name in ("qa.py", "report-guest.py", "login-setup.sh"):
        (scripts_dir / name).write_text(data[name])
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(probe_manifest(intake, product), sort_keys=True))
    normalized_path = tmp_path / "normalized.json"
    validated = subprocess.run([sys.executable, str(scripts_dir / "qa.py"), "validate",
                                str(source), str(normalized_path)], capture_output=True, text=True)
    assert validated.returncode == 0, validated.stderr
    spec = json.loads(normalized_path.read_text())
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    harness = (
        "set -Eeuo pipefail\n"
        'fail() { printf "FAIL:%s\\n" "$1"; exit 9; }\n'
        # The 2 host commands of this fragment: a recording incus and its timeout.
        'incus() { printf "%s" "$4" > "$WORK/qemu"; }\n'
        'timeout() { shift; "$@"; }\n'
        f"WORK={work} VM=snow-qa-test\n"
        f"phase={phase} report_login={report_login} report_intake={intake} INTAKE_IP=10.0.0.5\n"
        f"guest_mode={guest_mode} guest_after=\"{guest_after}\"\n"
        f"nonce={NONCE} budget=900 n={spec['image_n']} next={spec['image_n_plus_1']}\n"
        f"target={spec['target_ref']}\n"
        f"manifest_b64={base64.b64encode(normalized_path.read_bytes()).decode()}\n"
        f"report_mode={1 if phase == 'report' else 0}\n"
        + qa_payload_block().replace("/opt/snow-qa/", str(scripts_dir) + "/")
        + (report_payload_block().replace("/opt/snow-qa/", str(scripts_dir) + "/")
           if phase == "report" else 'report_b64=""\n')
        + credential_block().replace("/opt/snow-qa/", str(scripts_dir) + "/")
        + '\nprintf "%s\\n" "${#smbios}"\n'
    )
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert result.returncode == 0, (result.stdout, result.stderr)
    smbios = (work / "qemu").read_text().removeprefix("raw.qemu=")
    return int(result.stdout.strip()), smbios


BOOTS = [
    # phase, product, intake, report_login, guest_mode, guest_after
    ("installed-n", "snow", "lab", 0, "guest", ""),
    ("stage", "snow", "lab", 0, "guest", ""),
    ("problems", "snow", "lab", 0, "problems", " multi-user.target"),
    ("login-1", "snow", "lab", 0, "login", " multi-user.target"),
    ("report", "floe", "lab", 0, "report", " multi-user.target"),
    ("report", "floe", "production", 0, "report", " multi-user.target"),
    ("report", "snow", "lab", 1, "report", " multi-user.target"),
    ("report", "snow", "production", 1, "report", " multi-user.target"),
]


@pytest.mark.parametrize("phase,product,intake,report_login,guest_mode,guest_after", BOOTS)
def test_each_boot_stays_well_under_the_smbios_table_total(
        tmp_path, phase, product, intake, report_login, guest_mode, guest_after):
    # 58000 is the margin under the lane's 61440 bound: before the compressed
    # login-setup credential and the docstring-free payloads, the desktop report
    # boot came to about 68500 characters (run 6swrx) and lost its guest unit.
    length, smbios = raw_qemu(tmp_path, phase, product, intake, report_login, guest_mode, guest_after)
    assert length == len(smbios)
    assert length <= 58000, length
    # Every value stays under the SMBIOS credential bound on its own as well.
    for value in re.findall(r"-[a-z]+ type=11,value=[^ ]+", smbios):
        assert len(value) < 60000


def test_the_old_layout_would_have_failed_the_total(tmp_path):
    # The measured defect (run 6swrx, desktop report boot, lab): guest unit 49444
    # + report.z 8700 + the login unit with its base64 script 9788, and the small
    # values, came to about 68500 characters, over the 61440 bound.
    length, smbios = raw_qemu(tmp_path, "report", "snow", "lab", 1, "report", " multi-user.target")
    assert length <= 58000
    # The same boot without the 2 savings would be about 11500 characters longer.
    assert "snow-login.z=" in smbios
    assert not re.search(r"echo \$login_b64", run_script())
    assert 49444 + 8700 + 9788 > 61440


def test_run_script_bounds_the_total_of_every_smbios_value():
    script = run_script()
    # 1 bound on the sum, before the only raw.qemu of the boot.
    assert 'smbios="-smbios type=11,value=io.systemd.credential.binary:systemd.extra-unit.snow-guest.service=$gb' in script
    assert "[[ ${#smbios} -le 61440 ]] || fail smbios_size" in script
    assert script.index("fail smbios_size") < script.index('raw.qemu="$smbios"')
    # The comment names the reason: the table is bounded in total, not per value.
    bound = script.index("[[ ${#smbios} -le 61440 ]]")
    comment = script[bound - 1200:bound]
    assert "64 KiB in total (run 6swrx" in comment
    assert "The other SMBIOS structures need room" in comment


def test_the_guest_payloads_carry_no_docstrings():
    # Both payloads drop every docstring: it is the same program, and the boot's
    # SMBIOS total is what the table bounds.
    script = run_script()
    assert script.count("isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))") == 2
    assert script.count("node.body = body[1:] or [ast.Pass()]") == 2
    assert script.count("if ast.dump(ast.parse(stripped)) != ast.dump(tree):") == 2
    for name in ("qa.py", "report-guest.py"):
        assert "__doc__" not in scripts()[name]


def test_the_intake_creates_both_logs_before_any_request(tmp_path, monkeypatch, scripts_dir):
    # dev-snow-bootc-lifecycle-zf6pf: nothing was forwarded, glitchtip.jsonl never
    # existed, and the pull failed the run as report_logs.
    spec = importlib.util.spec_from_file_location("intake_lab_touch", scripts_dir / "intake-lab.py")
    lab = importlib.util.module_from_spec(spec)
    (scripts_dir / "intake-lab.py").write_text(scripts()["intake-lab.py"])
    spec.loader.exec_module(lab)
    state = tmp_path / "intake"
    monkeypatch.setattr(lab, "STATE", state)
    monkeypatch.setattr(lab, "REQUESTS", state / "requests.jsonl")
    monkeypatch.setattr(lab, "GLITCHTIP", state / "glitchtip.jsonl")
    monkeypatch.setattr(lab, "MOCK_PORT", 0)
    monkeypatch.setattr(lab, "PROXY_PORT", 0)

    class Stop(Exception):
        pass

    def stop(self, *args, **kwargs):
        raise Stop()

    monkeypatch.setattr(lab.ThreadingHTTPServer, "serve_forever", stop)
    with pytest.raises(Stop):
        lab.serve()
    assert (state / "requests.jsonl").read_text() == ""
    assert (state / "glitchtip.jsonl").read_text() == ""


# ------------------------------------------- cross-review fixes (lab#191, T857 t21)
# Every test here fails without its fix: the judge used to accept vacuous evidence.

def test_a_desktop_dry_run_that_wrote_a_sent_entry_posts_nothing(qa, monkeypatch, guest_module, tmp_path):
    # Point 1: a sent log entry is evidence that the dry run sent more than 1
    # report, so the production path must post nothing and the host says so.
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, intake="production", dry_sent=True,
        documents=(guest_document("app_problem", APP_ID), guest_document("count", COUNT_ID)))
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert record["production"] == {"http_status": 0, "report_id": None}
    assert record["cli"]["sent_absent"] is False and record["cli"]["on_rc"] == 0
    assert not any(args[0] == "curl" for args in desktop.calls)


def test_a_desktop_production_record_with_a_sent_entry_is_missing_production(report, qa, tmp_path, capsys):
    record = desktop_record(intake="production", sent=None, dry=None,
                            production={"http_status": 202, "report_id": APP_ID},
                            cli={"off_rc": [], "off_consent": False, "sent_absent": False,
                                 "config": False, "on_rc": 0, "output": []})
    summary, _ = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=missing-production"


def test_a_desktop_dry_run_that_failed_posts_nothing(qa, monkeypatch, guest_module, tmp_path):
    # Point 1: a dry run that exited non-zero proves nothing about the report.
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, intake="production", dry_rc=1,
        documents=(guest_document("app_problem", APP_ID),))
    assert guest_module.main(credentials) == 0
    record = payload(published[0])
    assert record["production"] == {"http_status": 0, "report_id": None}
    assert record["cli"]["on_rc"] == 1
    assert not any(args[0] == "curl" for args in desktop.calls)


def test_a_desktop_production_record_with_a_failed_dry_run_is_missing_production(report, qa, tmp_path, capsys):
    record = desktop_record(intake="production", sent=None, dry=None,
                            production={"http_status": 202, "report_id": APP_ID},
                            cli={"off_rc": [], "off_consent": False, "sent_absent": True,
                                 "config": False, "on_rc": 1, "output": []})
    summary, _ = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=missing-production"


def test_the_guest_records_the_crash_the_collector_kept(qa, monkeypatch, guest_module, tmp_path):
    # Point 2: the crash check needs a crash to exclude; the record says whether
    # the collector wrote 1.
    m, credentials, desktop, posts, published = desktop_environment(qa, monkeypatch, guest_module, tmp_path)
    assert guest_module.main(credentials) == 0
    assert payload(published[0])["crash_collected"] is True


def test_the_guest_records_a_missing_crash(qa, monkeypatch, guest_module, tmp_path):
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, crash=False)
    assert guest_module.main(credentials) == 0
    assert payload(published[0])["crash_collected"] is False


def test_the_floe_path_records_the_crash_the_collector_kept(qa, monkeypatch, guest_module, tmp_path):
    for name, expected in (("with", True), ("without", False)):
        folder = tmp_path / name
        folder.mkdir()
        m, credentials, calls, posts, published = guest_environment(
            qa, monkeypatch, guest_module, folder, crash=expected)
        assert guest_module.main(credentials) == 0
        assert payload(published[0])["crash_collected"] is expected


@pytest.mark.parametrize("change", [
    lambda r: r["injected"].update(crash=False),
    lambda r: r.update(crash_collected=False),
    lambda r: r.update(crash_collected=None),
])
def test_the_crash_check_fails_without_an_injected_and_collected_crash(report, qa, tmp_path, capsys, change):
    record = full_record()
    change(record)
    summary, _ = run_report(report, qa, tmp_path, record, capsys=capsys)
    assert summary == "report=missing-crash"


def malformed_envelopes():
    """Each point-3 case: a body that is not 1 whole Sentry event envelope."""
    import itertools

    def body(*lines):
        return "\n".join(lines) + "\n"

    header = json.dumps({"event_id": APP_ID.replace("-", "")})
    item = json.dumps({"type": "event", "length": 1})
    event = json.dumps({"event_id": APP_ID.replace("-", ""), "message": "a report"})
    return {
        "header-only": body(header),
        "missing-payload": body(header, item),
        "malformed-item-header": body(header, "{not json", event),
        "item-not-event": body(header, json.dumps({"type": "attachment"}), event),
        "conflicting-ids": body(header, item, json.dumps({"event_id": "f" * 32})),
        "2-items": body(header, item, event, item, event),
    }


@pytest.mark.parametrize("name", sorted(malformed_envelopes()))
def test_forwarding_needs_a_whole_envelope(report, qa, tmp_path, capsys, name):
    # Point 3: a header alone, a missing payload, a foreign item header or a
    # second item is no envelope, so the forward check must fail.
    entries = [envelope(APP_ID, body=malformed_envelopes()[name])]
    requests = requests_log(after_posts=(guest_document("app_problem", APP_ID),
                                         guest_document("count", COUNT_ID)))
    summary, _ = run_report(report, qa, tmp_path, full_record(),
                            logs=lab_logs(tmp_path, requests, entries), capsys=capsys)
    assert "forward" in summary.split("missing-", 1)[1].split("+")
    assert report.envelope_parts(entries[0]) == (None, None)


def test_a_whole_envelope_is_still_one_event(report, qa, tmp_path, capsys):
    entry = envelope(APP_ID)
    event_id, event = report.envelope_parts(entry)
    assert event_id == APP_ID.replace("-", "")
    assert event["event_id"] == event_id


def test_no_lab_secret_is_written_in_the_envelope_log(intake):
    # Point 4: the DSN key and the bearer reach the mock in the body and the
    # Authorization header; neither may appear anywhere in the log line.
    lab = intake["lab"]
    bearer = lab.SECRETS[0]
    body = (json.dumps({"event_id": "a" * 32}) + "\n" + json.dumps({"type": "event"}) + "\n"
            + json.dumps({"event_id": "a" * 32, "dsn": "https://" + "b" * 32 + "@127.0.0.1:8443/1",
                          "note": bearer}) + "\n")
    status, _ = request(intake["mock"] + "/api/1/envelope/", data=body.encode(),
                        headers={"Authorization": "Bearer " + bearer})
    assert status == 200
    line = (intake["state"] / "glitchtip.jsonl").read_text().splitlines()[-1]
    assert bearer not in line and "b" * 32 not in line
    assert line.count("<redacted>") >= 2
    assert lab.scrub("/api/" + bearer + "/envelope/") == "/api/<redacted>/envelope/"


def test_the_proxy_numbers_a_request_when_it_arrives(intake):
    # Point 5: a slow Worker must not move an off-state request behind a later mark.
    import time
    lab = intake["lab"]
    Upstream.delay = 1.5
    try:
        worker = threading.Thread(target=request,
                                  args=(intake["proxy"] + "/v1/report", b'{"kind":"count"}'))
        worker.start()
        time.sleep(0.3)   # the POST arrived, its forward is still running
        assert request(intake["proxy"] + "/lab/mark/on-begin")[0] == 204
        worker.join(10)
    finally:
        Upstream.delay = 0
    entries = lines(intake["state"] / "requests.jsonl")
    posted_entry = next(entry for entry in entries if entry["type"] == "request")
    mark = next(entry for entry in entries if entry["name"] == "on-begin")
    assert posted_entry["seq"] < mark["seq"]
    # The log line order is the completion order: the mark is written first.
    assert entries.index(mark) < entries.index(posted_entry)


def test_report_counts_a_post_that_finished_after_the_mark_as_off(report, qa, tmp_path, capsys):
    # Point 5, on the host: the judge orders by the arrival number, never by the
    # line order of the log.
    document_app = guest_document("app_problem", APP_ID)
    document_count = guest_document("count", COUNT_ID)
    entries = [{"type": "mark", "name": "off-begin", "seq": 1, "peer": PEER, "status": 204,
                "method": "POST", "path": "/lab/mark/off-begin", "body": ""},
               {"type": "mark", "name": "on-begin", "seq": 3, "peer": PEER, "status": 204,
                "method": "POST", "path": "/lab/mark/on-begin", "body": ""},
               {"type": "request", "method": "POST", "path": "/v1/report", "peer": PEER,
                "status": 202, "name": None, "seq": 2, "body": json.dumps(document_app)},
               {"type": "request", "method": "POST", "path": "/v1/report", "peer": PEER,
                "status": 202, "name": None, "seq": 4, "body": json.dumps(document_count)},
               {"type": "mark", "name": "on-end", "seq": 5, "peer": PEER, "status": 204,
                "method": "POST", "path": "/lab/mark/on-end", "body": ""}]
    summary, detail = run_report(report, qa, tmp_path, full_record(),
                                 logs=lab_logs(tmp_path, entries, [envelope(APP_ID)]), capsys=capsys)
    assert summary == "report=missing-off"
    # posted() is in arrival order, not file order.
    assert [entry["seq"] for entry in report.posted(entries)] == [2, 4]


def chairlift_entries(count=32):
    """Chairlift's own log entries: only successes, no HTTP status."""
    return [{"report_id": "%08x-1111-4111-8111-%012x" % (number, number),
             "kind": "app_problem", "problem_id": "%016x" % number, "week": WEEK,
             "sent_at": "2026-11-05T04:10:00Z"} for number in range(count)]


def heavy_output(count=20):
    """The worst output the record can carry: 160 characters full of " and \\."""
    return ("\n".join(['"' * 80 + "\\" * 80] * count) + "\n").encode()


def test_the_worst_case_record_is_trimmed_to_fit_and_keeps_every_send(qa, monkeypatch, guest_module, tmp_path):
    # Point 6: the record is 1 marker line with its own bound. The diagnostics go
    # first; a sent entry never goes.
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, sent_lines=chairlift_entries(),
        sender_output=heavy_output())
    assert guest_module.main(credentials) == 0
    line = published[0]
    assert line.startswith("SNOW_REPORT_V1 ")
    assert len(line.split(" ", 1)[1]) <= qa.REPORT_TOKEN_MAX
    record = payload(line)
    assert len(record["sent"]) == 32
    assert len(record["cli"]["output"]) < guest_module.OUTPUT_MAX


def test_a_record_that_cannot_fit_keeps_the_record_size_error(qa, monkeypatch, guest_module, tmp_path):
    # Point 6: trimming the diagnostics is not a licence to send a huge record.
    m, credentials, desktop, posts, published = desktop_environment(
        qa, monkeypatch, guest_module, tmp_path, sent_lines=chairlift_entries(),
        sender_output=heavy_output())
    monkeypatch.setattr(qa, "REPORT_TOKEN_MAX", 1000)
    assert guest_module.main(credentials) == 1
    assert published == ["SNOW_REPORT_ERR record_size"]


def intake_launch_block():
    return re.search(r'(?ms)^    intake_name="\$\{VM\}-intake"\n.*?^    fi\n',
                     run_script()).group()


def run_intake_launch(tmp_path, exists):
    """The real launch fragment with a stub incus: (state, reason, INTAKE)."""
    harness = ("set -Eeuo pipefail\n"
               'incus() { case "$1" in launch) return 1;; delete) return 1;; info) return "$EXISTS";; '
               'esac; }\n'
               'timeout() { shift; "$@"; }\n'
               'blocked() { printf "BLOCKED:%s intake=%s\\n" "$1" "${INTAKE:-none}"; exit 0; }\n'
               f"VM=snow-qa-test EXISTS={exists}\n"
               'INTAKE=""\n'
               + intake_launch_block() +
               '\nprintf "launched intake=%s\\n" "$INTAKE"\n')
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, (result.stdout, result.stderr)
    return result.stdout.strip(), result.stderr.strip()


def test_a_failed_intake_launch_reports_no_teardown_failure(tmp_path):
    # Point 7: nothing was created, so there is nothing to delete and no reason
    # to say the teardown failed.
    stdout, _ = run_intake_launch(tmp_path, exists=1)
    assert stdout == "BLOCKED:intake_unavailable intake=none"


def test_an_intake_that_still_exists_after_a_failed_launch_stays_a_teardown_task(tmp_path):
    # Point 7: a timed-out launch can leave the instance behind; then the name must
    # survive, so finish() can delete it or report it.
    stdout, _ = run_intake_launch(tmp_path, exists=0)
    assert stdout == "BLOCKED:intake_unavailable intake=snow-qa-test-intake"
