"""Offline contract tests for the first-login probe mode of the lifecycle lane.

The probe is report-only: after a fresh install a test-only autologin
configuration in the installed /etc logs a user in, and the probe records which
first-login app started. It never fails the run because of which app started.
"""
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

import test_snow_bootc_lifecycle as snow
from test_snow_bootc_lifecycle import (  # noqa: F401  (qa is the ConfigMap fixture)
    N, NEXT, NONCE, REPO, SOURCE, manifest, qa, run_script)

BOOT_ID = "01234567-89ab-4cde-8f01-23456789abcd"
SECOND_BOOT_ID = "11234567-89ab-4cde-8f01-23456789abcd"
LOGIN_PLAN = {"session_wait": 120, "app_wait": 60}
DETAIL = ("phase=login-1 dm=gdm autologin=yes session=wayland started=first-setup "
          "entries=org.frostyard.FirstSetup.autostart.desktop mark_done=first-setup:0 "
          "markers=first-setup waited=35")


def scripts():
    return yaml.safe_load(SOURCE.read_text())["data"]


def login_manifest(product="snow"):
    """The Snow manifest with the first_login_probe key, for any product."""
    m = manifest()
    repo = f"ghcr.io/frostyard/{product}"
    m.update(family=product, product=product, image_n=f"{repo}@sha256:{N}",
             image_n_plus_1=f"{repo}@sha256:{NEXT}",
             version_tag_n=f"{repo}:{m['version_n']}",
             version_tag_n_plus_1=f"{repo}:{m['version_n_plus_1']}",
             target_ref=f"{repo}:qa-controlled")
    m["first_login_probe"] = dict(LOGIN_PLAN)
    return m


def login_record(phase="login-1", **overrides):
    record = dict(phase=phase, nonce=NONCE, boot_id=BOOT_ID, booted_digest=N,
                  display_manager="gdm", autologin=True,
                  session={"type": "wayland", "class": "user", "state": "active", "desktop": "plasmax11"},
                  entries=[], apps={"first-setup": {"entry": False, "binary": False},
                                    "chairlift": {"entry": False, "binary": False}},
                  started=[], mark_done=[],
                  markers={"first-setup": False, "chairlift": False}, waited=35)
    record.update(overrides)
    return record


def login_line(qa, record):
    import base64
    return "SNOW_LOGIN_V1 " + base64.urlsafe_b64encode(
        qa.canonical(record).strip()).decode().rstrip("=")


def login_serial(qa, *records, extra=""):
    return "\n".join(["boot message unrelated"] + [login_line(qa, record) for record in records]) + "\n" + extra


def login_files(tmp_path, qa, m=None, product="snow"):
    normalized = tmp_path / "normalized.json"
    normalized.write_bytes(qa.canonical(m or login_manifest(product)))
    return normalized


# ---------------------------------------------------------------- manifest

@pytest.mark.parametrize("product", ["snow", "floe", "snowfield", "sundog"])
def test_first_login_probe_normalizes_for_every_product(qa, product):
    m = login_manifest(product)
    assert qa.normalize(m) is m
    assert qa.normalize(m)["first_login_probe"] == LOGIN_PLAN
    assert "first_login_probe" not in qa.FIELDS


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=1),
    lambda p: p.pop("session_wait"),
    lambda p: p.pop("app_wait"),
    lambda p: p.update(session_wait=59),
    lambda p: p.update(session_wait=901),
    lambda p: p.update(session_wait=True),
    lambda p: p.update(session_wait="120"),
    lambda p: p.update(app_wait=29),
    lambda p: p.update(app_wait=601),
    lambda p: p.update(app_wait=False),
    lambda p: p.update(session_wait=900, app_wait=600),
])
def test_first_login_probe_rejects_unsafe_input(qa, change):
    m = login_manifest()
    change(m["first_login_probe"])
    with pytest.raises(qa.GateError, match=r"^[a-z_]{1,40}$"):
        qa.normalize(m)


def test_first_login_waits_must_fit_both_phase_budgets(qa):
    # The two waits plus 180 s must fit the smaller of the two login budgets.
    for key in ("installed_boot", "reboot"):
        m = login_manifest()
        m["timeouts"][key] = 359
        with pytest.raises(qa.GateError, match="^first_login_window$"):
            qa.normalize(m)
        m["timeouts"][key] = 360
        assert qa.normalize(m) == m


def test_first_login_probe_and_sysext_probe_never_travel_together(qa):
    m = login_manifest()
    m["sysext_probe"] = {"features": ["notesd"], "units": ["frostmail.service"], "first_start": []}
    with pytest.raises(qa.GateError, match="^manifest_fields$"):
        qa.normalize(m)
    # The field set is the only accepted one: no other extra key passes either.
    m = login_manifest()
    m["other_probe"] = {}
    with pytest.raises(qa.GateError, match="^manifest_fields$"):
        qa.normalize(m)


def test_first_login_probe_uses_the_distinct_version_rule(qa):
    m = login_manifest()
    # The probe never stages N+1, so N only has to differ from N+1.
    m["version_n"], m["version_n_plus_1"] = "20260924000000", "20260923000000"
    m["version_tag_n"] = f"{REPO}:20260924000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260923000000"
    assert qa.normalize(m)["version_n"] == "20260924000000"
    m["version_n_plus_1"] = "20260924000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260924000000"
    with pytest.raises(qa.GateError, match="^version_distinct$"):
        qa.normalize(m)


def test_manifest_without_the_key_keeps_the_five_boot_rules(qa):
    # A manifest without first_login_probe must give exactly today's run.
    m = manifest()
    m["version_n_plus_1"] = "20260923000000"
    m["version_tag_n_plus_1"] = f"{REPO}:20260923000000"
    with pytest.raises(qa.GateError, match="^version_order$"):
        qa.normalize(m)


# ------------------------------------------------------------ login-setup.sh

def setup_script(tmp_path):
    path = tmp_path / "login-setup.sh"
    path.write_text(scripts()["login-setup.sh"])
    return path


def stub_tools(tmp_path):
    """The three host tools login-setup.sh calls, each recording that it ran."""
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "systemctl").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$STUB_DM"\n')
    (tools / "id").write_text('#!/bin/sh\nexit 1\n')
    (tools / "useradd").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$STUB_USERADD"\n')
    for tool in ("systemctl", "id", "useradd"):
        (tools / tool).chmod(0o755)
    return tools


def run_setup(tmp_path, root, tools, dm="gdm.service"):
    env = {**os.environ, "SNOW_QA_ROOT": str(root), "STUB_DM": dm,
           "STUB_USERADD": str(tmp_path / "useradd.args"),
           "PATH": f"{tools}:/usr/bin:/bin"}
    return subprocess.run(["bash", str(setup_script(tmp_path))], env=env,
                          capture_output=True, text=True, timeout=30)


def state_file(root):
    return (root / "var/lib/snow-qa/login-setup").read_text()


