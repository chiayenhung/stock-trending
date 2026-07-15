"""Point-in-time, cost-aware outcome calculations."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from .contracts import SchemaRegistry


def calculate_outcome(
    registry: SchemaRegistry,
    signal_id: str,
    horizon_sessions: int,
    observed_at: str,
    decision_price: Optional[float],
    observation_price: Optional[float],
    fees_usd: float = 0.0,
    slippage_usd: float = 0.0,
    benchmark_return_pct: Optional[float] = None,
    missing_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if decision_price is None or observation_price is None:
        gross_return = None
        net_return = None
        if not missing_reason:
            missing_reason = "MISSING_PRICE_OBSERVATION"
    else:
        gross_return = (float(observation_price) / float(decision_price) - 1.0) * 100.0
        cost_pct = (float(fees_usd) + float(slippage_usd)) / float(decision_price) * 100.0
        net_return = gross_return - cost_pct
    outcome = {
        "schema_version": "1.0.0",
        "outcome_id": "outcome_%s" % uuid.uuid4().hex,
        "signal_id": signal_id,
        "horizon_sessions": horizon_sessions,
        "decision_price": decision_price,
        "observation_price": observation_price,
        "gross_return_pct": gross_return,
        "net_return_pct": net_return,
        "fees_usd": fees_usd,
        "slippage_usd": slippage_usd,
        "benchmark_return_pct": benchmark_return_pct,
        "observed_at": observed_at,
        "missing_reason": missing_reason,
    }
    registry.validate("outcome", outcome)
    return outcome
