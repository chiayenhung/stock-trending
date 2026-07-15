from __future__ import annotations

import shutil
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    for directory in ("spec", "schemas", "prompts", "templates"):
        shutil.copytree(REPOSITORY_ROOT / directory, tmp_path / directory)
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    shutil.copyfile(
        REPOSITORY_ROOT / "tests" / "fixtures" / "demo_observations.json",
        tmp_path / "tests" / "fixtures" / "demo_observations.json",
    )
    return tmp_path
