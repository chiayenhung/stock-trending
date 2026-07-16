from __future__ import annotations

from copy import deepcopy

from stocktrend.email_rendering import (
    html_email_qa,
    ranked_research_views,
    render_analysis_email,
)


def _signal(symbol: str, assessment: str, probability: float) -> dict:
    direction = "up" if assessment == "positive_trend" else "down"
    return {
        "symbol": symbol,
        "venue": "XNAS",
        "assessment": assessment,
        "signal_strength_score": 7.0,
        "confidence_bucket": "medium",
        "thesis": "Evidence-linked thesis for %s." % symbol,
        "monitoring_triggers": ["Reassess the cited evidence."],
        "validation_status": "pass",
        "producer": {"analyst_output_ids": ["analysis_%s" % symbol]},
        "horizon_outlooks": [
            {
                "horizon_key": key,
                "horizon_sessions": sessions,
                "direction": direction,
                "estimated_probability_pct": probability - offset,
                "probability_basis": "model_estimate_uncalibrated",
                "supporting_claim_ids": ["claim_%s" % symbol],
                "limitations": ["Uncalibrated model estimate."],
            }
            for key, sessions, offset in (
                ("short_5d", 5, 0),
                ("medium_1m", 21, 2),
                ("cycle_3m", 63, 4),
            )
        ],
    }


def test_ranking_caps_opportunities_at_five_and_keeps_all_warnings() -> None:
    positive = [
        _signal("P%d" % index, "positive_trend", 55.0 + index)
        for index in range(6)
    ]
    negative = [_signal("WARN", "negative_trend", 68.0)]
    for index, item in enumerate(positive + negative):
        item["signal_strength_score"] = float(index + 1)
    opportunities, warnings = ranked_research_views(positive + negative)
    assert len(opportunities) == 5
    assert opportunities[0]["signal"]["symbol"] == "P5"
    assert [item["signal"]["symbol"] for item in warnings] == ["WARN"]


def test_html_email_escapes_model_text_and_excludes_unvalidated_signals() -> None:
    included = _signal("SAFE", "positive_trend", 62.0)
    included["thesis"] = "Momentum <script>alert(1)</script> remains evidence-linked."
    excluded = deepcopy(included)
    excluded["symbol"] = "BLOCKED"
    excluded["validation_status"] = "unavailable"
    excluded["producer"] = {"analyst_output_ids": ["analysis_BLOCKED"]}
    document = render_analysis_email(
        "batch-1",
        "run-1",
        {
            "identity": {"exchange_session_date": "2026-07-16"},
            "degraded_reasons": ["INDEPENDENT_VALIDATOR_UNAVAILABLE"],
        },
        {"summary": "Point-in-time market context."},
        [included, excluded],
        {"profile": "test"},
        {},
    )
    html_email_qa(
        document,
        ("Top 5 Research Opportunities", "Downside Risk Warnings"),
    )
    assert "SAFE" in document
    assert "BLOCKED" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert "<script" not in document.lower()
    assert "1 signals were excluded from rankings" in document
