"""Guard .github/dependabot.yml against core ADR-0089 and ADR-0075.

ADR-0089: each `updates` entry has a schedule with only `interval: monthly`,
each `package-ecosystem` appears in exactly 1 entry, and its group has no
`group-by`. ADR-0075: each entry has exactly 1 group with `patterns: ["*"]`
and no `applies-to`, so security updates stay 1 pull request each.
"""

from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"


def _entries():
    config = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    assert config["version"] == 2
    entries = config["updates"]
    assert entries, "dependabot.yml has no updates entries"
    return entries


def test_each_schedule_is_only_monthly():
    for entry in _entries():
        assert entry["schedule"] == {"interval": "monthly"}, entry["package-ecosystem"]


def test_each_ecosystem_has_exactly_one_entry():
    counts = Counter(entry["package-ecosystem"] for entry in _entries())
    assert {eco: n for eco, n in counts.items() if n != 1} == {}


def test_each_entry_has_one_catch_all_group():
    for entry in _entries():
        groups = entry["groups"]
        assert len(groups) == 1, entry["package-ecosystem"]
        (group,) = groups.values()
        assert group == {"patterns": ["*"]}, entry["package-ecosystem"]


def test_npm_entry_covers_both_manifests():
    (npm,) = [e for e in _entries() if e["package-ecosystem"] == "npm"]
    assert "directory" not in npm
    assert sorted(npm["directories"]) == ["/", "/site"]
    for directory in npm["directories"]:
        assert (ROOT / directory.lstrip("/") / "package.json").is_file()
