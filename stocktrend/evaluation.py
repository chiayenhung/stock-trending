"""Point-in-time evaluation for research trend assessments."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from .contracts import SchemaRegistry


def calculate_outcome(
    registry: SchemaRegistry,
    research_signal_id: str,
    assessment: str,
    horizon_sessions: int,
    observed_at: str,
    baseline_price: Optional[float],
    observation_price: Optional[float],
    benchmark_return_pct: Optional[float] = None,
    missing_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if baseline_price is None or observation_price is None:
        observed_return = None
        excess_return = None
        direction_correct = None
        if not missing_reason:
            missing_reason = "MISSING_PRICE_OBSERVATION"
    else:
        observed_return = (
            float(observation_price) / float(baseline_price) - 1.0
        ) * 100.0
        excess_return = (
            observed_return - float(benchmark_return_pct)
            if benchmark_return_pct is not None
            else None
        )
        if assessment == "positive_trend":
            direction_correct = observed_return > 0
        elif assessment == "negative_trend":
            direction_correct = observed_return < 0
        else:
            direction_correct = None
    outcome = {
        "schema_version": "2.0.0",
        "outcome_id": "outcome_%s" % uuid.uuid4().hex,
        "research_signal_id": research_signal_id,
        "assessment": assessment,
        "horizon_sessions": horizon_sessions,
        "baseline_price": baseline_price,
        "observation_price": observation_price,
        "observed_return_pct": observed_return,
        "benchmark_return_pct": benchmark_return_pct,
        "excess_return_pct": excess_return,
        "direction_correct": direction_correct,
        "observed_at": observed_at,
        "missing_reason": missing_reason,
    }
    registry.validate("outcome", outcome)
    return outcome
