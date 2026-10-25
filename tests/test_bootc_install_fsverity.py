"""frostyard/snosi#1130: the bootc install lane records and judges fs-verity errors."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "argo/workflow-templates/run-incus-bootc-install-tests.yaml"


@pytest.fixture
def source():
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    return template["spec"]["templates"][0]["script"]["source"]


def _probes(source):
    """The console fallback script (quoted heredoc) and the incus agent probe."""
    console = source.split("CHECK_SH=$(cat <<'SCRIPT'\n", 1)[1].split("\nSCRIPT", 1)[0]
    agent = source.split("bash -s <<'PROBE'", 1)[1].split("\n        PROBE", 1)[0]
    return console, agent


def _functions_and_emit(script):
    start = script.index("fsverity_errors() {")
    end = script.index('emit fsverity_errors "$(fsverity_errors || echo unknown)"')
    return script[start:end]


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("journal,expected", [
    ("kernel: boot\n", "0"),
    ("kernel: fs-verity (dm-0, inode 1): FILE CORRUPTED! pos=0, level=-1\n"
     "kernel: fsverity_msg: 3 callbacks suppressed\n", "4"),
    (None, "unknown"),
    ("", "unknown"),
    # journalctl exits 0 and prints this when it read no entry at all.
    ("-- No entries --\n", "unknown"),
    ("-- No entries --", "unknown"),
    ("\n  -- No entries --  \n\n", "unknown"),
])
def test_both_probes_emit_fsverity_errors(source, which, journal, expected):
    script = _probes(source)[which]
    assert 'emit fsverity_errors "$(fsverity_errors || echo unknown)"' in script
    assert "journalctl -q -k -b --no-pager" in script
    body = _functions_and_emit(script)
    stub = ('timeout() { shift; "$@"; }\n'
            + ('journalctl() { return 1; }\n' if journal is None else 'journalctl() { printf "%s" "$JOURNAL"; }\n'))
    result = subprocess.run(["bash", "-c", stub + body + '\nfsverity_errors || echo unknown'],
                            env={**os.environ, "JOURNAL": journal or ""}, capture_output=True, text=True, check=False)
    assert result.stdout.strip() == expected
    # Kernel logs are only read: never cleared, rotated or filtered.
    assert "dmesg" not in script and "--rotate" not in script and "--vacuum" not in script


def test_underscore_check_keys_survive_both_channels(source, tmp_path):
    # `fsverity_errors` has an underscore: the old [a-z]+ patterns silently dropped it.
    assert "[a-z]+=" not in source
    collect = source[source.index("{ cat /tmp/results/agent-checks.txt"):source.index("if [[ ! -s /tmp/results/checks.txt ]]")]
    script = ('set -euo pipefail\nM_CHECK=SNOSI_QA__CHECK\nmkdir -p "$R"\n'
              'printf "booted=ok\\nfsverity_errors=0\\n" > "$R/agent-checks.txt"\n'
              'console_log() { printf "%s\\n" "SNOSI_QA__CHECK fsverity_errors=2" "SNOSI_QA__CHECK rootfs=btrfs"; }\n'
              + collect.replace("/tmp/results", "$R") + '\ncat "$R/checks.txt"')
    result = subprocess.run(["bash", "-c", script], env={**os.environ, "R": str(tmp_path / "results")},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["booted=ok", "fsverity_errors=0", "fsverity_errors=2", "rootfs=btrfs"]


GUARD_MISSING = "FAILED: fs-verity guard did not report (snosi#1130)"
KNOWN = "7.2.11+deb14-amd64"
OTHER = "7.3.1+deb14-amd64"
AMBIGUOUS = "FAILED: installed system logged 2 fs-verity corruption message(s) and the kernel identity is ambiguous: fsverity_kernel_ambiguous"


@pytest.mark.parametrize("checks,failed,message", [
    (["booted=ok", "fsverity_errors=0", f"kernel={OTHER}"], False, None),
    (["booted=ok", "fsverity_errors=0", f"kernel={KNOWN}"], False, None),
    (["booted=ok", "fsverity_errors=3", f"kernel={OTHER}"], True,
     f"FAILED: installed system logged 3 fs-verity corruption message(s) on kernel {OTHER}"),
    (["booted=ok", "fsverity_errors=0", "fsverity_errors=3", f"kernel={OTHER}"], True,
     "FAILED: installed system logged 3 fs-verity corruption"),    # either channel saw errors
    (["booted=ok", "fsverity_errors=unknown", "fsverity_errors=0", f"kernel={OTHER}"], False, None),
    (["booted=ok", "fsverity_errors=unknown", "fsverity_errors=2", f"kernel={OTHER}"], True,
     "FAILED: installed system logged 2 fs-verity corruption"),
    # Known upstream defect kernels: recorded with a note, not failed.
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN}"], False, None),
    (["booted=ok", "fsverity_errors=1", "kernel=7.2.12+deb14-amd64"], False, None),
    (["booted=ok", "fsverity_errors=0", "fsverity_errors=4", f"kernel={KNOWN}"], False, None),
    # Another kernel, a look-alike, an unknown or a missing kernel with errors fails.
    (["booted=ok", "fsverity_errors=2", "kernel=7.2.13+deb14-amd64"], True, "FAILED: installed system logged 2"),
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN}-extra"], True, "FAILED: installed system logged 2"),
    (["booted=ok", "fsverity_errors=2", "kernel=unknown"], True, "FAILED: installed system logged 2"),
    (["booted=ok", "fsverity_errors=2"], True, "FAILED: installed system logged 2 fs-verity corruption message(s) and the kernel identity is ambiguous: fsverity_kernel_ambiguous"),
    # Both channels can hold different kernel lines: the exemption needs exactly 1 distinct,
    # well-formed value (identical duplicates are fine).
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN}", f"kernel={KNOWN}"], False, None),
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN}", f"kernel={OTHER}"], True, AMBIGUOUS),
    (["booted=ok", "fsverity_errors=2", f"kernel={OTHER}", f"kernel={KNOWN}"], True, AMBIGUOUS),
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN}", "kernel=7.2.12+deb14-amd64"], True, AMBIGUOUS),
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN} 7.2.12+deb14-amd64"], True, AMBIGUOUS),  # both entries in one value
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN} junk"], True, AMBIGUOUS),
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN} "], True, AMBIGUOUS),
    (["booted=ok", "fsverity_errors=2", "kernel="], True, AMBIGUOUS),
    (["booted=ok", "fsverity_errors=2", "kernel=" + "a" * 65], True, AMBIGUOUS),
    (["booted=ok", "fsverity_errors=2", f"kernel={KNOWN};x"], True, AMBIGUOUS),
    # Without errors the kernel identity is not consulted.
    (["booted=ok", "fsverity_errors=0", f"kernel={KNOWN}", f"kernel={OTHER}"], False, None),
    # The guard must have reported: no valid number from any channel fails the lane,
    # on a known-defect kernel too.
    (["booted=ok", "fsverity_errors=unknown", f"kernel={OTHER}"], True, GUARD_MISSING),
    (["booted=ok", "fsverity_errors=unknown", f"kernel={KNOWN}"], True, GUARD_MISSING),
    (["booted=ok", "fsverity_errors=unknown", "fsverity_errors=-1", "fsverity_errors=x1"], True, GUARD_MISSING),
    (["booted=ok", "fsverity_errors="], True, GUARD_MISSING),
    (["booted=ok", f"kernel={KNOWN}"], True, GUARD_MISSING),
    (["booted=ok"], True, GUARD_MISSING),
])
def test_judge_requires_a_numeric_count_and_judges_it_by_kernel(source, tmp_path, checks, failed, message):
    block = source[source.index("# frostyard/snosi#1130: the guard must have run"):
                   source.index('printf \'%s\' "bootc installed and verified')]
    # The guarded part of the summary line, as the script builds it.
    tail = source[source.index('printf \'%s\' "bootc installed and verified'):]
    tail = tail[:tail.index("cat /tmp/results/result-summary.txt")]
    (tmp_path / "checks.txt").write_text("".join(c + "\n" for c in checks))
    script = ("set -euo pipefail\nSECUREBOOT=true\nIMAGE=snow\n" + (block + tail).replace("/tmp/results", str(tmp_path))
              + "\necho reached-pass")
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    summary_file = tmp_path / "result-summary.txt"
    if failed:
        assert result.returncode == 1
        assert "reached-pass" not in result.stdout
        summary = summary_file.read_text()
        assert summary.startswith(message)
        assert "snosi#1130" in summary
        assert "fsverity_known_defect" not in summary
    else:
        assert result.returncode == 0, result.stderr
        assert "reached-pass" in result.stdout
        known_errors = any(c.startswith("fsverity_errors=") and c[16:].isdigit() and int(c[16:]) > 0 for c in checks)
        kernel = next((c[7:] for c in checks if c.startswith("kernel=")), None)
        if known_errors and kernel in ("7.2.11+deb14-amd64", "7.2.12+deb14-amd64"):
            assert (tmp_path / "checks.txt").read_text().splitlines().count(f"fsverity_known_defect={kernel}") == 1
            # Visible note in the summary, the checks file and the log.
            assert summary_file.read_text() == f"bootc installed and verified: snow (secureboot=true) fsverity_known_defect={kernel}"
            assert f"fsverity_known_defect={kernel}" in (tmp_path / "checks.txt").read_text().splitlines()
            assert "known-defect kernel" in result.stderr
        else:
            assert summary_file.read_text() == "bootc installed and verified: snow (secureboot=true)"
            assert "fsverity_known_defect" not in (tmp_path / "checks.txt").read_text()


def test_known_defect_list_matches_the_other_lanes(source):
    assert source.count('known_defect_kernels="7.2.11+deb14-amd64 7.2.12+deb14-amd64"') == 1
    for probe in _probes(source):
        assert 'emit kernel "$(uname -r)"' in probe
