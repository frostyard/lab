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


UNSPECIFIED_RESULT = object()


def make_workflow(
    name="run-container-tests-abc123",
    template_ref="run-container-tests",
    phase="Succeeded",
    started="2024-01-01T00:00:00Z",
    finished="2024-01-01T00:05:00Z",
    trigger=None,
    result=UNSPECIFIED_RESULT,
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
            "nodes": (
                {"qa": {"outputs": {"parameters": [{"name": "result", "value": result}]}}}
                if result is not UNSPECIFIED_RESULT
                else {}
            ),
        },
    }


@pytest.mark.parametrize(
    ("name", "phase", "result", "lane_key", "outcome"),
    [
        ("image-poll-snow-latest-1", "Failed", "19 passed, 1 failed", "image-poll-snow-latest", "failed"),
        ("image-poll-floe-latest-2", "Succeeded", "14 passed, 6 skipped", "image-poll-floe-latest", "passed"),
        ("image-poll-floe-latest-3", "Succeeded", None, "image-poll-floe-latest", "not-run"),
        ("image-poll-snowfield-latest-4", "Running", None, "image-poll-snowfield-latest", "unknown"),
        ("image-poll-snow-latest-5", "Failed", None, "image-poll-snow-latest", "unknown"),
        ("image-poll-snow-latest-6", "Succeeded", "Execution failed before any scenario ran", "image-poll-snow-latest", "unknown"),
        ("image-poll-snow-latest-7", "Succeeded", "0 passed, 0 skipped", "image-poll-snow-latest", "unknown"),
        ("image-poll-snow-latest-8", "Failed", "Execution failed before any scenario ran", "image-poll-snow-latest", "unknown"),
        ("image-poll-floe-latest-9", "Succeeded", "6 skipped", "image-poll-floe-latest", "unknown"),
        ("image-poll-snow-latest-10", "Succeeded", "19 passed, 1 failed", "image-poll-snow-latest", "unknown"),
    ],
)
def test_product_poll_qa_requires_scenario_evidence_and_phase(name, phase, result, lane_key, outcome):
    # Catches treating a successful registry-only poll (or failed resolution) as QA.
    summary = collect_runs.summarize(make_workflow(name=name, template_ref="image-poller", phase=phase, result=result))
    assert summary["name"] == name
    assert summary["template"] == "image-poller"
    assert summary["result"] == result
    assert summary["laneKey"] == lane_key
    assert summary["qaOutcome"] == outcome


@pytest.mark.parametrize("phase", ["Failed", "Succeeded"])
def test_empty_behave_result_is_present_but_has_no_qa_evidence(phase):
    # The workflow template filters zero counts into an empty result parameter.
    # Neither a failed workflow nor a successful poll establishes a QA outcome.
    workflow = make_workflow(name="image-poll-snow-latest-empty", template_ref="image-poller", phase=phase, result="")
    assert workflow["status"]["nodes"]["qa"]["outputs"]["parameters"] == [
        {"name": "result", "value": ""}
    ]
    summary = collect_runs.summarize(workflow)
    assert summary["result"] == ""
    assert summary["qaOutcome"] == "unknown"


def test_workflow_fixture_distinguishes_omitted_result_from_explicit_value():
    # Even an explicit null must not be silently modeled as an absent parameter;
    # the empty-string case above exercises the actual zero-scenario output.
    assert make_workflow()["status"]["nodes"] == {}
    assert make_workflow(result=None)["status"]["nodes"]["qa"]["outputs"]["parameters"] == [
        {"name": "result", "value": None}
    ]


@pytest.mark.parametrize("name", ["other-image-poll-snow-latest-1", "image-poll-unknown-latest-1", "image-poll-snow-latestish-1"])
def test_only_known_product_prefixes_get_product_identity(name):
    # Catches substring matching or inventing new product lanes.
    summary = collect_runs.summarize(make_workflow(name=name, template_ref="image-poller", result="2 passed"))
    assert summary["laneKey"] == "image-poller"
    assert summary["qaOutcome"] == "unknown"


def test_non_product_success_is_not_image_qa():
    # Catches treating publisher and maintenance success as image QA.
    for template in ("orphan-pod-gc", "publish", "run-container-tests"):
        summary = collect_runs.summarize(make_workflow(template_ref=template))
        assert summary["laneKey"] == template
        assert summary["qaOutcome"] == "unknown"
    summary = collect_runs.summarize(make_workflow(name="image-poll-floe-latest-1", template_ref="publish", result="2 passed"))
    assert summary["laneKey"] == "publish"
    assert summary["qaOutcome"] == "unknown"


def test_rollup_keeps_product_failures_separate_and_requires_observed_pass():
    # Catches floe success masking snow failure, and registry-only success marking snow green.
    runs = [
        collect_runs.summarize(make_workflow(name="image-poll-snow-latest-3", template_ref="image-poller", phase="Failed", result="19 passed, 1 failed")),
        collect_runs.summarize(make_workflow(name="image-poll-floe-latest-2", template_ref="image-poller", result="14 passed, 6 skipped")),
        collect_runs.summarize(make_workflow(name="image-poll-snow-latest-1", template_ref="image-poller")),
        collect_runs.summarize(make_workflow(name="image-poll-floe-latest-1", template_ref="image-poller")),
    ]
    lanes = {lane["laneKey"]: lane for lane in collect_runs.rollup(runs)}
    assert set(lanes) == {"image-poll-snow-latest", "image-poll-floe-latest"}
    assert lanes["image-poll-snow-latest"]["latest"] is runs[0]
    assert lanes["image-poll-snow-latest"]["runs"] == 2
    assert lanes["image-poll-snow-latest"]["everGreen"] is False
    assert lanes["image-poll-floe-latest"]["latest"] is runs[1]
    assert lanes["image-poll-floe-latest"]["runs"] == 2
    assert lanes["image-poll-floe-latest"]["everGreen"] is True


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
        item for item in payload["lanes"] if item["laneKey"] == "run-container-tests"
    )
    assert lane["template"] == "run-container-tests"
    assert lane["latest"]["name"] == "latest-failure"
    assert lane["runs"] == 2
    assert lane["everGreen"] is True


def test_main_writes_distinct_product_lanes(tmp_path, monkeypatch):
    # Catches main bypassing the product-aware rollup despite correct summaries.
    workflows = [
        make_workflow(name="image-poll-floe-latest-2", template_ref="image-poller", result="14 passed, 6 skipped", started="2026-09-24T21:20:00Z"),
        make_workflow(name="image-poll-snow-latest-1", template_ref="image-poller", phase="Failed", result="19 passed, 1 failed", started="2026-09-24T21:00:00Z"),
    ]
    monkeypatch.setattr(collect_runs, "kubectl", lambda *args: json.dumps({"items": workflows}))
    output = tmp_path / "runs.json"
    monkeypatch.setattr(sys, "argv", ["collect_runs.py", str(output)])

    assert collect_runs.main() == 0
    lanes = {lane["laneKey"]: lane for lane in json.loads(output.read_text())["lanes"]}
    assert lanes["image-poll-floe-latest"]["everGreen"] is True
    assert lanes["image-poll-snow-latest"]["everGreen"] is False
    assert lanes["image-poll-snow-latest"]["latest"]["qaOutcome"] == "failed"
