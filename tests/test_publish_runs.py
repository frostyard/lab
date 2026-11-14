"""Contract tests for the run-data publication path.

The publish-results CronWorkflow pushes runs.json to the orphan branch
`data/runs` with scripts/publish-runs.sh, because the default branch accepts
changes only through a pull request with the required check. The Pages
workflow loads the data back with scripts/fetch-runs-data.sh. These tests are
the guard that the job never pushes to main again.

Everything is offline: each behaviour test uses a bare `file://` remote under
tmp_path, and GIT_ALLOW_PROTOCOL=file blocks every network transport.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "scripts/publish-runs.sh"
FETCH = ROOT / "scripts/fetch-runs-data.sh"
MANIFEST = ROOT / "manifests/publish-results.yaml"
PAGES = ROOT / ".github/workflows/pages.yml"
DATA_REF = "refs/heads/data/runs"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def payload(generated="2026-11-14T10:00:00Z", runs=None):
    return {
        "generated": generated,
        "lanes": [{"laneKey": "smoke"}],
        "runs": runs if runs is not None else [{"name": "smoke-abc", "phase": "Succeeded"}],
    }


def code_lines(path):
    """The non-comment lines of a shell script."""
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]


# ── static contract ──────────────────────────────────────────────────────────


def publish_source():
    for document in yaml.safe_load_all(MANIFEST.read_text()):
        if document and document.get("kind") == "CronWorkflow" \
                and document["metadata"]["name"] == "publish-results":
            spec = document["spec"]["workflowSpec"]
            template = next(t for t in spec["templates"] if t["name"] == spec["entrypoint"])
            return template["script"]["source"]
    raise AssertionError("CronWorkflow publish-results not found")


def test_manifest_never_pushes_to_main_and_runs_the_publish_script():
    source = publish_source()
    assert "HEAD:main" not in source
    assert not re.search(r"\borigin\s+main\b", source), source
    assert "git push" not in source, "the manifest must leave every push to scripts/publish-runs.sh"
    assert "exec bash scripts/publish-runs.sh" in source


def test_manifest_keeps_its_guards():
    source = publish_source()
    assert 'if [[ -z "${GITHUB_TOKEN:-}" ]]' in source
    assert "sha256sum -c -" in source
    workflow = next(d for d in yaml.safe_load_all(MANIFEST.read_text()) if d)
    assert workflow["spec"]["workflowSpec"]["activeDeadlineSeconds"] == 600
    assert workflow["spec"]["workflowSpec"]["podGC"] == {"strategy": "OnPodCompletion"}


def test_publish_script_pushes_only_to_the_data_branch():
    text = PUBLISH.read_text()
    assert re.search(r"^readonly DATA_BRANCH=data/runs$", text, re.M)
    assert "DATA_BRANCH:-" not in text and "DATA_BRANCH:=" not in text, "the branch must not be overridable"
    lines = code_lines(PUBLISH)
    pushes = [line for line in lines if re.search(r"\bpush\b", line) and "git" in line]
    assert len(pushes) == 1, pushes
    assert '"HEAD:refs/heads/${DATA_BRANCH}"' in pushes[0]
    code = "\n".join(lines)
    assert "--force" not in code
    assert not re.search(r"\bpush\b[^\n]*\s-f\b", code)
    assert not re.search(r"""["'\s]\+(HEAD|refs/)""", code), "no forced (+) refspec"
    assert not re.search(r"\bmain\b", code), "the script must not name main"


def test_publish_and_fetch_scripts_are_executable():
    assert os.access(PUBLISH, os.X_OK)
    assert os.access(FETCH, os.X_OK)


def test_pages_builds_on_a_schedule_with_the_fetched_data():
    workflow = yaml.safe_load(PAGES.read_text())
    triggers = workflow[True]  # PyYAML reads the bare `on:` key as True.
    assert {"cron": "15,45 * * * *"} in triggers["schedule"]
    assert "workflow_dispatch" in triggers
    assert triggers["push"]["branches"] == ["main"]
    assert "scripts/fetch-runs-data.sh" in triggers["push"]["paths"]
    steps = workflow["jobs"]["build"]["steps"]
    runs = [step.get("run") for step in steps]
    fetch = runs.index("scripts/fetch-runs-data.sh")
    checkout = next(i for i, step in enumerate(steps) if "actions/checkout" in step.get("uses", ""))
    assert steps[checkout]["with"]["persist-credentials"] is False
    assert checkout < fetch < runs.index("npm test") < runs.index("npm run build")


