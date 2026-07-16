from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    for directory in ("spec", "schemas", "prompts", "templates"):
        shutil.copytree(REPOSITORY_ROOT / directory, tmp_path / directory)
    sources_path = tmp_path / "spec" / "sources.yaml"
    sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    for source_name in ("news", "industry", "social"):
        sources["sources"][source_name]["enabled"] = False
    sources_path.write_text(
        yaml.safe_dump(sources, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    shutil.copyfile(
        REPOSITORY_ROOT / "tests" / "fixtures" / "demo_observations.json",
        tmp_path / "tests" / "fixtures" / "demo_observations.json",
    )
    return tmp_path
