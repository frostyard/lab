"""Tests for scripts/collect_runs.py.

These exercise the pure summarization logic (no kubectl / cluster access
required) so they can run anywhere, including CI.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "collect_runs.py"

spec = importlib.util.spec_from_file_location("collect_runs", MODULE_PATH)
collect_runs = importlib.util.module_from_spec(spec)
sys.modules["collect_runs"] = collect_runs
spec.loader.exec_module(collect_runs)  # type: ignore[union-attr]


def make_workflow(
    name="run-container-tests-abc123",
    template_ref="run-container-tests",
    phase="Succeeded",
    started="2024-01-01T00:00:00Z",
    finished="2024-01-01T00:05:00Z",
    trigger=None,
):
    labels = {}
    if trigger is not None:
        labels["snosi.io/trigger"] = trigger
    return {
        "metadata": {"name": name, "labels": labels},
        "spec": {"workflowTemplateRef": {"name": template_ref}},
        "status": {
            "phase": phase,
            "startedAt": started,
            "finishedAt": finished,
            "nodes": {},
        },
    }


def test_summarize_known_template_maps_to_lane_kind():
    workflow = make_workflow()
    summary = collect_runs.summarize(workflow)

    assert summary["template"] == "run-container-tests"
    assert summary["kind"] == "container"
    assert summary["label"] == "Container smoke suites"
    assert summary["phase"] == "Succeeded"
    assert summary["durationSeconds"] == 300


def test_summarize_unknown_template_falls_back_to_other():
    workflow = make_workflow(template_ref="some-new-lane")
    summary = collect_runs.summarize(workflow)

    assert summary["kind"] == "other"
    assert summary["label"] == "some-new-lane"


def test_summarize_defaults_trigger_to_scheduled():
    workflow = make_workflow()
    summary = collect_runs.summarize(workflow)

    assert summary["trigger"] == "scheduled"


def test_summarize_respects_explicit_trigger_label():
    workflow = make_workflow(trigger="manual")
    summary = collect_runs.summarize(workflow)

    assert summary["trigger"] == "manual"


def test_summarize_handles_missing_timestamps():
    workflow = make_workflow(started=None, finished=None)
    summary = collect_runs.summarize(workflow)

    assert summary["durationSeconds"] is None


def test_summarize_inline_template_matches_by_name_prefix():
    workflow = {
        "metadata": {"name": "orphan-pod-gc-20240101", "labels": {}},
        "spec": {},
        "status": {"phase": "Succeeded", "nodes": {}},
    }

    summary = collect_runs.summarize(workflow)

    assert summary["template"] == "orphan-pod-gc"
    assert summary["kind"] == "maintenance"


def test_output_param_finds_named_parameter():
    node = {
        "outputs": {
            "parameters": [
                {"name": "result", "value": "pass"},
                {"name": "checks", "value": "a=1\nb=2"},
            ]
        }
    }

    assert collect_runs.output_param(node, "result") == "pass"
    assert collect_runs.output_param(node, "checks") == "a=1\nb=2"
    assert collect_runs.output_param(node, "missing") is None


def test_output_param_handles_missing_outputs():
    assert collect_runs.output_param({}, "result") is None


def test_kubectl_returns_stdout_and_surfaces_command_failure(monkeypatch):
    completed = subprocess.CompletedProcess(
        ["kubectl"], returncode=0, stdout='{"items": []}', stderr=""
    )
    monkeypatch.setattr(collect_runs.subprocess, "run", lambda *args, **kwargs: completed)

    assert collect_runs.kubectl("get", "workflows") == '{"items": []}'

    failed = subprocess.CompletedProcess(
        ["kubectl"], returncode=1, stdout="", stderr="cluster unavailable\n"
    )
    monkeypatch.setattr(collect_runs.subprocess, "run", lambda *args, **kwargs: failed)

    with pytest.raises(
        RuntimeError,
        match=r"kubectl get workflows failed: cluster unavailable",
    ):
        collect_runs.kubectl("get", "workflows")


def test_main_writes_capped_sorted_runs_and_lane_rollups(tmp_path, monkeypatch):
    workflows = [
        make_workflow(
            name="latest-failure",
            phase="Failed",
            started="2026-08-10T12:00:00Z",
            finished="2026-08-10T12:01:00Z",
        ),
        make_workflow(
            name="earlier-success",
            phase="Succeeded",
            started="2026-08-10T11:00:00Z",
            finished="2026-08-10T11:01:00Z",
        ),
    ]
    workflows.extend(
        make_workflow(
            name=f"other-{index}",
            template_ref=f"other-{index}",
            started=f"2026-08-09T{index // 60:02d}:{index % 60:02d}:00Z",
            finished=f"2026-08-09T{index // 60:02d}:{index % 60:02d}:30Z",
        )
        for index in range(collect_runs.MAX_RUNS)
    )
    monkeypatch.setattr(
        collect_runs,
        "kubectl",
        lambda *args: json.dumps({"items": workflows}),
    )
    output = tmp_path / "nested" / "runs.json"
    monkeypatch.setattr(sys, "argv", ["collect_runs.py", str(output)])

    assert collect_runs.main() == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["runs"]) == collect_runs.MAX_RUNS
    assert payload["runs"][0]["name"] == "latest-failure"
    lane = next(
        item for item in payload["lanes"] if item["template"] == "run-container-tests"
    )
    assert lane["latest"]["name"] == "latest-failure"
    assert lane["runs"] == 2
    assert lane["everGreen"] is True
