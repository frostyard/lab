"""The make gate rejects an empty Node test glob (ADR-0010: no vacuous success)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_target(tmp_path, target):
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    return subprocess.run(["make", "-C", str(tmp_path), target], capture_output=True, text=True)


@pytest.mark.parametrize("target, glob", [("verify-template", "test/*.test.mjs"),
                                          ("verify-site", "site/test/*.test.mjs")])
def test_node_test_targets_fail_when_their_glob_matches_nothing(tmp_path, target, glob):
    (tmp_path / Path(glob).parent).mkdir(parents=True)
    result = run_target(tmp_path, target)
    assert result.returncode != 0
    assert f"FAIL: no files match {glob}" in result.stdout


@pytest.mark.parametrize("glob", ["test/*.test.mjs", "site/test/*.test.mjs"])
def test_the_repository_has_files_for_each_node_test_glob(glob):
    assert sorted(ROOT.glob(glob)), glob


def test_verify_runs_every_part_and_ci_runs_verify():
    makefile = (ROOT / "Makefile").read_text()
    assert "\nverify: verify-template verify-python verify-site\n" in makefile
    assert "\nci: verify\n" in makefile
