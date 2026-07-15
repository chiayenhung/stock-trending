"""Deterministic momentum, volume, liquidity, and price screen."""

from __future__ import annotations

from typing import Any, Dict, List


def screen_candidates(
    facts_block: Dict[str, Any],
    strategy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for fact in facts_block["facts"]:
        grouped.setdefault(fact["symbol"], {})[fact["fact_type"]] = fact
    thresholds = strategy["screen"]
    candidates: List[Dict[str, Any]] = []
    for symbol, facts in sorted(grouped.items()):
        quote = facts.get("quote")
        metrics = facts.get("bar_metrics")
        if quote is None or metrics is None:
            continue
        price = float(quote["value"].get("price", 0))
        average_volume = float(metrics["value"].get("average_volume_20d", 0))
        volume_ratio = float(metrics["value"].get("volume_ratio", 0))
        momentum = float(metrics["value"].get("momentum_20d_pct", 0))
        if (
            price >= float(thresholds["minimum_price_usd"])
            and average_volume >= float(thresholds["minimum_average_volume"])
            and volume_ratio >= float(thresholds["minimum_volume_ratio"])
            and momentum >= float(thresholds["minimum_momentum_20d_pct"])
        ):
            candidates.append(
                {
                    "instrument_id": quote["instrument_id"],
                    "symbol": symbol,
                    "venue": quote["venue"],
                    "quote_fact_id": quote["fact_id"],
                    "metrics_fact_id": metrics["fact_id"],
                    "price": price,
                    "average_volume_20d": average_volume,
                    "volume_ratio": volume_ratio,
                    "momentum_20d_pct": momentum,
                }
            )
    return candidates
