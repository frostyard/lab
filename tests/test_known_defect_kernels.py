"""frostyard/snosi#1130: the known-defect kernel list stays the same in both lanes."""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "argo/workflow-templates"

KNOWN_DEFECT_LIST = 'known_defect_kernels="7.2.11+deb14-amd64 7.2.12+deb14-amd64 7.2.13+deb14-amd64 7.2.14+deb14-amd64 7.2.15+deb14-amd64 7.3.3+deb14-amd64 7.3.4+deb14-amd64 7.2.15-1-surface"'
UPSTREAM_FIX = "254e15591ee4"   # kdave/linux next-fixes; dcf8811f63af before a rebase


def test_known_defect_list_is_identical_in_every_lane():
    for name in ("run-firn-install-tests.yaml", "snow-bootc-lifecycle-scripts.yaml"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        lists = [line.strip().removeprefix("local ") for line in text.splitlines()
                 if "known_defect_kernels=" in line]
        assert lists == [KNOWN_DEFECT_LIST], name
        assert UPSTREAM_FIX in text, name
        assert "dcf8811f63af" not in text, name   # the unreachable pre-rebase hash


# The documented evidence has to back every listed kernel, and the lifecycle
# template trades its per-entry comment lines for a pointer to it.
QUALITY_DOC = "docs/quality.md"
EVIDENCE_POINTER = "The source evidence for each entry is in lab docs/quality.md"


def test_every_known_defect_kernel_is_evidenced_in_quality_md():
    root = Path(__file__).resolve().parents[1]
    words = KNOWN_DEFECT_LIST.split('"')[1].split()
    assert "7.3.4+deb14-amd64" in words
    assert "7.3.3+deb14-amd64" in words
    quality = (root / QUALITY_DOC).read_text(encoding="utf-8")
    for kernel in words:
        assert f"`{kernel}`" in quality, kernel
    lifecycle = (TEMPLATES / "snow-bootc-lifecycle-scripts.yaml").read_text(encoding="utf-8")
    assert EVIDENCE_POINTER in lifecycle
