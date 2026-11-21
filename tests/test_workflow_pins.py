"""Repository-wide check of the action pins (core ADR-0021, ADR-0088, ADR-0090).

Every `uses:` value in .github/workflows (and .github/actions, when it exists)
that names an action or a reusable workflow outside this repository is an
action pin, `OWNER/REPO[/PATH]@SHA`. For each one this check asserts:

- the ref is a full 40-character lowercase hex commit SHA;
- the line carries a version comment, `# vX.Y.Z` or `# vX.Y` (a major-only
  `# vN` is not one). A frostyard/core reusable workflow pin is exempt,
  because core has no release tags: it needs only a comment;
- each OWNER/REPO has 1 SHA across all those files.

PyYAML drops comments, so the comment comes from the raw line. The `uses:`
values that PyYAML sees must equal the values read from the raw lines: a
`uses:` this check cannot read from 1 line (a flow mapping, a block scalar)
fails instead of passing over the pin.

The tests never name a real SHA: a Dependabot bump that keeps these
properties passes, and the owner reviews the upstream diff in the bump
pull request.
"""

from collections import Counter
from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PIN_DIRS = (ROOT / ".github" / "workflows", ROOT / ".github" / "actions")
PLACEHOLDER = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"

USES_LINE = re.compile(
    r"""^\s*(?:-\s+)?(?:uses|"uses"|'uses')\s*:\s+"""
    r"""(?P<value>"[^"]*"|'[^']*'|[^\s#"'][^\s]*)"""
    r"""(?:\s+#\s*(?P<comment>.*?))?\s*$"""
)
ACTION_PIN = re.compile(r"^(?P<action>[^@]+)@(?P<ref>[^@]+)$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^v\d+\.\d+(\.\d+)?$")
CORE_WORKFLOW = "frostyard/core/.github/workflows/"


def pin_files():
    files = []
    for directory in PIN_DIRS:
        if directory.is_dir():
            files += [
                path
                for path in sorted(directory.rglob("*"))
                if path.is_file() and path.suffix in (".yml", ".yaml")
            ]
    return files


