"""`just validate` checks the generated-name Workflows with `create`, not `apply`.

Issue #256: `argo/*.yaml` holds Workflows that carry `metadata.generateName`, so
`kubectl apply` on them always fails. The recipe validates those files with
`create --dry-run=server`; `apply` stays for the server-side-named files in
`manifests/`, `argo/workflow-templates/` and `argocd/`. The recipe also keeps
its stderr in a `mktemp` file instead of the shared fixed path.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUSTFILE = ROOT / "Justfile"

# A loop head: `for f in a b c; do`.
LOOP = re.compile(r"^\s*for\s+(\w+)\s+in\s+(.*?)(?:;?\s*do)?\s*$")


def recipe_body(name):
    """The indented lines of recipe `name`, with their leading indentation kept."""
    lines = JUSTFILE.read_text().splitlines()
    body = []
    inside = False
    for line in lines:
        if not inside:
            inside = line.strip() == f"{name}:"
            continue
        if line and not line[0].isspace():
            break
        body.append(line)
    assert body, f"recipe {name} has no body"
    return body


def loops(lines):
    """Each `for` loop as (words it iterates over, body lines)."""
    found = []
    for index, line in enumerate(lines):
        match = LOOP.match(line)
        if not match:
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for later in lines[index + 1:]:
            if later.strip() and len(later) - len(later.lstrip()) <= indent:
                break
            body.append(later)
        found.append((match.group(1), match.group(2).split(), body))
    return found


def test_validate_never_applies_the_generated_name_workflows():
    body = recipe_body("validate")
    text = "\n".join(body)

    # argo/*.yaml stays in the validation (deleting it would pass silently).
    assert "argo/*.yaml" in text
    # No single line pairs an apply with the generated-name glob.
    for line in body:
        assert not ("kubectl apply" in line and "argo/*.yaml" in line), line

    # The loop over argo/*.yaml runs `create`, never `apply`.
    argo_loops = [loop for loop in loops(body) if "argo/*.yaml" in loop[1]]
    assert argo_loops, "the validate recipe has no loop over argo/*.yaml"
    for _, _, loop_body in argo_loops:
        loop_text = "\n".join(loop_body)
        assert "apply" not in loop_text, loop_text
        assert "create" in loop_text, loop_text

    # A created dry run is really the verb used.
    assert "create --dry-run=server" in text or any(
        "create" in " ".join(loop[2]) for loop in argo_loops
    )


def test_validate_keeps_apply_for_the_other_folders():
    body = recipe_body("validate")
    text = "\n".join(body)

    for glob in ("manifests/*.yaml", "argo/workflow-templates/*.yaml", "argocd/*.yaml"):
        assert glob in text, glob
        loop_for_glob = [loop for loop in loops(body) if glob in loop[1]]
        assert loop_for_glob, f"no loop validates {glob}"
        for _, _, loop_body in loop_for_glob:
            loop_text = "\n".join(loop_body)
            assert "apply" in loop_text, loop_text
            assert "create" not in loop_text, loop_text


def test_justfile_uses_no_fixed_tmp_path():
    assert "/tmp/" not in JUSTFILE.read_text()

    text = "\n".join(recipe_body("validate"))
    assert "mktemp" in text
    assert re.search(r"trap\s+'rm -f \"\$?\w+\"'?\s+EXIT", text), text


def test_argo_root_files_are_generated_name_workflows():
    files = sorted((ROOT / "argo").glob("*.yaml"))
    assert files, "argo/ has no Workflow submit files"
    for file in files:
        lines = file.read_text().splitlines()
        assert any(line.strip().startswith("generateName:") for line in lines), file
        # No `name:` key directly under metadata (2 spaces of indentation).
        assert not any(
            re.match(r"^  name:", line) for line in lines
        ), f"{file.name} has a metadata.name, so `apply` would work on it"
