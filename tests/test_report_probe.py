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
import re
import shutil
import subprocess
import sys
import threading
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


@pytest.mark.parametrize("product", ["snow", "snowfield", "sundog"])
def test_report_probe_is_floe_only_in_this_commit(qa, product):
    with pytest.raises(qa.GateError, match="^report_product$"):
        qa.normalize(probe_manifest("lab", product))


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


def guest_environment(qa, monkeypatch, module, tmp_path, *, intake="lab", sender=True,
                      condition="no", off_message=True, on=True, enabled=True, endpoint=True,
                      triggered=True, result="success", sent_lines=(), documents=(),
                      status=202, intake_credential=INTAKE_URL):
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
                  intake="lab", injected={"app": True, "crash": True},
                  off={"conf_absent": True, "service_rc": 1, "condition": "no", "send_rc": 1,
                       "send_off": True, "status_rc": 0, "endpoint": True, "again_rc": 1,
                       "again_result": "success", "sent_absent": True, "dry_rc": 0},
                  consent={"on_rc": 0, "enabled": True, "endpoint": True, "status": "reporting: on"},
                  timer={"triggered": True, "result": "success", "waited": 20, "enabled": "enabled"},
                  sent=[{"status": "accepted", "http_status": 202, "problem_id": "0123456789abcdef",
                         "kind": "app_problem", "report_id": APP_ID},
                        {"status": "accepted", "http_status": 202, "problem_id": "0123456789abcdee",
                         "kind": "count", "report_id": COUNT_ID}],
                  dry=["app_problem", "count"], production=None)
    record.update(overrides)
    return record


def production_record(**overrides):
    record = full_record(sender=True, intake="production", injected=None, off=None, consent=None,
                         timer=None, sent=None, dry=None,
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
    record = full_record(sender=False, injected=None, off=None, consent=None, timer=None,
                         sent=None, dry=None)
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
    assert 'incus launch images:debian/trixie "$INTAKE" --ephemeral' in block
    assert 'blocked intake_unavailable' in block and 'blocked intake_setup' in block
    assert "timeout 300 incus launch" in block and "timeout 1200 incus exec" in block
    assert 'incus file push /opt/snow-qa/intake-lab.py "$INTAKE/opt/intake/lab.py"' in block
    assert "--env INTAKE_COMMIT=" in block and "--env NODE_VERSION=" in block
    assert "--env NODE_SHA256=" in block
    assert '-- bash -s < /opt/snow-qa/intake-setup.sh' in block
    assert "572fdd876d0f358f8b36a6f292a7a938b01ebc32" in block
    assert "v24.22.1" in block
    assert "6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6" in block
    # The report guest code is compressed without comments, AST-checked, and bounded.
    assert "ast.unparse(ast.parse(source))" in block and "lzma.PRESET_EXTREME" in block
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

    def log_message(self, *args):
        pass

    def answer(self):
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
