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
])
def test_both_probes_emit_fsverity_errors(source, which, journal, expected):
    script = _probes(source)[which]
    assert 'emit fsverity_errors "$(fsverity_errors || echo unknown)"' in script
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


@pytest.mark.parametrize("checks,failed", [
    (["booted=ok", "fsverity_errors=0"], False),
    (["booted=ok", "fsverity_errors=3"], True),
    (["booted=ok", "fsverity_errors=0", "fsverity_errors=3"], True),   # either channel saw errors
    (["booted=ok", "fsverity_errors=unknown"], False),                 # recorded, not judged
    (["booted=ok"], False),
])
def test_judge_fails_only_on_a_positive_count(source, tmp_path, checks, failed):
    block = source[source.index("# frostyard/snosi#1130: these objects are not compressed"):
                   source.index('printf \'%s\' "bootc installed and verified')]
    (tmp_path / "checks.txt").write_text("".join(c + "\n" for c in checks))
    script = "set -euo pipefail\n" + block.replace("/tmp/results", str(tmp_path)) + "\necho reached-pass"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    if failed:
        assert result.returncode == 1
        assert "reached-pass" not in result.stdout
        assert (tmp_path / "result-summary.txt").read_text().startswith("FAILED: installed system logged")
        assert "snosi#1130" in (tmp_path / "result-summary.txt").read_text()
    else:
        assert result.returncode == 0, result.stderr
        assert "reached-pass" in result.stdout