# ── behaviour (offline git) ──────────────────────────────────────────────────


@pytest.fixture
def git_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "LANG": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_TERMINAL_PROMPT": "0",
    }


def git(env, *args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                          text=True, check=check)


class Lab:
    """A bare `file://` remote with a `main` branch, a clone and a fake collector."""

    def __init__(self, tmp_path, env):
        self.tmp = tmp_path
        self.env = dict(env)
        self.bare = tmp_path / "remote.git"
        git(env, "init", "-q", "--bare", "-b", "main", str(self.bare))
        seed = tmp_path / "seed"
        git(env, "init", "-q", "-b", "main", str(seed))
        (seed / "README.md").write_text("lab\n")
        git(env, "add", "README.md", cwd=seed)
        git(env, "commit", "-q", "-m", "init", cwd=seed)
        git(env, "push", "-q", self.url, "HEAD:refs/heads/main", cwd=seed)
        self.clone = tmp_path / "clone"
        git(env, "clone", "-q", self.url, str(self.clone))
        self.data = tmp_path / "payload.json"
        self.collector = tmp_path / "collector.sh"
        self.collector.write_text(f'#!/usr/bin/env bash\ncp "{self.data}" "$1"\n')
        self.collector.chmod(0o755)
        self.env["RUNS_COLLECTOR"] = str(self.collector)

    @property
    def url(self):
        return self.bare.as_uri()

    def rev(self, ref):
        result = git(self.env, "--git-dir", str(self.bare), "rev-parse", "--verify", "-q", ref,
                     check=False)
        return result.stdout.strip() or None

    def show(self, ref, path):
        return git(self.env, "--git-dir", str(self.bare), "show", f"{ref}:{path}").stdout

    def publish(self, data):
        self.data.write_text(json.dumps(data, indent=2) + "\n")
        # The pod sets no identity variables; the script sets its own identity.
        env = {key: value for key, value in self.env.items()
               if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
        return subprocess.run(["bash", str(PUBLISH)], cwd=self.clone, env=env,
                              capture_output=True, text=True, check=False)

    def hook_rejects(self, count):
        """Make the remote reject the next `count` pushes."""
        left = self.tmp / "rejections-left"
        left.write_text(f"{count}\n")
        hook = self.bare / "hooks/pre-receive"
        hook.write_text(
            "#!/usr/bin/env bash\n"
            f'n=$(cat "{left}")\n'
            "if (( n > 0 )); then\n"
            f'  echo $((n - 1)) > "{left}"\n'
            '  echo "rejected by the test hook" >&2\n'
            "  exit 1\n"
            "fi\n"
        )
        hook.chmod(0o755)

    def put_data_branch(self, content):
        """Commit `content` as runs.json on an orphan data/runs (test setup only)."""
        work = self.tmp / f"put-{len(list(self.tmp.glob('put-*')))}"
        git(self.env, "init", "-q", str(work))
        (work / "runs.json").write_text(content)
        git(self.env, "add", "runs.json", cwd=work)
        git(self.env, "commit", "-q", "-m", "data", cwd=work)
        git(self.env, "push", "-q", "--force", self.url, f"HEAD:{DATA_REF}", cwd=work)
        return git(self.env, "rev-parse", "HEAD", cwd=work).stdout.strip()


@pytest.fixture
def lab(tmp_path, git_env):
    return Lab(tmp_path, git_env)


@needs_git
def test_publish_creates_then_updates_the_data_branch_and_never_touches_main(lab):
    main = lab.rev("refs/heads/main")

    first = lab.publish(payload())
    assert first.returncode == 0, first.stderr
    assert "Published to data/runs." in first.stdout
    tip = lab.rev(DATA_REF)
    assert tip
    tree = git(lab.env, "--git-dir", str(lab.bare), "ls-tree", "--name-only", tip).stdout.split()
    assert tree == ["runs.json"]
    parents = git(lab.env, "--git-dir", str(lab.bare), "rev-list", "--parents", "-n1", tip).stdout.split()
    assert parents == [tip], "the first data commit must be an orphan root"
    assert json.loads(lab.show(tip, "runs.json")) == payload()
    message = git(lab.env, "--git-dir", str(lab.bare), "log", "-1", "--format=%an <%ae>%n%B", tip).stdout
    assert message.startswith("frostyard-lab[bot] <lab@frostyard.invalid>\nchore(report): refresh run data")
    assert lab.rev("refs/heads/main") == main

    same = lab.publish(payload())
    assert same.returncode == 0, same.stderr
    assert "No change in run data; skipping commit." in same.stdout
    assert lab.rev(DATA_REF) == tip

    stamp = lab.publish(payload(generated="2026-11-14T10:30:00Z"))
    assert stamp.returncode == 0, stamp.stderr
    assert "Only the generation timestamp changed; skipping commit." in stamp.stdout
    assert lab.rev(DATA_REF) == tip

    changed = payload(generated="2026-11-14T11:00:00Z", runs=[{"name": "smoke-def", "phase": "Failed"}])
    third = lab.publish(changed)
    assert third.returncode == 0, third.stderr
    child = lab.rev(DATA_REF)
    assert child != tip
    parents = git(lab.env, "--git-dir", str(lab.bare), "rev-list", "--parents", "-n1", child).stdout.split()
    assert parents == [child, tip]
    assert json.loads(lab.show(child, "runs.json")) == changed
    assert lab.rev("refs/heads/main") == main
    assert git(lab.env, "--git-dir", str(lab.bare), "for-each-ref", "--format=%(refname)").stdout.split() \
        == ["refs/heads/data/runs", "refs/heads/main"]


@needs_git
def test_publish_regenerates_when_another_run_creates_the_branch_first(lab):
    # The collector runs between ls-remote and push: on its first call it
    # creates data/runs, so the orphan push is a rejected non-fast-forward.
    marker = lab.tmp / "raced"
    racer = lab.tmp / "racer"
    git(lab.env, "init", "-q", str(racer))
    (racer / "runs.json").write_text(json.dumps(payload(runs=[])) + "\n")
    git(lab.env, "add", "runs.json", cwd=racer)
    git(lab.env, "commit", "-q", "-m", "other run", cwd=racer)
    other = git(lab.env, "rev-parse", "HEAD", cwd=racer).stdout.strip()
    lab.collector.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ ! -e "{marker}" ]]; then\n'
        f'  touch "{marker}"\n'
        f'  git -C "{racer}" push -q "{lab.url}" HEAD:{DATA_REF}\n'
        "fi\n"
        f'cp "{lab.data}" "$1"\n'
    )

    result = lab.publish(payload())
    assert result.returncode == 0, result.stderr
    assert "regenerating on top of the new tip" in result.stderr
    tip = lab.rev(DATA_REF)
    parents = git(lab.env, "--git-dir", str(lab.bare), "rev-list", "--parents", "-n1", tip).stdout.split()
    assert parents == [tip, other]
    assert json.loads(lab.show(tip, "runs.json")) == payload()


