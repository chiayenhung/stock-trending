"""Deterministic scripted model clients for offline demonstration and tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Tuple

from .providers import ScriptedClient
from .util import parse_datetime, sha256_json


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
                "prompt_version": "v1",
            },
        }

    def stock_analyst(payload: Dict[str, Any]) -> Dict[str, Any]:
        candidate = payload["candidate"]
        symbol = candidate["symbol"]
        quote_id = candidate["quote_fact_id"]
        metrics_id = candidate["metrics_fact_id"]
        price = float(candidate["price"])
        momentum = float(candidate["momentum_20d_pct"])
        signal = "enter_long" if momentum >= 3.0 else "watch"
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
        return {
            "schema_version": "1.0.0",
            "analyst_output_id": "analysis_%s" % prefix,
            "instrument_id": candidate["instrument_id"],
            "symbol": symbol,
            "score": min(10.0, 5.0 + momentum / 2.0),
            "recommended_signal": signal,
            "confidence_bucket": "medium",
            "thesis": "Positive price momentum and relative volume may support a short swing continuation.",
            "claims": claims,
            "entry_condition": "Enter only at or below the proposed maximum price while liquidity remains valid.",
            "maximum_entry_price": round(price * 1.01, 2),
            "stop_price": round(price * 0.94, 2),
            "target_price": round(price * 1.12, 2),
            "time_exit_sessions": 5,
            "limitations": ["Offline fixture; no current news or earnings calendar."],
            "producer": {
                "vendor": producer_vendor,
                "model": "scripted-producer",
                "prompt_version": "v1",
            },
        }

    def synthesizer(payload: Dict[str, Any]) -> Dict[str, Any]:
        decision_at = payload["as_of"]
        expires_at = (
            parse_datetime(decision_at) + timedelta(hours=4)
        ).isoformat().replace("+00:00", "Z")
        proposals: List[Dict[str, Any]] = []
        for analyst in payload["analyst_outputs"]:
            signal_id = "signal_%s" % sha256_json(
                {
                    "analysis": analyst["analyst_output_id"],
                    "decision_at": decision_at,
                }
            )[:20]
            proposals.append(
                {
                    "schema_version": "1.0.0",
                    "signal_id": signal_id,
                    "revision": 1,
                    "strategy_id": payload["strategy"]["strategy_id"],
                    "strategy_version": payload["strategy"]["strategy_version"],
                    "signal_type": analyst["recommended_signal"],
                    "instrument_id": analyst["instrument_id"],
                    "symbol": analyst["symbol"],
                    "venue": payload["candidate_venues"][analyst["symbol"]],
                    "decision_at": decision_at,
                    "expires_at": expires_at,
                    "holding_horizon_sessions": analyst["time_exit_sessions"],
                    "entry_condition": analyst["entry_condition"],
                    "maximum_entry_price": analyst["maximum_entry_price"],
                    "stop_price": analyst["stop_price"],
                    "target_price": analyst["target_price"],
                    "time_exit_sessions": analyst["time_exit_sessions"],
                    "thesis_invalidation": "Exit if the stop is reached or the cited momentum condition no longer holds.",
                    "evidence_claim_ids": [
                        claim["claim_id"] for claim in analyst["claims"]
                    ],
                    "confidence_bucket": analyst["confidence_bucket"],
                    "known_gaps": list(analyst["limitations"]),
                    "execution_eligible": False,
                    "eligibility_reasons": ["PENDING_VALIDATION"],
                    "producer": {
                        "vendor": producer_vendor,
                        "model": "scripted-producer",
                        "prompt_version": "v1",
                        "analyst_output_ids": [analyst["analyst_output_id"]],
                    },
                }
            )
        return {"proposals": proposals}

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
            "target_id": target["signal_id"],
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
