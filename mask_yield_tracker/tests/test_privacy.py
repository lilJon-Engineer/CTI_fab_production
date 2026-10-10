"""Guard rails: reticle data and local outputs must never be tracked by git."""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FORBIDDEN_SUFFIXES = (".gds", ".gds2", ".gdsii", ".oas", ".oasis")


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


pytestmark = pytest.mark.skipif(_git("rev-parse").returncode != 0, reason="not a git checkout")


def test_no_layout_files_tracked():
    tracked = _git("ls-files").stdout.splitlines()
    assert not [f for f in tracked if f.lower().endswith(FORBIDDEN_SUFFIXES)]
    assert not [f for f in tracked if f.startswith("mask_yield_tracker/local/") and not f.endswith("README.txt")]


@pytest.mark.parametrize("path", [
    "mask_yield_tracker/local/reticle.gds",
    "mask_yield_tracker/local/extracted/device_table.json",
    "mask_yield_tracker/local/my.local.toml",
    "somewhere/mask.OAS",
    "a/b.oasis",
    "x/batches/lot1.json",
    "x/extracted/devices.csv",
    "x/reports/r.html",
    "cfg/real.local.toml",
])
def test_ignored(path):
    assert _git("check-ignore", "-q", path).returncode == 0, path
