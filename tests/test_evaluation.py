from __future__ import annotations

from pathlib import Path

import pytest

from stocktrend.contracts import SchemaRegistry
from stocktrend.evaluation import calculate_outcome


def test_research_outcome_tracks_direction_and_benchmark(project_root: Path) -> None:
    registry = SchemaRegistry(project_root / "schemas")
    outcome = calculate_outcome(
        registry,
        research_signal_id="research_test",
        assessment="positive_trend",
        horizon_sessions=1,
        observed_at="2026-07-16T20:00:00Z",
        baseline_price=100.0,
        observation_price=105.0,
        benchmark_return_pct=1.0,
    )
    assert outcome["observed_return_pct"] == pytest.approx(5.0)
    assert outcome["excess_return_pct"] == pytest.approx(4.0)
    assert outcome["direction_correct"] is True


def test_missing_research_outcome_is_explicit(project_root: Path) -> None:
    registry = SchemaRegistry(project_root / "schemas")
    outcome = calculate_outcome(
        registry,
        research_signal_id="research_test",
        assessment="watch",
        horizon_sessions=5,
        observed_at="2026-07-22T20:00:00Z",
        baseline_price=None,
        observation_price=None,
    )
    assert outcome["observed_return_pct"] is None
    assert outcome["direction_correct"] is None
    assert outcome["missing_reason"] == "MISSING_PRICE_OBSERVATION"
