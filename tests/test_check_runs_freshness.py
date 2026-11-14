"""Tests for scripts/check_runs_freshness.py and its Pages job.

The check turns stale published run data into a red Pages run, so a stalled
publish-results CronWorkflow cannot freeze the dashboard unnoticed.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_runs_freshness.py"
PAGES = ROOT / ".github" / "workflows" / "pages.yml"

spec = importlib.util.spec_from_file_location("check_runs_freshness", MODULE_PATH)
freshness = importlib.util.module_from_spec(spec)
sys.modules["check_runs_freshness"] = freshness
spec.loader.exec_module(freshness)  # type: ignore[union-attr]

NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)


def write(tmp_path, data, name="runs.json"):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def snapshot(*runs):
    return {"generated": "2026-11-13T11:00:00Z", "lanes": [], "runs": list(runs)}


def test_fresh_data_passes(tmp_path, capsys):
    path = write(tmp_path, snapshot(
        {"name": "old", "started": "2026-11-13T01:00:00Z", "finished": "2026-11-13T01:05:00Z"},
        {"name": "new", "started": "2026-11-13T10:30:00Z", "finished": None},
    ))
    assert freshness.main([str(path)], now=NOW) == 0
    assert "fresh run data: the newest run is from 2026-11-13T10:30:00Z, 1.5 h ago (limit 6 h)" \
        in capsys.readouterr().out


def test_stale_data_fails(tmp_path, capsys):
    # The stall that went unseen: the newest run is 4 days old.
    path = write(tmp_path, snapshot(
        {"name": "publish-results-1794240000", "started": "2026-11-09T16:00:00Z", "finished": None},
    ))
    assert freshness.main([str(path)], now=NOW) == 1
    err = capsys.readouterr().err
    assert "stale run data: the newest run is from 2026-11-09T16:00:00Z, 92.0 h ago (limit 6 h)" in err
    assert "publish-results" in err


def test_finished_time_counts_and_limit_is_exclusive(tmp_path):
    path = write(tmp_path, snapshot(
        {"name": "long", "started": "2026-11-13T00:00:00Z", "finished": "2026-11-13T06:00:00Z"},
    ))
    assert freshness.main([str(path)], now=NOW) == 0  # exactly 6 h old
    assert freshness.main([str(path), "--max-age-hours", "5.9"], now=NOW) == 1


@pytest.mark.parametrize("data", [
    snapshot(),
    snapshot({"name": "no-times", "started": None, "finished": None}),
    snapshot({"name": "bad-time", "started": "yesterday"}, "not a run"),
    {"generated": "2026-11-13T11:00:00Z", "lanes": []},
    [],
], ids=["no-runs", "no-times", "bad-times", "no-runs-key", "not-object"])
def test_data_without_a_run_time_fails(tmp_path, capsys, data):
    assert freshness.main([str(write(tmp_path, data))], now=NOW) == 1
    assert "holds no run with a start or finish time" in capsys.readouterr().err


def test_missing_or_invalid_file_fails(tmp_path, capsys):
    assert freshness.main([str(tmp_path / "absent.json")], now=NOW) == 1
    assert "cannot read run data" in capsys.readouterr().err
    assert freshness.main([str(write(tmp_path, "not json"))], now=NOW) == 1
    assert "cannot read run data" in capsys.readouterr().err


def test_naive_time_reads_as_utc():
    assert freshness.parse_time("2026-11-13T10:00:00") == datetime(2026, 11, 13, 10, tzinfo=timezone.utc)


def test_max_age_must_be_positive(tmp_path):
    with pytest.raises(SystemExit) as error:
        freshness.main([str(write(tmp_path, snapshot())), "--max-age-hours", "0"], now=NOW)
    assert error.value.code == 2


def test_cli_uses_the_real_clock(tmp_path):
    path = write(tmp_path, snapshot({"name": "ancient", "started": "2020-01-01T00:00:00Z"}))
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "stale run data" in result.stderr


def test_pages_runs_the_freshness_job_on_the_branch_data_without_blocking_deploy():
    workflow = yaml.safe_load(PAGES.read_text())
    assert "scripts/check_runs_freshness.py" in workflow[True]["push"]["paths"]
    jobs = workflow["jobs"]
    job = jobs["freshness"]
    assert job["permissions"] == {"contents": "read"}
    runs = [step.get("run") for step in job["steps"]]
    # A temporary path with no fallback copy: a missing data/runs branch
    # leaves no file there, so the check fails instead of reading main's copy.
    fetch = runs.index('scripts/fetch-runs-data.sh "$RUNNER_TEMP/runs.json"')
    check = runs.index(
        'python3 scripts/check_runs_freshness.py "$RUNNER_TEMP/runs.json" --max-age-hours 6'
    )
    assert fetch < check
    assert jobs["deploy"]["needs"] == "build"
    assert "needs" not in job
