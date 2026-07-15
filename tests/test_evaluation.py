from __future__ import annotations

from pathlib import Path

import pytest

from stocktrend.contracts import SchemaRegistry
from stocktrend.evaluation import calculate_outcome


def test_outcome_is_cost_aware(project_root: Path) -> None:
    registry = SchemaRegistry(project_root / "schemas")
    outcome = calculate_outcome(
        registry,
        signal_id="signal_test",
        horizon_sessions=1,
        observed_at="2026-07-16T20:00:00Z",
        decision_price=100.0,
        observation_price=105.0,
        fees_usd=0.5,
        slippage_usd=0.5,
        benchmark_return_pct=1.0,
    )
    assert outcome["gross_return_pct"] == pytest.approx(5.0)
    assert outcome["net_return_pct"] == pytest.approx(4.0)


def test_missing_outcome_is_explicit(project_root: Path) -> None:
    registry = SchemaRegistry(project_root / "schemas")
    outcome = calculate_outcome(
        registry,
        signal_id="signal_test",
        horizon_sessions=5,
        observed_at="2026-07-22T20:00:00Z",
        decision_price=None,
        observation_price=None,
    )
    assert outcome["net_return_pct"] is None
    assert outcome["missing_reason"] == "MISSING_PRICE_OBSERVATION"