def created_paths(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def test_login_setup_writes_the_gdm_autologin_after_the_daemon_section(tmp_path):
    root = tmp_path / "root"
    (root / "etc/gdm3").mkdir(parents=True)
    (root / "etc/gdm3/daemon.conf").write_text(
        "# a comment\n[daemon]\nAutomaticLogin=someone\nTimedLoginEnable=true\nWaylandEnable=true\n")
    result = run_setup(tmp_path, root, stub_tools(tmp_path))
    assert result.returncode == 0, result.stderr
    lines = (root / "etc/gdm3/daemon.conf").read_text().splitlines()
    # The image's own active autologin lines are gone; the comment is kept.
    assert not any(line.strip() in ("AutomaticLogin=someone", "TimedLoginEnable=true") for line in lines)
    assert "# a comment" in lines
    index = lines.index("[daemon]")
    assert lines[index + 1:index + 4] == [
        "# snow-qa: test-only autologin for the lab first-login probe, never in an image",
        "AutomaticLoginEnable=true", "AutomaticLogin=snowqa"]
    assert lines[index + 4] == "WaylandEnable=true"
    assert state_file(root) == "dm=gdm\nuser=snowqa\nsession=none\n"
    assert (tmp_path / "useradd.args").read_text().split() == [
        "--create-home", "--user-group", "--shell", "/bin/bash", "--comment", "Snow", "QA", "snowqa"]


def test_login_setup_replaces_every_active_autologin_line_in_any_case(tmp_path):
    root = tmp_path / "root"
    (root / "etc/gdm3").mkdir(parents=True)
    (root / "etc/gdm3/daemon.conf").write_text(
        "[daemon]\nautomaticloginenable=false\nautomaticlogin=root\n")
    assert run_setup(tmp_path, root, stub_tools(tmp_path)).returncode == 0
    body = (root / "etc/gdm3/daemon.conf").read_text()
    assert "automaticlogin=root" not in body and "automaticloginenable=false" not in body
    assert body.count("AutomaticLogin=") == 1 and "AutomaticLogin=snowqa" in body


def test_login_setup_creates_the_gdm_config_with_a_daemon_section(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert run_setup(tmp_path, root, stub_tools(tmp_path), dm="gdm3.service").returncode == 0
    body = (root / "etc/gdm3/daemon.conf").read_text()
    assert body.startswith("[daemon]\n# snow-qa:") and "AutomaticLogin=snowqa" in body
    assert state_file(root).startswith("dm=gdm\n")


def test_login_setup_appends_a_missing_daemon_section(tmp_path):
    root = tmp_path / "root"
    (root / "etc/gdm3").mkdir(parents=True)
    (root / "etc/gdm3/daemon.conf").write_text("[security]\nWaylandEnable=true\n")
    assert run_setup(tmp_path, root, stub_tools(tmp_path)).returncode == 0
    lines = (root / "etc/gdm3/daemon.conf").read_text().splitlines()
    assert lines[0] == "[security]"
    index = lines.index("[daemon]")
    assert "AutomaticLogin=snowqa" in lines[index + 1:index + 4]


def test_login_setup_writes_an_sddm_autologin_with_the_plasma_session(tmp_path):
    root = tmp_path / "root"
    (root / "usr/share/wayland-sessions").mkdir(parents=True)
    (root / "usr/share/wayland-sessions/plasma.desktop").write_text("[Desktop Entry]\n")
    before = set(created_paths(root))
    assert run_setup(tmp_path, root, stub_tools(tmp_path), dm="sddm.service").returncode == 0
    body = (root / "etc/sddm.conf.d/zz-snow-qa-autologin.conf").read_text().splitlines()
    assert body[0].startswith("# snow-qa:")
    assert body[1] == "[Autologin]" and body[2] == "User=snowqa" and body[3] == "Session=plasma.desktop"
    assert state_file(root) == "dm=sddm\nuser=snowqa\nsession=plasma.desktop\n"
    # Nothing is written below /usr: the image's session files are read only.
    assert set(created_paths(root)) - before == {
        "etc", "etc/sddm.conf.d", "etc/sddm.conf.d/zz-snow-qa-autologin.conf",
        "var", "var/lib", "var/lib/snow-qa", "var/lib/snow-qa/login-setup"} - before


def test_login_setup_writes_an_sddm_autologin_without_a_session(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert run_setup(tmp_path, root, stub_tools(tmp_path), dm="sddm.service").returncode == 0
    body = (root / "etc/sddm.conf.d/zz-snow-qa-autologin.conf").read_text()
    assert "Session=" not in body and body.rstrip().endswith("User=snowqa")
    assert state_file(root) == "dm=sddm\nuser=snowqa\nsession=none\n"


@pytest.mark.parametrize("dm", ["", "lightdm.service", "lxdm.service"])
def test_login_setup_records_no_display_manager_and_creates_no_user(tmp_path, dm):
    root = tmp_path / "root"
    root.mkdir()
    result = run_setup(tmp_path, root, stub_tools(tmp_path), dm=dm)
    assert result.returncode == 0, result.stderr
    assert state_file(root) == "dm=none\n"
    assert not (tmp_path / "useradd.args").exists()
    assert created_paths(root) == ["var", "var/lib", "var/lib/snow-qa", "var/lib/snow-qa/login-setup"]


def test_login_setup_keeps_an_existing_account(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    tools = stub_tools(tmp_path)
    (tools / "id").write_text('#!/bin/sh\n[ "$1" = -u ] && printf 1000\n')
    assert run_setup(tmp_path, root, tools).returncode == 0
    assert not (tmp_path / "useradd.args").exists()


def test_login_setup_writes_only_below_etc_and_var_of_the_root():
    script = scripts()["login-setup.sh"]
    assert "set -Eeuo pipefail" in script and 'root="${SNOW_QA_ROOT:-}"' in script
    assert "$root/usr" not in script and "root/usr" not in script
    written = re.findall(r'"\$root/([^"]*)"', script)
    assert written and all(path.startswith(("etc/", "var/")) for path in written)
    # A missing display manager creates no account.
    assert "exit 0" in script.split("case \"$dm\" in", 1)[1].split("esac", 1)[0]


# --------------------------------------------------------------- guest login

def write_process(root, pid, argv, uid=1000):
    folder = root / "proc" / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status").write_text("Name:\tx\nUid:\t%d\t%d\t%d\t%d\n" % (uid, uid, uid, uid))
    (folder / "cmdline").write_bytes(b"".join(part.encode() + b"\0" for part in argv))


def drop_process(root, pid):
    for path in sorted((root / "proc" / str(pid)).rglob("*"), reverse=True):
        path.unlink()
    (root / "proc" / str(pid)).rmdir()


def login_guest(qa, tmp_path, monkeypatch, *, phase="login-1", product="snow", dm="gdm",
                session="wayland", journal=b"", pw_dir="/home/snowqa", app_wait=60,
                entries=(), binaries=(), processes=(), on_sleep=None, mark_done_rc=0,
                markers=False):
    """Stand up the first-login collector with every guest command replaced."""
    m = login_manifest(product)
    m["first_login_probe"] = {"session_wait": 120, "app_wait": app_wait}
    normalized = tmp_path / "normalized.json"
    normalized.write_bytes(qa.canonical(m))
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    for name, value in {"phase": phase, "nonce": NONCE, "image_n": m["image_n"],
                        "image_n_plus_1": m["image_n_plus_1"],
                        "target_ref": m["target_ref"]}.items():
        (credentials / name).write_text(value + "\n")
    boot = tmp_path / "boot_id"
    boot.write_text(BOOT_ID + "\n")
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "proc").mkdir()
    if dm == "gdm":
        (root / "etc/gdm3").mkdir()
        (root / "etc/gdm3/daemon.conf").write_text(
            "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=snowqa\n")
    if dm == "sddm":
        (root / "etc/sddm.conf.d").mkdir(parents=True)
        (root / "etc/sddm.conf.d/zz-snow-qa-autologin.conf").write_text(
            "# snow-qa: test-only\n[Autologin]\nUser=snowqa\nSession=plasma.desktop\n")
    for name in entries:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("[Desktop Entry]\n")
    for name in binaries:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("#!/bin/sh\n")
    home = root / pw_dir.lstrip("/") if pw_dir else None
    if home is not None:
        (home / ".config").mkdir(parents=True)
        if markers:
            (home / ".config/snow-first-setup.done").write_text("")
            (home / ".config/chairlift").mkdir()
            (home / ".config/chairlift/first-run.done").write_text("")
    for pid, argv in processes:
        write_process(root, pid, argv)
    clock = {"now": 1000.0}
    monkeypatch.setattr(qa, "LOGIN_ROOT", root)
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized)
    monkeypatch.setattr(qa, "GUEST_PATHS", {**qa.GUEST_PATHS, "boot_id": boot})
    monkeypatch.setattr(qa.time, "monotonic", lambda: clock["now"])

    def fake_sleep(seconds):
        clock["now"] += seconds
        if on_sleep is not None:
            on_sleep(clock)

    monkeypatch.setattr(qa.time, "sleep", fake_sleep)
    if pw_dir is None:
        monkeypatch.setattr(qa, "login_account", lambda: None)
    else:
        monkeypatch.setattr(qa, "login_account", lambda: types.SimpleNamespace(
            pw_uid=1000, pw_name="snowqa", pw_dir=pw_dir))
    calls = []

    def fake_login_run(args, timeout=120):
        calls.append(args)
        if args[:2] == ["systemctl", "show"]:
            return (0, dm.encode() + b".service\n") if dm else (1, b"")
        if args[:2] == ["loginctl", "show-user"]:
            return (0, b"" if session is None else b"3\n")
        if args[:2] == ["loginctl", "show-session"]:
            return 0, ("Type=%s\nClass=user\nState=active\nDesktop=plasmax11\n" % session).encode()
        if args[0] == "journalctl":
            return 0, journal() if callable(journal) else journal
        if args[0] == "runuser":
            # The app's own completion command writes its done marker.
            if "--mark-done" in args:
                (home / ".config/chairlift").mkdir(parents=True, exist_ok=True)
                (home / ".config/chairlift/first-run.done").write_text("")
            if args[-1].endswith("complete-setup"):
                (home / ".config/snow-first-setup.done").write_text("")
            return mark_done_rc, b""
        pytest.fail(f"unexpected login tool {args}")

    monkeypatch.setattr(qa, "login_run", fake_login_run)
    status = {"spec": {"image": {"image": m["target_ref"], "transport": "registry"}},
              "status": {"booted": {"image": {"imageDigest": "sha256:" + N, "version": m["version_n"]}},
                         "staged": None, "rollback": None}}

    def fake_subprocess(args, **kwargs):
        if args[:2] == ["bootc", "status"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(status).encode(), b"")
        pytest.fail(f"unexpected guest command {args}")

    monkeypatch.setattr(qa.subprocess, "run", fake_subprocess)
    return credentials, root, calls


def login_payload(qa, line):
    import base64
    assert line.startswith("SNOW_LOGIN_V1 ")
    encoded = line.split()[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def test_guest_login_records_first_setup_running_on_the_first_login(qa, tmp_path, monkeypatch):
    credentials, root, calls = login_guest(
        qa, tmp_path, monkeypatch,
        entries=["etc/xdg/autostart/org.frostyard.FirstSetup.autostart.desktop"],
        binaries=["usr/bin/snow-first-setup"],
        processes=[("101", ["/usr/bin/snow-first-setup", "--autostart"])])
    record = login_payload(qa, qa.login(credentials))
    assert set(record) == qa.LOGIN_RECORD_FIELDS
    assert record["phase"] == "login-1" and record["boot_id"] == BOOT_ID
    assert record["booted_digest"] == N
    assert record["display_manager"] == "gdm" and record["autologin"] is True
    assert record["session"] == {"type": "wayland", "class": "user", "state": "active",
                                 "desktop": "plasmax11"}
    assert record["entries"] == [
        str(root / "etc/xdg/autostart/org.frostyard.FirstSetup.autostart.desktop")]
    assert record["apps"] == {"first-setup": {"entry": True, "binary": True},
                              "chairlift": {"entry": False, "binary": False}}
    # The same pid in two consecutive polls: the app ran, and the whole window
    # is watched before any mark-done command runs.
    assert record["started"] == ["first-setup"] and record["waited"] == 60
    assert record["mark_done"] == [{"app": "first-setup", "rc": 0}]
    assert record["markers"] == {"first-setup": True, "chairlift": False}
    assert ["runuser", "-u", "snowqa", "--"] == calls[-1][:4]
    assert calls[-1][-1].endswith("complete-setup")
    assert "HOME=/home/snowqa" in calls[-1] and "XDG_RUNTIME_DIR=/run/user/1000" in calls[-1]


def test_guest_login_does_not_count_a_gate_process_seen_only_once(qa, tmp_path, monkeypatch):
    root = tmp_path / "root"

    def gone(clock):
        if (root / "proc" / "101").exists():
            drop_process(root, "101")

    credentials, root, _ = login_guest(
        qa, tmp_path, monkeypatch, app_wait=30,
        processes=[("101", ["/usr/bin/snow-first-setup"])], on_sleep=gone)
    record = login_payload(qa, qa.login(credentials))
    assert record["started"] == [] and record["mark_done"] == []
    # The full window was observed, then the session ended without an app.
    assert record["waited"] == 30


def test_guest_login_records_chairlift_presented_then_marks_it_done(qa, tmp_path, monkeypatch):
    credentials, root, calls = login_guest(
        qa, tmp_path, monkeypatch,
        entries=["etc/xdg/autostart/org.frostyard.ChairLift.FirstRun.desktop"],
        binaries=["usr/bin/chairlift-first-run"],
        journal=b"chairlift[900]: first-run: window presented\n")
    record = login_payload(qa, qa.login(credentials))
    assert record["started"] == ["chairlift"]
    assert record["mark_done"] == [{"app": "chairlift", "rc": 0}]
    assert record["markers"]["chairlift"] is True
    assert calls[-1][-2:] == ["/usr/bin/chairlift-first-run", "--mark-done"]


def test_guest_login_marks_a_failed_first_run_without_failing_the_record(qa, tmp_path, monkeypatch):
    credentials, _, _ = login_guest(
        qa, tmp_path, monkeypatch, mark_done_rc=1,
        processes=[("101", ["/usr/bin/snow-first-setup"])])
    record = login_payload(qa, qa.login(credentials))
    assert record["started"] == ["first-setup"]
    assert record["mark_done"] == [{"app": "first-setup", "rc": 1}]


def test_guest_login_second_boot_never_marks_done_and_watches_the_whole_window(qa, tmp_path, monkeypatch):
    credentials, _, calls = login_guest(qa, tmp_path, monkeypatch, phase="login-2", app_wait=30)
    record = login_payload(qa, qa.login(credentials))
    assert record["phase"] == "login-2" and record["started"] == []
    assert record["mark_done"] == [] and record["waited"] == 30
    assert not any(call[0] == "runuser" for call in calls)


def test_guest_login_sundog_first_login_starts_no_known_app(qa, tmp_path, monkeypatch):
    credentials, _, _ = login_guest(qa, tmp_path, monkeypatch, product="sundog", dm="sddm")
    record = login_payload(qa, qa.login(credentials))
    assert record["display_manager"] == "sddm" and record["autologin"] is True
    assert record["started"] == [] and record["session"]["type"] == "wayland"


def test_guest_login_floe_has_no_display_manager_and_no_session(qa, tmp_path, monkeypatch):
    credentials, _, calls = login_guest(qa, tmp_path, monkeypatch, product="floe", dm=None,
                                        pw_dir=None)
    record = login_payload(qa, qa.login(credentials))
    assert record["display_manager"] is None and record["autologin"] is False
    assert record["session"] is None and record["started"] == [] and record["waited"] == 0
    assert not any(call[0] in ("loginctl", "journalctl", "runuser") for call in calls)


def test_guest_login_records_no_session_when_none_appears(qa, tmp_path, monkeypatch):
    credentials, _, _ = login_guest(qa, tmp_path, monkeypatch, session=None)
    record = login_payload(qa, qa.login(credentials))
    assert record["session"] is None and record["started"] == [] and record["waited"] == 0
    assert record["autologin"] is True


def test_guest_login_a_missing_autologin_configuration_is_recorded_not_failed(qa, tmp_path, monkeypatch):
    credentials, root, _ = login_guest(qa, tmp_path, monkeypatch)
    (root / "etc/gdm3/daemon.conf").write_text("[daemon]\nWaylandEnable=true\n")
    record = login_payload(qa, qa.login(credentials))
    assert record["autologin"] is False


def test_guest_login_fatal_error_publishes_the_error_marker(qa, tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr(qa, "publish", published.append)
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "phase").write_text("login-1\n")
    (credentials / "nonce").write_text("not-a-nonce\n")
    assert qa.main(["login", str(credentials)]) == 1
    assert published == ["SNOW_LOGIN_ERR credential"]
    # A manifest without the key is no login guest.
    (credentials / "nonce").write_text(NONCE + "\n")
    rejected = []
    monkeypatch.setattr(qa, "publish", rejected.append)
    normalized = login_files(tmp_path, qa, manifest())
    monkeypatch.setattr(qa, "GUEST_MANIFEST", normalized)
    assert qa.main(["login", str(credentials)]) == 1
    assert rejected == ["SNOW_LOGIN_ERR login_manifest"]


# --------------------------------------------------------------- login-phase

def run_login_phase(qa, tmp_path, serial_text, phase="login-1", nonce=NONCE, prior="none"):
    normalized = login_files(tmp_path, qa)
    serial = tmp_path / "serial"
    serial.write_text(serial_text)
    out = tmp_path / f"{phase}.json"
    return qa.main(["login-phase", str(normalized), phase, nonce, prior, str(serial), str(out)]), out


def test_login_phase_accepts_one_record_and_writes_it(qa, tmp_path, capsys):
    record = login_record(started=["first-setup"], mark_done=[{"app": "first-setup", "rc": 0}],
                          markers={"first-setup": True, "chairlift": False})
    assert run_login_phase(qa, tmp_path, login_serial(qa, record))[0] == 0
    out = tmp_path / "login-1.json"
    assert capsys.readouterr().out.splitlines() == [BOOT_ID, "started=first-setup", "session=wayland"]
    assert out.stat().st_mode & 0o777 == 0o600
    assert json.loads(out.read_text()) == record


def test_login_phase_accepts_identical_copies_and_an_empty_boot(qa, tmp_path, capsys):
    record = login_record()
    line = login_line(qa, record)
    assert run_login_phase(qa, tmp_path, line + "\n" + line + "\n")[0] == 0
    assert capsys.readouterr().out.splitlines() == [BOOT_ID, "started=none", "session=wayland"]
    second = login_record("login-2", boot_id=SECOND_BOOT_ID)
    assert run_login_phase(qa, tmp_path, login_serial(qa, second), phase="login-2",
                           nonce=NONCE, prior=BOOT_ID)[0] == 0
    assert capsys.readouterr().out.splitlines()[1] == "started=none"


def test_login_phase_accepts_a_record_only_the_kernel_log_carried(qa, tmp_path, capsys):
    record = login_record(started=["chairlift"])
    serial = tmp_path / "serial"
    serial.write_bytes(b"".join(line.encode() + b"\n" for line in qa.kmsg_lines(login_line(qa, record))))
    qa.kmsg_join(str(serial))
    capsys.readouterr()
    normalized = login_files(tmp_path, qa)
    out = tmp_path / "login-1.json"
    assert qa.main(["login-phase", str(normalized), "login-1", NONCE, "none",
                    str(serial), str(out)]) == 0
    assert capsys.readouterr().out.splitlines() == [BOOT_ID, "started=chairlift", "session=wayland"]


@pytest.mark.parametrize("damage,code", [
    (lambda r: r.update(nonce="0" * 32), "record_identity"),
    (lambda r: r.update(phase="login-2"), "record_identity"),
    (lambda r: r.update(boot_id="not-a-boot-id"), "boot_identity"),
    (lambda r: r.update(booted_digest=NEXT), "booted_image"),
    (lambda r: r.update(extra=1), "record_fields"),
    (lambda r: r.pop("waited"), "record_fields"),
    (lambda r: r.update(display_manager="lightdm"), "record_fields"),
    (lambda r: r.update(autologin="yes"), "record_fields"),
    (lambda r: r.update(session={"type": "wayland"}), "record_session"),
    (lambda r: r.update(session={"type": "wayland", "class": "user", "state": "active",
                                 "desktop": "x" * 65}), "record_session"),
    # Only a real user session is evidence of a first login.
    (lambda r: r.update(session={"type": "tty", "class": "user", "state": "active",
                                 "desktop": ""}), "record_session"),
    (lambda r: r.update(session={"type": "wayland", "class": "greeter", "state": "active",
                                 "desktop": "plasma"}), "record_session"),
    (lambda r: r.update(session={"type": "wayland", "class": "user", "state": "closing",
                                 "desktop": "plasma"}), "record_session"),
    (lambda r: r.update(session={"type": "wayland", "class": "user", "state": "active",
                                 "desktop": "plasma\n"}), "record_session"),
    (lambda r: r.update(entries=["etc/xdg/autostart/x.desktop"]), "record_entries"),
    (lambda r: r.update(entries=["/etc/xdg/autostart/%s.desktop" % ("x" * 200)]), "record_entries"),
    (lambda r: r.update(entries=["/etc/xdg/autostart/%s" % ("x.desktop",)] * 17), "record_entries"),
    (lambda r: r.update(apps={"first-setup": {"entry": True}}), "record_apps"),
    (lambda r: r.update(apps={"first-setup": {"entry": True, "binary": "yes"},
                              "chairlift": {"entry": False, "binary": False}}), "record_apps"),
    (lambda r: r.update(started=["first-setup", "first-setup"]), "record_started"),
    (lambda r: r.update(started=["other-app"]), "record_started"),
    (lambda r: r.update(mark_done=[{"app": "first-setup", "rc": True}]), "record_mark_done"),
    (lambda r: r.update(mark_done=[{"app": "other-app", "rc": 0}]), "record_mark_done"),
    (lambda r: r.update(markers={"first-setup": True}), "record_markers"),
    (lambda r: r.update(markers={"first-setup": True, "chairlift": "no"}), "record_markers"),
    (lambda r: r.update(waited="35"), "record_waited"),
    (lambda r: r.update(waited=-1), "record_waited"),
])
def test_login_phase_rejects_untrustworthy_records(qa, tmp_path, capsys, damage, code):
    record = login_record()
    damage(record)
    assert run_login_phase(qa, tmp_path, login_serial(qa, record))[0] == 1
    assert capsys.readouterr().err.strip() == "qa_failed:" + code
    assert not (tmp_path / "login-1.json").exists()


def test_login_phase_requires_exactly_one_record(qa, tmp_path, capsys):
    first = login_record()
    second = login_record(boot_id=SECOND_BOOT_ID)
    assert run_login_phase(qa, tmp_path, login_serial(qa, first, second))[0] == 1
    assert capsys.readouterr().err.strip() == "qa_failed:record_count"
    # No record at all is no record, never a passing empty boot.
    assert run_login_phase(qa, tmp_path, "no marker here\n")[0] == 1
    assert capsys.readouterr().err.strip() == "qa_failed:record_count"


def test_login_phase_rejects_the_previous_boot_and_a_foreign_phase(qa, tmp_path, capsys):
    assert run_login_phase(qa, tmp_path, login_serial(qa, login_record()),
                           prior=BOOT_ID)[0] == 1
    assert capsys.readouterr().err.strip() == "qa_failed:boot_identity"
    assert run_login_phase(qa, tmp_path, login_serial(qa, login_record("login-2", boot_id=SECOND_BOOT_ID)),
                           phase="login-2", prior=BOOT_ID)[0] == 0


def test_login_phase_rejects_a_manifest_without_the_key(qa, tmp_path, capsys):
    normalized = login_files(tmp_path, qa, manifest())
    serial = tmp_path / "serial"
    serial.write_text(login_serial(qa, login_record()))
    out = tmp_path / "login-1.json"
    assert qa.main(["login-phase", str(normalized), "login-1", NONCE, "none",
                    str(serial), str(out)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:phase_arguments"


# -------------------------------------------------------------- login-report

def write_record(tmp_path, name, record):
    path = tmp_path / name
    path.write_text(json.dumps(record))
    return path


def run_login_report(qa, tmp_path, *records, product="snow"):
    paths = [str(write_record(tmp_path, f"login-{index + 1}.json", record))
             for index, record in enumerate(records)]
    normalized = login_files(tmp_path, qa, login_manifest(product), product)
    return qa.main(["login-report", str(normalized), *paths])


def two_records(first=None, second=None):
    once = login_record("login-1", **(first or {}))
    twice = login_record("login-2", boot_id=SECOND_BOOT_ID, **(second or {}))
    return [once, twice]


@pytest.mark.parametrize("records,product,summary", [
    # Floe is headless: there is no first-run autostart entry at all.
    ([login_record(display_manager=None, autologin=False, session=None)], "floe", "headless-no-entry"),
    ([login_record(display_manager=None, autologin=False, session=None,
                   entries=["/etc/xdg/autostart/org.frostyard.FirstSetup.autostart.desktop"])],
     "floe", "headless-entry"),
    ([login_record(display_manager=None, autologin=False, session=None,
                   apps={"first-setup": {"entry": False, "binary": True},
                         "chairlift": {"entry": False, "binary": False}})], "floe", "headless-entry"),
    # A desktop product whose display manager was never named proves nothing.
    ([login_record(display_manager=None, autologin=False, session=None)], "snow",
     "unproven-no-display-manager"),
    ([login_record(autologin=False)], "snow", "unproven-no-autologin"),
    ([login_record(session=None)], "snow", "unproven-no-session"),
    ([login_record()], "snow", "unproven-no-second-login"),
    (two_records(), "snow", "none"),
    (two_records({"started": ["first-setup"], "mark_done": [{"app": "first-setup", "rc": 0}],
                  "markers": {"first-setup": True, "chairlift": False}}), "snow", "first-setup-once"),
    (two_records({"started": ["first-setup"], "mark_done": [{"app": "first-setup", "rc": 0}]}),
     "snow", "first-setup-once-mark-done-failed"),
    (two_records({"started": ["first-setup"], "mark_done": [{"app": "first-setup", "rc": 1}],
                  "markers": {"first-setup": True, "chairlift": False}}),
     "snow", "first-setup-once-mark-done-failed"),
    (two_records({"started": ["chairlift", "first-setup"],
                  "mark_done": [{"app": "chairlift", "rc": 0}, {"app": "first-setup", "rc": 0}],
                  "markers": {"first-setup": True, "chairlift": True}}), "snow", "both-once"),
    (two_records({"started": ["first-setup"], "mark_done": [{"app": "first-setup", "rc": 0}],
                  "markers": {"first-setup": True, "chairlift": False}},
                 {"started": ["first-setup"]}), "snow", "repeated-first-setup"),
    (two_records({}, {"started": ["chairlift"]}), "snow", "late-chairlift"),
    (two_records({"started": ["first-setup"]}, {"started": ["chairlift"]}),
     "snow", "repeated-chairlift+first-setup"),
])
def test_login_report_summaries(qa, tmp_path, capsys, records, product, summary):
    assert run_login_report(qa, tmp_path, *records, product=product) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "first_login=" + summary
    assert re.fullmatch(r"[a-z0-9+-]{1,64}", summary)
    # One detail line per boot, in boot order.
    assert [line.split()[0] for line in printed[1:]] == ["phase=" + record["phase"] for record in records]


def test_login_report_detail_line_names_the_evidence(qa, tmp_path, capsys):
    record = login_record(
        started=["first-setup"],
        entries=["/etc/xdg/autostart/org.frostyard.FirstSetup.autostart.desktop"],
        mark_done=[{"app": "first-setup", "rc": 0}],
        markers={"first-setup": True, "chairlift": False})
    assert run_login_report(qa, tmp_path, record) == 0
    assert capsys.readouterr().out.splitlines()[1] == DETAIL


def test_login_report_rejects_a_malformed_written_record(qa, tmp_path, capsys):
    path = tmp_path / "login-1.json"
    path.write_text(json.dumps(login_record(waited="35")))
    normalized = login_files(tmp_path, qa)
    assert qa.main(["login-report", str(normalized), str(path)]) == 1
    assert capsys.readouterr().err.strip() == "qa_failed:record_waited"


# --------------------------------------------------------------- run.sh plan

def plan_block():
    script = run_script()
    # Both probe-mode flags and the plan they select, as one runnable block.
    return re.search(r"(?ms)^sysext_mode=.*?^fi\n", script).group()


@pytest.mark.parametrize("name,expected", [
    ("plain", "installed-n stage boot-n-plus-1 rollback boot-n"),
    ("sysext", "installed-n sysext-enable sysext-reboot"),
    ("login", "login-1 login-2"),
    ("floe", "login-1"),
    ("sundog", "login-1 login-2"),
])
def test_run_script_plans_the_login_boots_from_the_manifest(tmp_path, name, expected):
    work = tmp_path / name
    work.mkdir()
    documents = {"plain": {"product": "snow"}, "sysext": {"product": "snow", "sysext_probe": {}},
                 "login": {"product": "snow", "first_login_probe": {}},
                 "floe": {"product": "floe", "first_login_probe": {}},
                 "sundog": {"product": "sundog", "first_login_probe": {}}}
    (work / "normalized.json").write_text(json.dumps(documents[name]))
    result = subprocess.run(["bash", "-c",
                             'fail() { exit 9; }; WORK="$PWD"\n' + plan_block() +
                             '\nprintf "%s\\n" "${phase_plan[*]}"'],
                            cwd=work, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_run_script_injects_the_setup_unit_only_in_login_1():
    script = run_script()
    loop = snow.phase_loop(script)
    assert loop.count("raw.qemu=") == 1
    assert loop.count("systemd.extra-unit.snow-login-setup.service") == 1
    assert 'login_credential=" -smbios type=11,value=io.systemd.credential.binary:' \
           'systemd.extra-unit.snow-login-setup.service=$lb"' in loop
    assert loop.count("$td$login_credential") == 1
    # The setup unit runs before the display manager and the first user session.
    assert ("Before=display-manager.service gdm.service gdm3.service sddm.service "
            "systemd-user-sessions.service") in loop
    assert "TimeoutStartSec=120" in loop
    assert '[[ ${#lb} -lt 60000 ]] || fail unit_size' in loop
    # Only login-1 pulls it, and only login-1 encodes the setup script.
    guarded = re.search(r'(?ms)if \[\[ "\$phase" == login-1 \]\]; then\n.*?\n  else\n', loop).group()
    assert "login_b64=" in guarded and "snow-login-setup.service" in guarded
    assert loop.count("login_b64=") == 1


def test_run_script_keeps_the_other_phases_credentials_unchanged():
    _ = scripts()
    loop = snow.phase_loop(run_script())
    # The five-boot and sysext boots keep exactly today's drop-in and credentials.
    assert "gd=$(printf '[Unit]\\nWants=snow-guest.service' | base64 -w0) || fail unit_encoding" in loop
    assert "systemd.unit-dropin.multi-user.target=$gd" in loop
    assert ("systemd.extra-unit.snow-guest.service=$gb -smbios type=11,value="
            "io.systemd.credential.binary:systemd.unit-dropin.multi-user.target=$gd") in loop
    # login-1 alone gets the wider drop-in.
    assert "gd=$(printf '[Unit]\\nWants=snow-guest.service snow-login-setup.service'" in loop


def test_run_script_wires_the_login_boots_into_the_shared_body():
    script = run_script()
    loop = snow.phase_loop(script)
    assert 'await SNOW_LOGIN_V1 "$((budget + 300))" SNOW_LOGIN_ERR' in loop
    assert 'qa.py login-phase' in loop
    assert 'persist "$WORK/$phase.json" "$EVIDENCE/$phase.json"' in loop
    assert "session=%s started=%s" in loop
    assert 'login-1|login-2) guest_mode=login; guest_after=" multi-user.target";;' in loop
    assert "REASON=\"verified_first_login_probe;first_login=$login_summary" in script
    assert 'blocked "first_login_$login_summary"' in script
    assert "ADR-0003" in script
    # A fatal login guest error is its own bounded code, never the sysext one.
    assert 'fail "login_guest_error:$code"' in script
    assert "SNOW_LOGIN_ERR ([a-z_]{1,40})" in script
    assert ("SNOW_SYSEXT_V1 || \"$needle\" == SNOW_LOGIN_V1" in script)


def test_run_script_reports_the_first_login_after_the_boots():
    script = run_script()
    report = re.search(r'(?ms)^if \[\[ "\$login_mode" == 1 \]\]; then\n.*?^fi\n', script).group()
    assert "qa.py login-report" in report
    assert "|$login_summary\" =~ ^[a-z0-9+-]{1,64}$" not in report
    assert "=~ ^[a-z0-9+-]{1,64}$" in report
    assert 'cp -- "$WORK/first-login" "$OUT/first-login.txt" || fail login_report' in report
    assert 'persist "$OUT/first-login.txt" "$EVIDENCE/first-login.txt"' in report
    assert 'printf \'first_login=%s\\n\' "$login_summary" >> "$OUT/checks.txt"' in report
    # Unproven is BLOCKED, never FAILED (ADR-0003).
    assert 'if [[ "$login_summary" == unproven-* ]]; then' in report
    assert report.index("login-report") < report.index("blocked")
    # The results file exists from the start, like fsverity.txt.
    assert ': > "$OUT/first-login.txt" || true' in script
    # finish() keeps it best effort, so it can never change the verdict.
    finish = script.split("finish() {", 1)[1].split("\n    trap finish EXIT", 1)[0]
    assert 'persist "$OUT/first-login.txt" "$EVIDENCE/first-login.txt" || true' in finish
    assert 'persist "$OUT/fsverity.txt" "$EVIDENCE/fsverity.txt" || true' in finish


def test_workflow_template_publishes_the_first_login_report():
    template = yaml.safe_load((SOURCE.parent / "run-snow-bootc-lifecycle.yaml").read_text())
    entry = next(t for t in template["spec"]["templates"] if t["name"] == template["spec"]["entrypoint"])
    outputs = {item["name"]: item["valueFrom"] for item in entry["outputs"]["parameters"]}
    assert outputs["first-login"]["path"] == "/tmp/results/first-login.txt"
    assert outputs["first-login"]["default"] == "evidence unavailable"


# ------------------------------------------------- offline runner behaviour

def login_error_parser():
    script = run_script()
    block = re.search(r"observed_failure\" == SNOW_LOGIN_ERR \]\]; then\n.*?"
                      r"code=\$\(python3 - \"\$WORK/serial\" <<'PY'\n(.*?)\nPY\n", script, re.S)
    assert block
    return block.group(1)


@pytest.mark.parametrize("serial,code", [
    ("SNOW_LOGIN_ERR credential\n", "credential"),
    ("SNOW_LOGIN_ERR credential\r\n", "credential"),
    ("boot noise\nSNOW_LOGIN_ERR login_manifest\n", "login_manifest"),
    # A cut fragment followed by kernel text is not a code (ADR-0014).
    ("SNOW_LOGIN_ERR credential[   12.345678] kernel text\n", None),
    # A record and an error marker in one boot contradict each other.
    ("SNOW_LOGIN_ERR credential\nSNOW_LOGIN_V1 abc\n", None),
    # Two different codes cannot be resolved to one.
    ("SNOW_LOGIN_ERR credential\nSNOW_LOGIN_ERR login_manifest\n", None),
    ("SNOW_LOGIN_ERR not a code\n", None),
])
def test_login_error_parser_accepts_only_one_complete_code(tmp_path, serial, code):
    parser = tmp_path / "parse.py"
    parser.write_text(login_error_parser())
    path = tmp_path / "serial"
    path.write_bytes(serial.encode())
    result = subprocess.run([sys.executable, str(parser), str(path)], capture_output=True, text=True)
    assert result.returncode == (0 if code else 1)
    assert result.stdout.strip() == (code or "")


def login_phase_branch():
    branch = re.search(r"(?ms)^\s*login-1\|login-2\)\n.*?;;\n", run_script()).group()
    return branch


def run_login_branch(tmp_path, stub, *, phase="login-1", prior="none", seen=":"):
    """Run the runner's login phase arm with stub tools, as the loop would."""
    branch = login_phase_branch().replace("/opt/snow-qa/qa.py", str(tmp_path / "qa.py"))
    (tmp_path / "qa.py").write_text(stub)
    work, out, evidence = tmp_path / "work", tmp_path / "results", tmp_path / "evidence"
    for folder in (work, out, evidence):
        folder.mkdir(exist_ok=True)
    (work / "serial-channel").write_text("serial_record=1 kmsg_record=0\n")
    (out / "checks.txt").write_text("")
    script = (
        'set -Eeuo pipefail\n'
        'fail() { printf "FAIL:%s\\n" "$1"; exit 9; }\n'
        'persist() { cp -- "$1" "$2"; }\n'
        'await() { return 0; }\n'
        f'WORK={work} OUT={out} EVIDENCE={evidence} phase={phase} nonce={NONCE} prior={prior} budget=900\n'
        f'seen_boot_ids="{seen}"\n'
        'case "$phase" in\n' + branch + "esac\n")
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result, work, out, evidence


STUB_RECORD = """import json, sys
from pathlib import Path
Path(sys.argv[7]).write_text(json.dumps({"stub": True}))
print("%s")
print("started=first-setup")
print("session=wayland")
""" % BOOT_ID


def test_run_script_login_phase_records_the_boot_and_its_evidence(tmp_path):
    result, work, out, evidence = run_login_branch(tmp_path, STUB_RECORD)
    assert result.returncode == 0, result.stderr
    assert (out / "checks.txt").read_text() == (
        "phase=login-1 boot_id=%s session=wayland started=first-setup\n" % BOOT_ID)
    assert json.loads((evidence / "login-1.json").read_text()) == {"stub": True}
    assert (evidence / "checks.txt").read_text() == (out / "checks.txt").read_text()
    assert (evidence / "serial-channel.txt").read_text() == "phase=login-1 serial_record=1 kmsg_record=0\n"


def test_run_script_login_phase_fails_closed_on_a_rejected_record(tmp_path):
    result, _, out, evidence = run_login_branch(tmp_path, "import sys\nsys.exit(1)\n")
    assert result.stdout.strip() == "FAIL:phase_mismatch"
    assert (out / "checks.txt").read_text() == ""
    assert not (evidence / "login-1.json").exists()


def test_run_script_login_phase_rejects_a_reused_boot_id(tmp_path):
    result, _, _, _ = run_login_branch(tmp_path, STUB_RECORD, phase="login-2",
                                       prior="none", seen=":%s:" % BOOT_ID)
    assert result.stdout.strip() == "FAIL:reused_boot_id"
    assert "01234567" not in result.stdout


def report_block():
    return re.search(r'(?ms)^if \[\[ "\$login_mode" == 1 \]\]; then\n.*?^fi\n', run_script()).group()


def run_report_block(tmp_path, stdout, two_boots=True):
    block = report_block().replace("/opt/snow-qa/qa.py", str(tmp_path / "qa.py"))
    (tmp_path / "qa.py").write_text(stdout)
    work, out, evidence = tmp_path / "work", tmp_path / "results", tmp_path / "evidence"
    for folder in (work, out, evidence):
        folder.mkdir(exist_ok=True)
    (work / "login-1.json").write_text("{}")
    if two_boots:
        (work / "login-2.json").write_text("{}")
    (out / "checks.txt").write_text("")
    script = (
        'set -Eeuo pipefail\n'
        'fail() { printf "FAIL:%s\\n" "$1"; exit 9; }\n'
        'blocked() { printf "BLOCKED:%s\\n" "$1"; exit 0; }\n'
        'persist() { cp -- "$1" "$2"; }\n'
        f'WORK={work} OUT={out} EVIDENCE={evidence} login_mode=1\n' + block)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result, work, out, evidence


@pytest.mark.parametrize("summary,verdict", [
    ("first-setup-once", ""),
    ("both-once", ""),
    ("headless-no-entry", ""),
    ("unproven-no-autologin", "BLOCKED:first_login_unproven-no-autologin"),
    ("unproven-no-display-manager", "BLOCKED:first_login_unproven-no-display-manager"),
    ("unproven-no-second-login", "BLOCKED:first_login_unproven-no-second-login"),
    ("unproven-no-session", "BLOCKED:first_login_unproven-no-session"),
])
def test_run_script_reports_the_first_login_and_blocks_only_unproven(tmp_path, summary, verdict):
    stub = 'print("first_login=%s")\nprint("phase=login-1 dm=gdm autologin=yes session=wayland")\n' % summary
    result, _, out, evidence = run_report_block(tmp_path, stub)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == verdict
    assert (out / "checks.txt").read_text().splitlines()[-1] == "first_login=" + summary
    assert (out / "first-login.txt").read_text().splitlines()[0] == "first_login=" + summary
    assert (evidence / "first-login.txt").read_text() == (out / "first-login.txt").read_text()


@pytest.mark.parametrize("stdout", [
    'print("first_login=PASS")\n',
    'print("first_login=First-Setup")\n',
    'print("first_login=")\n',
    'print("something else")\n',
    'import sys\nsys.exit(1)\n',
])
def test_run_script_login_report_fails_closed_on_a_bad_summary(tmp_path, stdout):
    result, _, _, evidence = run_report_block(tmp_path, stdout)
    assert result.stdout.strip() == "FAIL:login_report"
    assert not (evidence / "first-login.txt").exists()


# ------------------------------------------------ review fixes (modes, home)

def test_login_setup_keeps_an_existing_etc_directory_and_file_mode(tmp_path):
    root = tmp_path / "root"
    (root / "etc/gdm3").mkdir(parents=True)
    (root / "etc/gdm3").chmod(0o755)
    (root / "etc/gdm3/daemon.conf").write_text("[daemon]\nWaylandEnable=true\n")
    (root / "etc/gdm3/daemon.conf").chmod(0o640)
    assert run_setup(tmp_path, root, stub_tools(tmp_path)).returncode == 0
    # install -d would chmod an existing directory to 0700, and then the greeter
    # and the session user could not read it.
    assert (root / "etc/gdm3").stat().st_mode & 0o777 == 0o755
    assert (root / "etc/gdm3/daemon.conf").stat().st_mode & 0o777 == 0o640
    assert "AutomaticLogin=snowqa" in (root / "etc/gdm3/daemon.conf").read_text()
    assert "WaylandEnable=true" in (root / "etc/gdm3/daemon.conf").read_text()


def test_login_setup_creates_a_readable_gdm_configuration(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert run_setup(tmp_path, root, stub_tools(tmp_path)).returncode == 0
    assert (root / "etc/gdm3").stat().st_mode & 0o777 == 0o755
    assert (root / "etc/gdm3/daemon.conf").stat().st_mode & 0o777 == 0o644


def test_login_setup_creates_a_readable_sddm_configuration(tmp_path):
    root = tmp_path / "root"
    (root / "etc/sddm.conf.d").mkdir(parents=True)
    (root / "etc/sddm.conf.d").chmod(0o755)
    assert run_setup(tmp_path, root, stub_tools(tmp_path), dm="sddm.service").returncode == 0
    conf = root / "etc/sddm.conf.d/zz-snow-qa-autologin.conf"
    assert conf.stat().st_mode & 0o777 == 0o644
    # An existing directory keeps the mode the image gave it.
    assert (root / "etc/sddm.conf.d").stat().st_mode & 0o777 == 0o755
    # The QA state stays private.
    assert (root / "var/lib/snow-qa").stat().st_mode & 0o777 == 0o700


def test_login_setup_creates_a_missing_sddm_directory_readable(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert run_setup(tmp_path, root, stub_tools(tmp_path), dm="sddm.service").returncode == 0
    assert (root / "etc/sddm.conf.d").stat().st_mode & 0o777 == 0o755


def test_guest_login_reads_the_markers_in_the_accounts_own_home(qa, tmp_path, monkeypatch):
    credentials, root, calls = login_guest(qa, tmp_path, monkeypatch,
                                          pw_dir="/var/home/snowqa", markers=True)
    autostart = root / "var/home/snowqa/.config/autostart"
    autostart.mkdir(parents=True)
    (autostart / "org.frostyard.FirstSetup.autostart.desktop").write_text("[Desktop Entry]\n")
    record = login_payload(qa, qa.login(credentials))
    assert record["markers"] == {"first-setup": True, "chairlift": True}
    assert record["entries"] == [str(autostart / "org.frostyard.FirstSetup.autostart.desktop")]
    assert not (root / "home/snowqa/.config").exists()


def test_guest_login_marks_done_with_the_accounts_home(qa, tmp_path, monkeypatch):
    credentials, root, calls = login_guest(
        qa, tmp_path, monkeypatch, pw_dir="/var/home/snowqa",
        processes=[("101", ["/usr/bin/snow-first-setup"])])
    record = login_payload(qa, qa.login(credentials))
    assert record["mark_done"] == [{"app": "first-setup", "rc": 0}]
    assert "HOME=/var/home/snowqa" in calls[-1]


def test_guest_login_without_an_account_home_records_no_marker(qa, tmp_path, monkeypatch):
    credentials, root, calls = login_guest(qa, tmp_path, monkeypatch, pw_dir=None,
                                           markers=True)
    home = root / "home/snowqa"
    (home / ".config/autostart").mkdir(parents=True)
    (home / ".config/snow-first-setup.done").write_text("")
    (home / ".config/autostart/org.frostyard.FirstSetup.autostart.desktop").write_text("")
    record = login_payload(qa, qa.login(credentials))
    # No account home: no user folder is scanned and both markers are false.
    assert record["markers"] == {"first-setup": False, "chairlift": False}
    assert record["entries"] == [] and record["session"] is None


def test_login_home_accepts_only_a_bounded_absolute_path(qa):
    def account(path):
        return types.SimpleNamespace(pw_dir=path)

    assert qa.login_home(account("/var/home/snowqa")) == "/var/home/snowqa"
    assert qa.login_home(account("/home/a.b-c_d")) == "/home/a.b-c_d"
    for path in (None, "", "home/snowqa", "/home/../etc", "/home/" + "a" * 128,
                 "/var/home/a b", 1000):
        assert qa.login_home(account(path)) is None, path
    assert qa.login_home(None) is None


def test_guest_login_skips_an_autostart_name_it_cannot_carry(qa, tmp_path, monkeypatch):
    credentials, root, _ = login_guest(qa, tmp_path, monkeypatch)
    folder = root / "etc/xdg/autostart"
    folder.mkdir(parents=True)
    # A name that matches the first-run pattern but not the safe path pattern.
    (folder / "first setup ü.desktop").write_text("[Desktop Entry]\n")
    (folder / "org.frostyard.FirstSetup.autostart.desktop").write_text("[Desktop Entry]\n")
    record = login_payload(qa, qa.login(credentials))
    # One odd file name is skipped; it never turns the record into an error.
    assert record["entries"] == [str(folder / "org.frostyard.FirstSetup.autostart.desktop")]


def test_guest_login_reads_the_journal_once_after_the_window(qa, tmp_path, monkeypatch):
    credentials, _, calls = login_guest(qa, tmp_path, monkeypatch, app_wait=120)
    record = login_payload(qa, qa.login(credentials))
    assert record["started"] == [] and record["waited"] == 120
    # One bounded read after the window, never one per 5 s poll.
    assert len([call for call in calls if call[0] == "journalctl"]) == 1
    assert all("2000" in call for call in calls if call[0] == "journalctl")


def test_guest_login_counts_chairlift_only_through_its_journal_line(qa, tmp_path, monkeypatch):
    # A persistent `chairlift --first-run` process proves no presented window.
    credentials, _, _ = login_guest(
        qa, tmp_path, monkeypatch,
        processes=[("900", ["/usr/bin/chairlift", "--first-run"])])
    record = login_payload(qa, qa.login(credentials))
    assert record["started"] == [] and record["mark_done"] == []


def test_guest_login_first_boot_watches_the_whole_window_for_a_late_app(qa, tmp_path, monkeypatch):
    """The presented line arrives at t=20; login-1 must still see it (no early stop)."""
    state = {"now": 1000.0}

    def track(clock):
        state["now"] = clock["now"]

    def journal():
        return (b"chairlift[900]: first-run: window presented\n"
                if state["now"] >= 1020 else b"")

    credentials, _, _ = login_guest(
        qa, tmp_path, monkeypatch, app_wait=60, on_sleep=track, journal=journal,
        processes=[("101", ["/usr/bin/snow-first-setup"])])
    record = login_payload(qa, qa.login(credentials))
    # first-setup runs from the start, the window is presented 20 s later, and
    # mark-done must not have cut the observation short.
    assert record["started"] == ["chairlift", "first-setup"]
    assert record["waited"] == 60
    assert [item["app"] for item in record["mark_done"]] == ["chairlift", "first-setup"]


# --------------------------------------- review fixes (chairlift, sddm, host)

def test_login_setup_comments_out_a_conflicting_sddm_autologin(tmp_path):
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/sddm.conf").write_text(
        "[General]\nNumlock=on\n\n[Autologin]\nUser=other\nSession=plasmax11\nRelogin=false\n\n"
        "[Theme]\nUser=keep\n")
    (root / "etc/sddm.conf").chmod(0o640)
    assert run_setup(tmp_path, root, stub_tools(tmp_path), dm="sddm.service").returncode == 0
    body = (root / "etc/sddm.conf").read_text().splitlines()
    # SDDM reads /etc/sddm.conf after /etc/sddm.conf.d: its [Autologin] section
    # must not win over the drop-in.
    assert "# snow-qa: User=other" in body
    assert "# snow-qa: Session=plasmax11" in body
    assert "# snow-qa: Relogin=false" in body
    assert "User=keep" in body          # another section stays untouched
    assert (root / "etc/sddm.conf").stat().st_mode & 0o777 == 0o640
    assert "etc/sddm.conf.d/zz-snow-qa-autologin.conf" in created_paths(root)


def test_login_setup_never_creates_an_sddm_main_configuration(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert run_setup(tmp_path, root, stub_tools(tmp_path), dm="sddm.service").returncode == 0
    assert not (root / "etc/sddm.conf").exists()


def test_guest_login_autologin_is_false_when_sddm_main_config_conflicts(qa, tmp_path, monkeypatch):
    credentials, root, _ = login_guest(qa, tmp_path, monkeypatch, dm="sddm")
    (root / "etc/sddm.conf").write_text("[Autologin]\nUser=other\n")
    record = login_payload(qa, qa.login(credentials))
    assert record["display_manager"] == "sddm" and record["autologin"] is False


def test_guest_login_autologin_ignores_a_commented_sddm_main_config(qa, tmp_path, monkeypatch):
    credentials, root, _ = login_guest(qa, tmp_path, monkeypatch, dm="sddm")
    (root / "etc/sddm.conf").write_text(
        "[Autologin]\n# snow-qa: User=other\n[General]\nUser=keep\n")
    record = login_payload(qa, qa.login(credentials))
    assert record["autologin"] is True
