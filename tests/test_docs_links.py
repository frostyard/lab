"""Relative Markdown links in the docs and the root files point to real paths.

A link that names a scheme (`https:`, `mailto:` and the like) or a bare anchor
(`#...`) leaves the repository, so only the in-tree relative links are checked.
Each one is resolved against the folder of the Markdown file that holds it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The link target in `[text](target)`, up to a space or the closing paren.
LINK = re.compile(r"\]\(([^)\s]+)\)")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
ROOT_FILES = ("README.md", "AGENTS.md", "CONTRIBUTING.md")


def markdown_files():
    files = [p for p in sorted((ROOT / "docs").rglob("*.md")) if p.name != "TEMPLATE.md"]
    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    return files


def is_external(target):
    return bool(SCHEME.match(target)) or target.startswith("#")


def dead_links(path, root=ROOT):
    """Return (checked, dead) for the relative links of 1 Markdown file.

    checked counts the in-tree links that were resolved; dead lists
    "file:line: target" for each one that is not on disk.
    """
    checked = 0
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for match in LINK.finditer(line):
            target = match.group(1)
            if is_external(target):
                continue
            target = target.split("#")[0]
            if not target:
                continue
            checked += 1
            if not (path.parent / target).exists():
                found.append(f"{path.relative_to(root)}:{number}: {target}")
    return checked, found


def test_no_dead_relative_links():
    files = markdown_files()
    # A guard against a broken glob silently passing the gate.
    assert any(p.parent.name == "docs" for p in files), "no docs/*.md files found"
    checked = 0
    dead = []
    for path in files:
        count, found = dead_links(path)
        checked += count
        dead.extend(found)
    # Zero checked links would pass vacuously (ADR-0010).
    assert checked > 100, f"only {checked} relative links checked in {len(files)} files"
    assert not dead, f"{len(dead)} of {checked} relative links are dead:\n" + "\n".join(dead)


def test_dead_link_is_reported(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "there.md").write_text("x\n", encoding="utf-8")
    doc = tmp_path / "docs" / "a.md"
    doc.write_text(
        "[ok](../there.md#part) [web](https://example.com/x) [anchor](#top)\n"
        "[gone](../argo/deleted.yaml)\n",
        encoding="utf-8",
    )
    checked, dead = dead_links(doc, root=tmp_path)
    assert checked == 2
    assert dead == ["docs/a.md:2: ../argo/deleted.yaml"]