def yaml_uses(node):
    """Every value of a `uses` key in the parsed document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                yield value
            else:
                yield from yaml_uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from yaml_uses(item)


def line_uses(text):
    """(line number, value, comment) of each `uses:` line, YAML comments left out."""
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        match = USES_LINE.match(line)
        if match:
            value = match["value"]
            if value[:1] in "\"'":
                value = value[1:-1]
            yield number, value, match["comment"]


def check_pins(files):
    """Return the errors for {name: text} of workflow and action files."""
    errors = []
    shas = {}
    for name, text in files.items():
        parsed = Counter(yaml_uses(yaml.safe_load(text)))
        lines = list(line_uses(text))
        read = Counter(value for _, value, _ in lines)
        for value in sorted((parsed - read).elements()):
            errors.append(f"{name}: uses: {value} is not on 1 line this check can read")
        for number, value, comment in lines:
            if value.startswith("./") or value.startswith("docker://"):
                continue
            where = f"{name}:{number}: {value}"
            match = ACTION_PIN.match(value)
            if not match or value.count("/") < 1:
                errors.append(f"{where}: not OWNER/REPO[/PATH]@SHA")
                continue
            action, ref = match["action"], match["ref"]
            if not COMMIT_SHA.match(ref):
                errors.append(f"{where}: ref {ref!r} is not a 40-hex commit SHA")
            if action.startswith(CORE_WORKFLOW):
                if not comment:
                    errors.append(f"{where}: core workflow pin has no comment")
            elif not comment or not VERSION_COMMENT.match(comment):
                errors.append(
                    f"{where}: comment {comment!r}, want # vX.Y.Z or # vX.Y"
                )
            repo = "/".join(action.split("/")[:2])
            shas.setdefault(repo, set()).add(ref)
    for repo, refs in sorted(shas.items()):
        if len(refs) != 1:
            errors.append(f"{repo} has {len(refs)} SHAs across the files, want 1: {sorted(refs)}")
    return errors


def test_the_pin_files_are_found():
    assert any(path.name == "claude-code-review.yml" for path in pin_files())


def test_every_action_pin_has_a_sha_a_version_comment_and_1_sha_per_repository():
    files = {str(path.relative_to(ROOT)): path.read_text(encoding="utf-8") for path in pin_files()}
    assert check_pins(files) == []


def step(value, comment=" # v1.2.3"):
    return f"jobs:\n  a:\n    runs-on: x\n    steps:\n      - uses: {value}{comment}\n"


GOOD = f"owner/repo@{PLACEHOLDER}"


@pytest.mark.parametrize(
    "text",
    [
        step(GOOD),
        step(GOOD, " # v1.5"),
        step(f"owner/repo/sub/path@{PLACEHOLDER}", " # v10.20.30"),
        step(f"'{GOOD}'"),
        step(f'"{GOOD}"'),
        step("./.github/actions/local", ""),
        step("docker://alpine@sha256:" + "0" * 64, ""),
        step(f"{CORE_WORKFLOW}pr-triage.yml@{PLACEHOLDER}", " # frostyard/core main, 2026-11-14"),
        f"jobs:\n  a:\n    uses: {CORE_WORKFLOW}x.yml@{PLACEHOLDER} # core main\n",
        step(GOOD) + "      # - uses: owner/repo@v1\n",
        step(GOOD).replace("\n", "\r\n"),
    ],
    ids=[
        "vX.Y.Z",
        "vX.Y",
        "path",
        "single-quoted",
        "double-quoted",
        "local",
        "docker",
        "core-step",
        "core-job",
        "yaml-comment",
        "crlf",
    ],
)
def test_self_test_a_good_pin_passes(text):
    assert check_pins({"w.yml": text}) == []


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (step("owner/repo@v1.2.3"), "not a 40-hex commit SHA"),
        (step(f"owner/repo@{PLACEHOLDER[:7]}"), "not a 40-hex commit SHA"),
        (step(f"owner/repo@{PLACEHOLDER.upper()}"), "not a 40-hex commit SHA"),
        (step("owner/repo"), "not OWNER/REPO[/PATH]@SHA"),
        (step(GOOD, ""), "comment None"),
        (step(GOOD, " # v2"), "comment 'v2'"),
        (step(GOOD, " # 1.2.3"), "comment '1.2.3'"),
        (step(GOOD, " # v1.2.3-rc1"), "comment 'v1.2.3-rc1'"),
        (step(GOOD, " # main 2026-11-09"), "comment 'main 2026-11-09'"),
        (step(f"{CORE_WORKFLOW}pr-triage.yml@{PLACEHOLDER}", ""), "core workflow pin has no comment"),
        (f"jobs:\n  a:\n    steps: [{{uses: {GOOD}}}]\n", "is not on 1 line this check can read"),
        (f"jobs:\n  a:\n    steps:\n      - uses: >-\n          {GOOD}\n", "is not on 1 line"),
    ],
    ids=[
        "tag",
        "short-sha",
        "upper-hex",
        "no-ref",
        "no-comment",
        "major-only",
        "no-v",
        "pre-release",
        "branch-comment",
        "core-no-comment",
        "flow-mapping",
        "block-scalar",
    ],
)
def test_self_test_a_bad_pin_fails(text, error):
    errors = check_pins({"w.yml": text})
    assert any(error in message for message in errors), errors


@pytest.mark.parametrize(
    "files",
    [
        {"a.yml": step(GOOD), "b.yml": step(f"owner/repo@{OTHER}")},
        {"a.yml": step(GOOD) + f"      - uses: owner/repo@{OTHER} # v1.2.4\n"},
        {"a.yml": step(f"owner/repo/one@{PLACEHOLDER}"), "b.yml": step(f"owner/repo/two@{OTHER}")},
        {
            "a.yml": step(f"{CORE_WORKFLOW}x.yml@{PLACEHOLDER}", " # core"),
            "b.yml": step(f"{CORE_WORKFLOW}y.yml@{OTHER}", " # core"),
        },
    ],
    ids=["2-files", "1-file", "2-paths-of-1-repository", "core"],
)
def test_self_test_2_shas_for_1_repository_fail(files):
    errors = check_pins(files)
    assert any("has 2 SHAs across the files" in message for message in errors), errors


def test_self_test_1_sha_in_many_places_passes():
    files = {
        "a.yml": step(GOOD) + f"      - uses: owner/repo/sub@{PLACEHOLDER} # v1.2.3\n",
        "b.yml": step(GOOD),
        "c.yml": step(f"other/repo@{OTHER}"),
    }
    assert check_pins(files) == []