@needs_git
def test_publish_retries_a_rejected_push_without_stacking_commits(lab):
    assert lab.publish(payload()).returncode == 0
    base = lab.rev(DATA_REF)
    lab.hook_rejects(1)

    result = lab.publish(payload(runs=[{"name": "smoke-xyz", "phase": "Succeeded"}]))
    assert result.returncode == 0, result.stderr
    assert "push rejected (data/runs moved)" in result.stderr
    tip = lab.rev(DATA_REF)
    parents = git(lab.env, "--git-dir", str(lab.bare), "rev-list", "--parents", "-n1", tip).stdout.split()
    assert parents == [tip, base]


@needs_git
def test_publish_gives_up_after_three_rejections(lab):
    main = lab.rev("refs/heads/main")
    lab.hook_rejects(3)

    result = lab.publish(payload())
    assert result.returncode == 1
    assert "push rejected 3x; leaving it for the next cycle" in result.stderr
    assert lab.rev(DATA_REF) is None
    assert lab.rev("refs/heads/main") == main


@needs_git
def test_publish_fails_on_an_ls_remote_error_without_a_push(lab):
    missing = (lab.tmp / "missing.git").as_uri()
    git(lab.env, "remote", "set-url", "origin", missing, cwd=lab.clone)
    calls = lab.tmp / "collector-called"
    lab.collector.write_text(f'#!/usr/bin/env bash\ntouch "{calls}"\ncp "{lab.data}" "$1"\n')

    result = lab.publish(payload())
    assert result.returncode == 1
    assert "ERROR: git ls-remote for data/runs failed" in result.stderr
    assert not calls.exists()
    assert lab.rev(DATA_REF) is None


