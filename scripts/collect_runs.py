#!/usr/bin/env python3
"""Collect Argo Workflow results from the cluster into the site's data file.

Runs in-cluster (see manifests/publish-results.yaml) and reads the Kubernetes
API rather than being wired into each lane. That means a lane never has to know
the reporting exists, and a lane added tomorrow shows up here without a code
change — the cost is that anything not expressed as a Workflow is invisible.

Output contract is site/src/data/runs.json; the Astro site reads nothing else.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Argo keeps failed workflows 30 days and successful ones 7 (see
# workflow-controller-configmap), so this window is bounded by retention, not
# by choice. Cap the count so one busy day cannot blow up the page.
MAX_RUNS = 200

# Maps the WorkflowTemplate a run came from to how it should be described.
# Anything unmatched still appears, labelled by its raw template name.
LANE_KINDS = {
    "snosi-qa-pipeline": ("container", "Container smoke suites"),
    "run-container-tests": ("container", "Container smoke suites"),
    "image-poller": ("poll", "Registry digest poll"),
    "run-incus-vm-tests": ("vm", "ISO boot (Secure Boot)"),
    "run-incus-disk-tests": ("vm", "Published A/B disk artifact"),
    "run-incus-install-tests": ("install", "Native A/B installer"),
    "run-incus-bootc-install-tests": ("install", "bootc installer (mechanics)"),
    "run-firn-install-tests": ("install", "firn installer matrix"),
    "orphan-pod-gc": ("maintenance", "Orphan pod GC"),
}

PRODUCT_POLLS = tuple(f"image-poll-{product}-latest" for product in ("snow", "floe", "snowfield"))
SCENARIO_COUNTS = re.compile(r"\d+ (?:passed|failed|skipped|undefined)(?:, \d+ (?:passed|failed|skipped|undefined))*")


def kubectl(*args: str) -> str:
    result = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def output_param(node: dict, name: str) -> str | None:
    for param in (node.get("outputs") or {}).get("parameters") or []:
        if param.get("name") == name:
            return param.get("value")
    return None


def summarize(workflow: dict) -> dict:
    meta = workflow.get("metadata", {})
    status = workflow.get("status", {})
    spec = workflow.get("spec", {})

    # Identifying the lane takes two passes because runs reach their work two
    # different ways. The pollers and the container pipeline set
    # workflowTemplateRef on the spec; the VM lanes are a one-step DAG whose
    # node carries templateRef. Reading only templateName labels every VM lane
    # "main" — the DAG's entrypoint — which is useless.
    template = (spec.get("workflowTemplateRef") or {}).get("name") or ""
    result = checks = None
    fallback = ""

    for node in (status.get("nodes") or {}).values():
        ref = (node.get("templateRef") or {}).get("name")
        if ref:
            template = template or ref
        else:
            fallback = fallback or node.get("templateName") or ""
        result = result or output_param(node, "result")
        checks = checks or output_param(node, "checks")

    if not template:
        # Inline-template workflows (orphan-pod-gc) have no ref at all. Their
        # CronWorkflow name is the stable identity; the generated run name is
        # that plus a timestamp suffix.
        name = meta.get("name") or ""
        for known in LANE_KINDS:
            if name.startswith(known):
                template = known
                break
        else:
            template = fallback

    kind, label = LANE_KINDS.get(template, ("other", template or "unknown"))
    lane_key = template or label
    qa_outcome = "unknown"
    if template == "image-poller":
        name = meta.get("name") or ""
        lane_key = next((key for key in PRODUCT_POLLS if name.startswith(key + "-")), lane_key)
        if lane_key in PRODUCT_POLLS:
            # The output is written by Behave's scenario summarizer, not by
            # the digest poll. A successful poll with no counts proves no QA.
            counts = result if isinstance(result, str) and SCENARIO_COUNTS.fullmatch(result) else None
            scenario_counts = {status: int(n) for n, status in re.findall(
                r"(\d+) (passed|failed|skipped|undefined)", counts or ""
            )}
            observed = any(scenario_counts.values())
            if observed and status.get("phase") == "Failed":
                qa_outcome = "failed"
            elif (observed and status.get("phase") == "Succeeded"
                  and scenario_counts.get("passed", 0) > 0
                  and not scenario_counts.get("failed", 0)
                  and not scenario_counts.get("undefined", 0)):
                qa_outcome = "passed"
            elif status.get("phase") == "Succeeded" and result is None:
                qa_outcome = "not-run"

    started, finished = status.get("startedAt"), status.get("finishedAt")
    duration = None
    if started and finished:
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            duration = int(
                (
                    datetime.strptime(finished, fmt) - datetime.strptime(started, fmt)
                ).total_seconds()
            )
        except ValueError:
            duration = None

    return {
        "name": meta.get("name"),
        "phase": status.get("phase", "Unknown"),
        "started": started,
        "finished": finished,
        "durationSeconds": duration,
        "template": template,
        "laneKey": lane_key,
        "qaOutcome": qa_outcome,
        "kind": kind,
        "label": label,
        "trigger": (meta.get("labels") or {}).get("snosi.io/trigger", "scheduled"),
        "result": result,
        # checks arrive as newline-separated key=value pairs from the VM lanes
        "checks": [c for c in (checks or "").splitlines() if "=" in c],
    }


def rollup(runs: list[dict]) -> list[dict]:
    """Group newest-first runs by lane, preserving distinct product QA evidence."""
    lanes: dict[str, dict] = {}
    for run in runs:
        key = run["laneKey"]
        green = (run["qaOutcome"] == "passed" if key in PRODUCT_POLLS
                 else run["phase"] == "Succeeded")
        if key in lanes:
            lanes[key]["runs"] += 1
            lanes[key]["everGreen"] |= green
            continue
        lanes[key] = {
            "laneKey": key,
            "template": run["template"],
            "label": run["label"],
            "kind": run["kind"],
            "latest": run,
            "runs": 1,
            # A lane that has never once succeeded is not reporting a finding
            # about the thing under test — it is reporting that nobody has shown
            # the lane can pass. Two false bug reports against snosi came from
            # reading a never-green lane's red as evidence (see docs/roadmap.md).
            # The site renders this as `unproven` rather than `Failed`.
            "everGreen": green,
        }
    return sorted(lanes.values(), key=lambda item: (item["kind"], item["label"]))


def main() -> int:
    out_path = Path(sys.argv[1] if len(sys.argv) > 1 else "site/src/data/runs.json")

    raw = json.loads(kubectl("get", "workflows", "-n", "argo", "-o", "json"))
    runs = [summarize(w) for w in raw.get("items", [])]
    # Newest first; runs without a start time sort last rather than crashing.
    runs.sort(key=lambda r: r["started"] or "", reverse=True)
    runs = runs[:MAX_RUNS]
    lanes = rollup(runs)

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lanes": lanes,
        "runs": runs,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} — {len(runs)} runs across {len(lanes)} lanes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
