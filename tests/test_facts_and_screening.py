from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from stocktrend.config import ConfigBundle
from stocktrend.errors import ContractError
from stocktrend.facts import FactsBuilder
from stocktrend.screening import screen_candidates
from stocktrend.util import load_json


def test_point_in_time_builder_deduplicates_and_screens(project_root: Path) -> None:
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    observations = document["observations"] + [deepcopy(document["observations"][0])]
    block = FactsBuilder().build("run_test", observations, document["as_of"])
    assert len(block["facts"]) == 4
    config = ConfigBundle.load(project_root)
    candidates = screen_candidates(block, config.strategy)
    assert [candidate["symbol"] for candidate in candidates] == ["NVDA"]


def test_point_in_time_builder_rejects_future_observation(project_root: Path) -> None:
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    document["observations"][0]["observed_at"] = "2026-07-16T00:00:00Z"
    with pytest.raises(ContractError, match="future data"):
        FactsBuilder().build("run_test", document["observations"], document["as_of"])
