from __future__ import annotations

from copy import deepcopy

from stocktrend.validation import deterministic_research_signal_errors


def _signal() -> dict:
    return {
        "research_only": True,
        "strategy_id": "strategy",
        "strategy_version": "1",
        "assessment": "positive_trend",
        "horizon_sessions": 5,
        "instrument_id": "instrument",
        "evidence_claim_ids": ["quote_claim", "metrics_claim"],
        "horizon_outlooks": [
            {
                "horizon_key": key,
                "horizon_sessions": sessions,
                "supporting_claim_ids": ["quote_claim", "metrics_claim"],
            }
            for key, sessions in (
                ("short_5d", 5),
                ("medium_1m", 21),
                ("cycle_3m", 63),
            )
        ],
    }


def _inputs() -> tuple:
    claims = [
        {"claim_id": "quote_claim", "supporting_fact_ids": ["quote_fact"]},
        {"claim_id": "metrics_claim", "supporting_fact_ids": ["metrics_fact"]},
    ]
    facts = {
        "facts": [
            {
                "fact_id": "quote_fact",
                "fact_type": "quote",
                "instrument_id": "instrument",
            },
            {
                "fact_id": "metrics_fact",
                "fact_type": "bar_metrics",
                "instrument_id": "instrument",
            },
        ]
    }
    strategy = {
        "strategy_id": "strategy",
        "strategy_version": "1",
        "assessments": ["positive_trend"],
        "research_horizon_sessions": {"minimum": 1, "maximum": 5},
        "outlook_horizons": {
            "short_5d": 5,
            "medium_1m": 21,
            "cycle_3m": 63,
        },
        "required_fact_types": {
            "positive_trend": ["quote", "bar_metrics"]
        },
    }
    return claims, facts, strategy


def test_complete_evidence_linked_outlooks_pass_deterministic_validation() -> None:
    claims, facts, strategy = _inputs()
    assert deterministic_research_signal_errors(
        _signal(), claims, facts, strategy
    ) == []


def test_missing_mismatched_and_uncited_outlooks_fail_closed() -> None:
    claims, facts, strategy = _inputs()
    signal = deepcopy(_signal())
    signal["horizon_outlooks"].pop()
    signal["horizon_outlooks"][0]["horizon_sessions"] = 21
    signal["horizon_outlooks"][1]["supporting_claim_ids"] = ["unknown_claim"]
    errors = deterministic_research_signal_errors(signal, claims, facts, strategy)
    assert "OUTLOOK_HORIZONS_INCOMPLETE" in errors
    assert "OUTLOOK_HORIZON_SESSION_MISMATCH:short_5d" in errors
    assert "OUTLOOK_CLAIM_NOT_CITED:unknown_claim" in errors