@needs_git
def test_publish_never_prints_the_remote_credentials(lab):
    # GIT_ALLOW_PROTOCOL=file refuses https, so this fails before any network.
    secret = "ghp_testSECRETtoken"
    git(lab.env, "remote", "set-url", "origin",
        f"https://x-access-token:{secret}@github.invalid/frostyard/lab.git", cwd=lab.clone)

    result = lab.publish(payload())
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr



@needs_git
def test_publish_masks_credentials_in_a_git_error(lab):
    # A git stub on PATH fails `ls-remote` with the credential URL in its
    # stderr, as git does for an https error, so the masking itself is tested.
    secret = "ghp_testSECRETtoken"
    url = f"https://x-access-token:{secret}@github.invalid/frostyard/lab.git"
    git(lab.env, "remote", "set-url", "origin", url, cwd=lab.clone)
    stub_dir = lab.tmp / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "git"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == ls-remote ]]; then\n'
        f"    echo \"fatal: unable to access '{url}/': Could not resolve host\" >&2\n"
        "    exit 128\n"
        "  fi\n"
        "done\n"
        f'exec {shutil.which("git")} "$@"\n'
    )
    stub.chmod(0o755)
    lab.env["PATH"] = f"{stub_dir}{os.pathsep}{lab.env['PATH']}"

    result = lab.publish(payload())
    assert result.returncode == 1
    assert "fatal: unable to access 'https://***@github.invalid/frostyard/lab.git/'" in result.stderr
    assert secret not in result.stdout + result.stderr

def run_fetch(lab, out, remote=None):
    env = dict(lab.env, LAB_DATA_REMOTE=remote or lab.url)
    return subprocess.run(["bash", str(FETCH), str(out)], cwd=lab.clone, env=env,
                          capture_output=True, text=True, check=False)


@pytest.fixture
def fallback(tmp_path):
    out = tmp_path / "site/src/data/runs.json"
    out.parent.mkdir(parents=True)
    out.write_text('{"generated": "fallback", "lanes": [], "runs": []}\n')
    return out


@needs_git
def test_fetch_keeps_the_fallback_when_the_branch_is_missing(lab, fallback):
    before = fallback.read_text()
    result = run_fetch(lab, fallback)
    assert result.returncode == 0, result.stderr
    assert "data/runs branch not found; keeping the committed fallback" in result.stderr
    assert fallback.read_text() == before


@needs_git
def test_fetch_loads_the_branch_data_without_touching_the_caller_repo(lab, fallback):
    content = json.dumps(payload(), indent=2) + "\n"
    sha = lab.put_data_branch(content)

    result = run_fetch(lab, fallback)
    assert result.returncode == 0, result.stderr
    assert fallback.read_text() == content
    assert sha in result.stdout and "2026-11-14T10:00:00Z" in result.stdout
    assert not (lab.clone / ".git/FETCH_HEAD").exists()
    assert not (lab.clone / ".git/shallow").exists()
    assert list(fallback.parent.iterdir()) == [fallback], "no staging file is left behind"


@needs_git
@pytest.mark.parametrize("content", [
    "not json\n",
    "[]\n",
    json.dumps({"generated": "x", "lanes": []}) + "\n",
    json.dumps({"generated": 1, "lanes": [], "runs": []}) + "\n",
], ids=["not-json", "not-object", "no-runs", "generated-not-string"])
def test_fetch_rejects_invalid_branch_data(lab, fallback, content):
    before = fallback.read_text()
    lab.put_data_branch(content)

    result = run_fetch(lab, fallback)
    assert result.returncode == 1
    assert "invalid runs.json" in result.stderr
    assert fallback.read_text() == before
    assert list(fallback.parent.iterdir()) == [fallback]


@needs_git
def test_fetch_fails_when_the_remote_is_unreachable(lab, fallback):
    before = fallback.read_text()
    result = run_fetch(lab, fallback, remote=(lab.tmp / "missing.git").as_uri())
    assert result.returncode == 1
    assert "ERROR: git ls-remote for data/runs failed" in result.stderr
    assert fallback.read_text() == before
