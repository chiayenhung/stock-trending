"""Deterministic scripted model clients for offline demonstration and tests."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .providers import ScriptedClient
from .util import sha256_json


def create_demo_clients(
    producer_vendor: str = "openai",
    validator_vendor: str = "anthropic",
) -> Tuple[ScriptedClient, ScriptedClient]:
    def market_context(payload: Dict[str, Any]) -> Dict[str, Any]:
        fact_ids = [
            fact["fact_id"]
            for fact in payload["facts_block"]["facts"]
            if fact["fact_type"] == "bar_metrics"
        ]
        return {
            "schema_version": "1.0.0",
            "cycle_stage": "transition",
            "direction_bias": "neutral",
            "summary": "Screened momentum is positive, while the fixture contains no broad-market regime evidence.",
            "claim_fact_ids": fact_ids,
            "limitations": ["Demo fixture does not include a broad-market index."],
            "producer": {
                "vendor": producer_vendor,
                "model": "scripted-producer",
                "prompt_version": "v2",
            },
        }

    def stock_analyst(payload: Dict[str, Any]) -> Dict[str, Any]:
        candidate = payload["candidate"]
        symbol = candidate["symbol"]
        quote_id = candidate["quote_fact_id"]
        metrics_id = candidate["metrics_fact_id"]
        momentum = float(candidate["momentum_20d_pct"])
        assessment = "positive_trend" if momentum >= 3.0 else "watch"
        prefix = sha256_json(
            {"symbol": symbol, "quote": quote_id, "metrics": metrics_id}
        )[:12]
        claims = [
            {
                "claim_id": "claim_%s_price" % prefix,
                "claim_type": "price_observation",
                "text": "%s screened at the cited point-in-time price." % symbol,
                "supporting_fact_ids": [quote_id],
                "limitations": [],
            },
            {
                "claim_id": "claim_%s_momentum" % prefix,
                "claim_type": "momentum_observation",
                "text": "%s has positive 20-session momentum and elevated volume in the cited metrics."
                % symbol,
                "supporting_fact_ids": [metrics_id],
                "limitations": [],
            },
        ]
        direction = "up" if assessment == "positive_trend" else "uncertain"
        probabilities = (64.0, 59.0, 54.0) if direction == "up" else (50.0, 50.0, 50.0)
        horizon_outlooks = [
            {
                "horizon_key": horizon_key,
                "horizon_sessions": sessions,
                "direction": direction,
                "estimated_probability_pct": probability,
                "probability_basis": "model_estimate_uncalibrated",
                "supporting_claim_ids": [claim["claim_id"] for claim in claims],
                "limitations": [
                    "Offline model estimate without historical calibration."
                ],
            }
            for horizon_key, sessions, probability in (
                ("short_5d", 5, probabilities[0]),
                ("medium_1m", 21, probabilities[1]),
                ("cycle_3m", 63, probabilities[2]),
            )
        ]
        return {
            "schema_version": "2.0.0",
            "analyst_output_id": "analysis_%s" % prefix,
            "instrument_id": candidate["instrument_id"],
            "symbol": symbol,
            "score": min(10.0, 5.0 + momentum / 2.0),
            "assessment": assessment,
            "confidence_bucket": "medium",
            "thesis": "Positive price momentum and relative volume warrant continued research monitoring.",
            "claims": claims,
            "horizon_sessions": 5,
            "horizon_outlooks": horizon_outlooks,
            "monitoring_triggers": [
                "Reassess if 20-session momentum falls below the screening threshold.",
                "Reassess if relative volume normalizes below the screening threshold.",
            ],
            "limitations": ["Offline fixture; no current news or earnings calendar."],
            "producer": {
                "vendor": producer_vendor,
                "model": "scripted-producer",
                "prompt_version": "v2",
            },
        }

    def synthesizer(payload: Dict[str, Any]) -> Dict[str, Any]:
        decision_at = payload["as_of"]
        signals: List[Dict[str, Any]] = []
        for analyst in payload["analyst_outputs"]:
            signal_id = "research_%s" % sha256_json(
                {
                    "analysis": analyst["analyst_output_id"],
                    "decision_at": decision_at,
                }
            )[:20]
            signals.append(
                {
                    "schema_version": "2.0.0",
                    "research_signal_id": signal_id,
                    "revision": 1,
                    "strategy_id": payload["strategy"]["strategy_id"],
                    "strategy_version": payload["strategy"]["strategy_version"],
                    "assessment": analyst["assessment"],
                    "signal_strength_score": analyst["score"],
                    "instrument_id": analyst["instrument_id"],
                    "symbol": analyst["symbol"],
                    "venue": payload["candidate_venues"][analyst["symbol"]],
                    "assessed_at": decision_at,
                    "horizon_sessions": analyst["horizon_sessions"],
                    "horizon_outlooks": analyst["horizon_outlooks"],
                    "thesis": analyst["thesis"],
                    "monitoring_triggers": analyst["monitoring_triggers"],
                    "evidence_claim_ids": [
                        claim["claim_id"] for claim in analyst["claims"]
                    ],
                    "confidence_bucket": analyst["confidence_bucket"],
                    "known_gaps": list(analyst["limitations"]),
                    "research_only": True,
                    "validation_status": "pending",
                    "validation_reason_codes": [],
                    "producer": {
                        "vendor": producer_vendor,
                        "model": "scripted-producer",
                        "prompt_version": "v2",
                        "analyst_output_ids": [analyst["analyst_output_id"]],
                    },
                }
            )
        return {"signals": signals}

    def semantic_validator(payload: Dict[str, Any]) -> Dict[str, Any]:
        target = payload["target"]
        evidence_ids = {fact["fact_id"] for fact in payload["evidence"]}
        supported: List[str] = []
        unsupported: List[str] = []
        for claim in payload["claims"]:
            if set(claim["supporting_fact_ids"]).issubset(evidence_ids):
                supported.append(claim["claim_id"])
            else:
                unsupported.append(claim["claim_id"])
        passed = not unsupported and set(target["evidence_claim_ids"]).issubset(
            set(supported)
        )
        return {
            "schema_version": "1.0.0",
            "target_id": target["research_signal_id"],
            "verdict": "pass" if passed else "reject",
            "supported_claim_ids": supported,
            "unsupported_claim_ids": unsupported,
            "reason_codes": [] if passed else ["EVIDENCE_PACKET_INCOMPLETE"],
            "summary": "All cited claims map to the supplied evidence."
            if passed
            else "One or more cited claims lack supplied evidence.",
        }

    producer = ScriptedClient(
        vendor_id=producer_vendor,
        model="scripted-producer",
        handlers={
            "market_context": market_context,
            "stock_analyst": stock_analyst,
            "synthesizer": synthesizer,
        },
    )
    validator = ScriptedClient(
        vendor_id=validator_vendor,
        model="scripted-validator",
        handlers={"semantic_validator": semantic_validator},
    )
    return producer, validator
