"""Deterministic momentum, volume, liquidity, and price screen."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


def screen_candidates(
    facts_block: Dict[str, Any],
    strategy: Dict[str, Any],
    instrument_buckets: Optional[Mapping[str, str]] = None,
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
                    "industry_bucket": (instrument_buckets or {}).get(
                        quote["instrument_id"], "unclassified"
                    ),
                }
            )
    candidates.sort(key=_candidate_rank)
    selection = strategy.get("candidate_selection", {})
    maximum_per_bucket = int(selection.get("maximum_per_bucket", len(candidates) or 1))
    maximum_total = int(selection.get("maximum_total", len(candidates) or 1))
    bucket_counts: Dict[str, int] = {}
    selected = []
    for candidate in candidates:
        bucket = candidate["industry_bucket"]
        if bucket_counts.get(bucket, 0) >= maximum_per_bucket:
            continue
        selected.append(candidate)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    return sorted(selected, key=_candidate_rank)[:maximum_total]


def screening_coverage(
    source_coverage: Optional[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if source_coverage is None:
        return {
            "profile": "test_or_unspecified",
            "buckets": {
                "unclassified": {
                    "configured": 0,
                    "attempted": 0,
                    "valid": 0,
                    "passing_screen": len(candidates),
                }
            },
        }
    buckets = {}
    for bucket, counts in source_coverage["buckets"].items():
        buckets[bucket] = {
            "configured": counts["configured"],
            "attempted": counts["attempted"],
            "valid": counts["valid"],
            "passing_screen": sum(
                1 for candidate in candidates if candidate["industry_bucket"] == bucket
            ),
        }
    return {"profile": "production", "buckets": buckets}


def _candidate_rank(candidate: Dict[str, Any]) -> tuple:
    return (
        -float(candidate["momentum_20d_pct"]),
        -float(candidate["volume_ratio"]),
        -float(candidate["average_volume_20d"]),
        candidate["symbol"],
    )
