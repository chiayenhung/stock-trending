"""Safe, deterministic HTML rendering for completion email packages."""

from __future__ import annotations

from html import escape
from typing import Any, Dict, Iterable, List, Tuple

from .errors import ContractError


OUTLOOK_LABELS = {
    "short_5d": "未來 5 天勝率（短線）",
    "medium_1m": "未來 1 個月勝率（中線）",
    "cycle_3m": "未來 3 個月勝率（Cycle 反應）",
}
OUTLOOK_ORDER = ("short_5d", "medium_1m", "cycle_3m")
ASSESSMENT_LABELS = {
    "positive_trend": "Positive trend / 正向趨勢",
    "negative_trend": "Negative trend / 負向趨勢",
    "watch": "Watch / 觀察",
    "no_action": "No action / 暫無方向",
}
CONFIDENCE_LABELS = {
    "high": "High / 高",
    "medium": "Medium / 中",
    "low": "Low / 低",
}


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _outlook_map(signal: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        item["horizon_key"]: item
        for item in signal.get("horizon_outlooks", [])
        if item.get("horizon_key") in OUTLOOK_LABELS
    }


def _direction_probability(signal: Dict[str, Any], direction: str) -> float:
    outlook = _outlook_map(signal).get("short_5d")
    if not outlook:
        return -1.0
    probability = float(outlook["estimated_probability_pct"])
    outlook_direction = outlook["direction"]
    if outlook_direction == direction:
        return probability
    if outlook_direction in ("up", "down"):
        return 100.0 - probability
    return 50.0


