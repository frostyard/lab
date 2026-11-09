from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW_FILES = sorted(
    list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")),
    key=lambda path: path.name,
)


def load_workflow(path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_workflow_file_list_is_not_empty():
    assert WORKFLOW_FILES


@pytest.mark.parametrize("workflow_file", WORKFLOW_FILES, ids=lambda path: path.name)
def test_workflow_top_level_permissions_are_read_only(workflow_file):
    workflow = load_workflow(workflow_file)

    assert "permissions" in workflow, (
        f"{workflow_file.name}: bad key 'permissions': key must exist"
    )

    permissions = workflow["permissions"]
    if permissions == "read-all":
        return
    if permissions == {}:
        return

    assert isinstance(permissions, dict), (
        f"{workflow_file.name}: bad key 'permissions': "
        f"value must be {{}}, read-all, or a read-only mapping; got {permissions!r}"
    )
    for key, value in permissions.items():
        assert value in ("read", "none"), (
            f"{workflow_file.name}: bad key '{key}': "
            f"top-level value must be 'read' or 'none', got {value!r}"
        )


def test_pages_job_permissions_are_exact():
    workflow = load_workflow(WORKFLOWS / "pages.yml")

    assert workflow["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "pages": "read",
    }
    assert workflow["jobs"]["deploy"]["permissions"] == {
        "pages": "write",
        "id-token": "write",
    }


def test_labeler_job_permissions_are_exact():
    workflow = load_workflow(WORKFLOWS / "labeler.yml")

    assert workflow["jobs"]["triage"]["permissions"] == {
        "contents": "read",
        "issues": "write",
    }


def test_job_write_permissions_are_allowlisted():
    allowed_writes = {
        ("claude-code-review.yml", "review", "pull-requests"),
        ("risk-paths.yml", "pr-triage", "pull-requests"),
        ("pages.yml", "deploy", "pages"),
        ("pages.yml", "deploy", "id-token"),
        ("labeler.yml", "triage", "issues"),
    }

    for workflow_file in WORKFLOW_FILES:
        workflow = load_workflow(workflow_file)
        for job_name, job in (workflow.get("jobs") or {}).items():
            permissions = job.get("permissions") or {}
            if isinstance(permissions, str):
                assert permissions in ("read-all", "none"), (
                    f"{workflow_file.name}: bad job grant "
                    f"'{job_name}: {permissions}' is not allowlisted"
                )
                continue
            for key, value in permissions.items():
                if value in ("write", "write-all"):
                    assert (
                        workflow_file.name,
                        job_name,
                        key,
                    ) in allowed_writes, (
                        f"{workflow_file.name}: bad job write grant "
                        f"'{job_name}.{key}: {value}' is not allowlisted"
                    )


def test_ci_aggregate_job_covers_every_other_job():
    # core ADR-0076 (rule N5): 1 required check covers the whole CI workflow.
    # The job must wait for every other ci.yml job, run whatever their result,
    # and keep 1 stable name, or the required check can pass while a job failed.
    workflow = load_workflow(WORKFLOWS / "ci.yml")
    jobs = workflow["jobs"]
    result = jobs.get("result")
    assert result is not None, "ci.yml needs the aggregate job `result`"
    assert result["name"] == "CI result", result.get("name")
    assert result["if"] == "always()", result.get("if")
    others = set(jobs) - {"result"}
    assert set(result["needs"]) == others, sorted(others ^ set(result["needs"]))