def ranked_research_views(
    signals: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return validated positive opportunities and negative warning views."""

    passed = [
        {
            "signal": signal,
            "score": float(signal["signal_strength_score"]),
        }
        for signal in signals
        if signal.get("validation_status") == "pass"
    ]
    opportunities = [
        item
        for item in passed
        if item["signal"].get("assessment") == "positive_trend"
    ]
    warnings = [
        item
        for item in passed
        if item["signal"].get("assessment") == "negative_trend"
    ]
    opportunities.sort(
        key=lambda item: (
            -_direction_probability(item["signal"], "up"),
            -item["score"],
            item["signal"]["symbol"],
        )
    )
    warnings.sort(
        key=lambda item: (
            -_direction_probability(item["signal"], "down"),
            -item["score"],
            item["signal"]["symbol"],
        )
    )
    return opportunities[:5], warnings


def _probability_display(outlook: Dict[str, Any]) -> str:
    direction = {"up": "&#8593;", "down": "&#8595;", "uncertain": "&#8594;"}[
        outlook["direction"]
    ]
    probability = float(outlook["estimated_probability_pct"])
    value = "%g%%" % probability
    return (
        '<span style="font-size:16px;font-weight:700;color:#172554;">%s %s</span>'
        '<br><span style="font-size:11px;color:#64748b;">模型估計・未校準</span>'
        % (direction, _e(value))
    )


def _outlook_cells(signal: Dict[str, Any]) -> str:
    outlooks = _outlook_map(signal)
    cells = []
    for key in OUTLOOK_ORDER:
        outlook = outlooks.get(key)
        display = (
            _probability_display(outlook)
            if outlook
            else '<span style="color:#94a3b8;">N/A</span>'
        )
        cells.append(
            '<td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;'
            'text-align:center;vertical-align:top;min-width:108px;">%s</td>'
            % display
        )
    return "".join(cells)


def _signal_rows(views: List[Dict[str, Any]], warning: bool = False) -> str:
    if not views:
        message = (
            "No independently validated negative-trend warnings."
            if warning
            else "No independently validated positive-trend opportunities."
        )
        return (
            '<tr><td colspan="8" style="padding:18px;color:#64748b;'
            'text-align:center;border-bottom:1px solid #e2e8f0;">%s</td></tr>'
            % _e(message)
        )
    rows = []
    for rank, view in enumerate(views, start=1):
        signal = view["signal"]
        detail = (
            "; ".join(signal.get("monitoring_triggers", []))
            if warning
            else signal["thesis"]
        )
        rows.append(
            "".join(
                [
                    "<tr>",
                    '<td style="padding:12px 8px;border-bottom:1px solid #e2e8f0;'
                    'text-align:center;color:#64748b;">%d</td>' % rank,
                    '<td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;'
                    'vertical-align:top;"><strong style="font-size:17px;color:#0f172a;">%s</strong>'
                    '<br><span style="font-size:11px;color:#64748b;">%s</span></td>'
                    % (_e(signal["symbol"]), _e(signal["venue"])),
                    '<td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;'
                    'text-align:center;vertical-align:top;"><strong>%.1f/10</strong>'
                    '<br><span style="font-size:11px;color:#64748b;">訊號強度</span></td>'
                    % view["score"],
                    '<td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;'
                    'text-align:center;vertical-align:top;">%s</td>'
                    % _e(CONFIDENCE_LABELS.get(signal["confidence_bucket"], signal["confidence_bucket"])),
                    _outlook_cells(signal),
                    '<td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;'
                    'vertical-align:top;min-width:220px;color:#334155;">%s</td>'
                    % _e(detail),
                    "</tr>",
                ]
            )
        )
    return "".join(rows)


def _research_table(views: List[Dict[str, Any]], warning: bool = False) -> str:
    return (
        '<div style="overflow-x:auto;border:1px solid #e2e8f0;border-radius:10px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%" '
        'style="border-collapse:collapse;background:#ffffff;font-size:13px;">'
        '<thead><tr style="background:#f8fafc;color:#475569;">'
        '<th style="padding:10px 8px;text-align:center;">#</th>'
        '<th style="padding:10px;text-align:left;">Symbol</th>'
        '<th style="padding:10px;text-align:center;">Score</th>'
        '<th style="padding:10px;text-align:center;">Confidence</th>'
        '<th style="padding:10px;text-align:center;">5D</th>'
        '<th style="padding:10px;text-align:center;">1M</th>'
        '<th style="padding:10px;text-align:center;">3M</th>'
        '<th style="padding:10px;text-align:left;">%s</th>'
        '</tr></thead><tbody>%s</tbody></table></div>'
        % ("Warning triggers" if warning else "Research thesis", _signal_rows(views, warning))
    )


def _metric_card(label: str, value: Any, color: str) -> str:
    return (
        '<td width="25%%" style="padding:6px;vertical-align:top;">'
        '<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;'
        'padding:14px;text-align:center;">'
        '<div style="font-size:24px;font-weight:700;color:%s;">%s</div>'
        '<div style="font-size:12px;color:#64748b;margin-top:4px;">%s</div>'
        '</div></td>'
        % (color, _e(value), _e(label))
    )


def render_analysis_email(
    batch_id: str,
    run_id: str,
    manifest: Dict[str, Any],
    context: Dict[str, Any],
    signals: List[Dict[str, Any]],
    coverage: Dict[str, Any],
    source_input: Dict[str, Any],
) -> str:
    opportunities, warnings = ranked_research_views(signals)
    validated_count = sum(item["validation_status"] == "pass" for item in signals)
    watch_count = sum(
        item["validation_status"] == "pass" and item["assessment"] == "watch"
        for item in signals
    )
    excluded_count = len(signals) - validated_count
    degraded = manifest.get("degraded_reasons", [])
    coverage_status = source_input.get("coverage_status", "test_or_unspecified")
    if coverage.get("profile") == "production":
        bucket_count = len(coverage.get("buckets", {}))
        coverage_detail = "%s; %d industry buckets reported" % (
            coverage_status,
            bucket_count,
        )
    else:
        coverage_detail = "%s; demo/test coverage gate" % coverage_status
    degraded_banner = ""
    if degraded:
        degraded_banner = (
            '<div style="margin:0 0 18px;padding:12px 14px;background:#fff7ed;'
            'border:1px solid #fdba74;border-radius:8px;color:#9a3412;">'
            '<strong>Degraded research run:</strong> %s</div>'
            % _e(", ".join(degraded))
        )
    validation_note = (
        "%d signals were excluded from rankings because independent validation did not pass."
        % excluded_count
        if excluded_count
        else "All rendered ranked signals passed independent cross-vendor validation."
    )
    return "".join(
        [
            "<!doctype html>",
            '<html lang="zh-Hant"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>Stock Trend Research Briefing</title></head>',
            "<body style=\"margin:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,"
            "'Segoe UI',Arial,sans-serif;color:#0f172a;\">",
            '<div style="display:none;max-height:0;overflow:hidden;">Research-only trend briefing '
            'with independently validated opportunity and downside views.</div>',
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="background:#f1f5f9;"><tr><td align="center" style="padding:24px 10px;">',
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="max-width:1120px;background:#ffffff;border-radius:14px;overflow:hidden;">',
            '<tr><td style="padding:28px;background:#172554;color:#ffffff;">',
            '<div style="font-size:12px;letter-spacing:1.2px;text-transform:uppercase;color:#bfdbfe;">'
            'Research only · 非投資指令</div>',
            '<h1 style="margin:8px 0 4px;font-size:28px;line-height:1.25;">Stock Trend Research Briefing</h1>',
            '<div style="font-size:13px;color:#dbeafe;">Session %s · Batch %s · Run %s</div>'
            % (
                _e(manifest["identity"]["exchange_session_date"]),
                _e(batch_id),
                _e(run_id),
            ),
            '</td></tr><tr><td style="padding:24px;">',
            degraded_banner,
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:0 0 22px;"><tr>',
            _metric_card("Validated signals", "%d/%d" % (validated_count, len(signals)), "#1d4ed8"),
            _metric_card("Top opportunities", len(opportunities), "#15803d"),
            _metric_card("Downside warnings", len(warnings), "#b91c1c"),
            _metric_card("Watch list", watch_count, "#a16207"),
            "</tr></table>",
            '<div style="margin:0 0 22px;padding:16px;background:#eff6ff;border-left:4px solid #2563eb;">',
            '<div style="font-size:12px;font-weight:700;color:#1d4ed8;text-transform:uppercase;">Market context</div>',
            '<div style="margin-top:5px;line-height:1.55;color:#1e3a8a;">%s</div></div>'
            % _e(context["summary"]),
            '<h2 style="margin:0 0 6px;font-size:21px;">Top 5 Research Opportunities / 前五名研究機會</h2>',
            '<p style="margin:0 0 12px;color:#64748b;font-size:13px;">'
            'Only validated positive-trend signals are included; ranking uses the 5-day upward outlook, '
            'then analyst signal strength. It is not a profit forecast.</p>',
            _research_table(opportunities),
            '<h2 style="margin:26px 0 6px;font-size:21px;color:#991b1b;">Downside Risk Warnings / 下行風險警示</h2>',
            '<p style="margin:0 0 12px;color:#64748b;font-size:13px;">'
            'These are validated negative-trend monitoring warnings, not sell instructions.</p>',
            _research_table(warnings, warning=True),
            '<h2 style="margin:26px 0 8px;font-size:19px;">Probability horizon guide / 勝率區間說明</h2>',
            '<ul style="margin:0 0 18px;padding-left:22px;color:#475569;line-height:1.65;">'
            '<li><strong>%s</strong>: 5 market sessions.</li>'
            '<li><strong>%s</strong>: 21 market sessions.</li>'
            '<li><strong>%s</strong>: 63 market sessions and cycle response.</li>'
            '</ul>'
            % tuple(_e(OUTLOOK_LABELS[key]) for key in OUTLOOK_ORDER),
            '<div style="padding:16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;'
            'font-size:13px;color:#475569;line-height:1.55;">'
            '<strong>Validation and data quality</strong><br>%s<br>Source coverage: %s</div>'
            % (_e(validation_note), _e(coverage_detail)),
            '<div style="margin-top:18px;padding:14px;background:#fff7ed;border-radius:8px;'
            'font-size:12px;color:#9a3412;line-height:1.55;">'
            '<strong>Important:</strong> percentages are uncalibrated model estimates linked to the cited '
            'research evidence. They are not historical win rates, expected returns, profit guarantees, '
            'personalized advice, or transaction instructions. Validate against the attached digest and '
            'your own risk process.</div>',
            '</td></tr></table></td></tr></table></body></html>',
        ]
    )


def render_system_logs_email(
    batch_id: str,
    run_id: str,
    manifest: Dict[str, Any],
    source_input: Dict[str, Any],
    signal_count: int,
    validated_count: int,
) -> str:
    producer = manifest["versions"]
    rows = [
        ("Batch", batch_id),
        ("Run", run_id),
        ("Snapshot stage", "pre_committer"),
        (
            "Producer",
            "%s / %s" % (producer["producer_vendor"], producer["producer_model"]),
        ),
        (
            "Validator",
            "%s / %s" % (producer["validator_vendor"], producer["validator_model"]),
        ),
        ("Validated signals", "%d/%d" % (validated_count, signal_count)),
        (
            "Degraded reasons",
            ", ".join(manifest.get("degraded_reasons", [])) or "none",
        ),
        (
            "Source coverage",
            source_input.get("coverage_status", "test_or_unspecified"),
        ),
    ]
    rendered_rows = "".join(
        '<tr><th style="padding:10px;text-align:left;border-bottom:1px solid #e2e8f0;'
        'background:#f8fafc;color:#475569;width:180px;">%s</th>'
        '<td style="padding:10px;border-bottom:1px solid #e2e8f0;color:#0f172a;">%s</td></tr>'
        % (_e(label), _e(value))
        for label, value in rows
    )
    template = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Stock Trend System Logs</title></head>'
        "<body style=\"margin:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,"
        "'Segoe UI',Arial,sans-serif;color:#0f172a;\">"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td align="center" style="padding:24px 10px;"><div style="max-width:760px;background:#ffffff;'
        'border-radius:12px;padding:24px;text-align:left;">'
        '<h1 style="margin:0 0 6px;font-size:24px;">System logs</h1>'
        '<p style="margin:0 0 18px;color:#64748b;">Sanitized pre-committer completion snapshot.</p>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;border:1px solid #e2e8f0;">{rows}</table>'
        '<p style="margin:18px 0 0;color:#475569;line-height:1.55;">The sanitized JSON system '
        'log is attached. Credentials, raw provider prompts, and raw provider responses are excluded.</p>'
        '</div></td></tr></table></body></html>'
    )
    return template.replace("{rows}", rendered_rows)


def html_email_qa(document: str, required_markers: Iterable[str]) -> None:
    lowered = document.lower()
    if not lowered.startswith("<!doctype html>"):
        raise ContractError("email body must be a complete HTML document")
    if "<script" in lowered or "javascript:" in lowered:
        raise ContractError("unsafe active content in email body")
    if "{{" in document or "}}" in document:
        raise ContractError("unfilled email template slot")
    for marker in required_markers:
        if marker not in document:
            raise ContractError("email body missing required section: %s" % marker)
