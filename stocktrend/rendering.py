"""Deterministic digest rendering and artifact QA."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from .errors import ContractError


OUTLOOK_LABELS = {
    "short_5d": "5 sessions / short",
    "medium_1m": "21 sessions / medium",
    "cycle_3m": "63 sessions / cycle",
}


def _outlook_markdown(signal: Dict[str, Any]) -> str:
    values = []
    for outlook in signal.get("horizon_outlooks", []):
        label = OUTLOOK_LABELS.get(outlook["horizon_key"], outlook["horizon_key"])
        values.append(
            "%s: %s %g%%"
            % (
                label,
                outlook["direction"],
                float(outlook["estimated_probability_pct"]),
            )
        )
    return "; ".join(values) or "not available"


def render_digest(
    template_path: Path,
    run_id: str,
    session_date: str,
    context: Dict[str, Any],
    research_signals: Iterable[Dict[str, Any]],
    validation_reports: Iterable[Dict[str, Any]],
    degraded_reasons: List[str],
    coverage: Dict[str, Any],
) -> str:
    template = template_path.read_text(encoding="utf-8")
    signal_sections = []
    for signal in research_signals:
        signal_sections.append(
            "\n".join(
                [
                    "### %s" % signal["symbol"],
                    "",
                    "- Assessment: %s" % signal["assessment"],
                    "- Research horizon: %s sessions"
                    % signal["horizon_sessions"],
                    "- Confidence assessment: %s" % signal["confidence_bucket"],
                    "- Horizon outlooks (uncalibrated model estimates): %s"
                    % _outlook_markdown(signal),
                    "- Validation status: %s" % signal["validation_status"],
                    "- Thesis: %s" % signal["thesis"],
                    "- Monitoring triggers: %s"
                    % "; ".join(signal["monitoring_triggers"]),
                    "- Evidence claims: %s"
                    % ", ".join(signal["evidence_claim_ids"]),
                ]
            )
        )
    reports = list(validation_reports)
    passed = sum(1 for report in reports if report["verdict"] == "pass")
    validation_summary = (
        "%d/%d research signals passed independent cross-vendor validation."
        % (passed, len(reports))
        if reports
        else "No research signals required semantic validation."
    )
    degraded_banner = (
        "**DEGRADED — research only:** %s" % "; ".join(degraded_reasons)
        if degraded_reasons
        else ""
    )
    values = {
        "{{session_date}}": session_date,
        "{{run_id}}": run_id,
        "{{status}}": "degraded" if degraded_reasons else "finalized",
        "{{degraded_banner}}": degraded_banner,
        "{{market_context}}": context["summary"],
        "{{source_coverage}}": _coverage_markdown(coverage),
        "{{candidate_sections}}": "\n\n".join(signal_sections)
        or "No screened candidates.",
        "{{validation_summary}}": validation_summary,
    }
    output = template
    for slot, value in values.items():
        output = output.replace(slot, value)
    return output


def artifact_qa(
    digest: str,
    research_signals: Iterable[Dict[str, Any]],
    known_claim_ids: set,
) -> None:
    if "{{" in digest or "}}" in digest:
        raise ContractError("unfilled digest template slot")
    for signal in research_signals:
        missing = set(signal["evidence_claim_ids"]) - known_claim_ids
        if missing:
            raise ContractError(
                "digest research signal references unknown claims: %s"
                % ", ".join(sorted(missing))
            )
    if "<script" in digest.lower():
        raise ContractError("unsafe script content in digest")
def _coverage_markdown(coverage: Dict[str, Any]) -> str:
    if coverage.get("profile") != "production":
        return "Coverage gate not enforced for this demo/test input."
    lines = [
        "| Industry bucket | Configured | Attempted | Valid | Passed screen |",
        "|---|---:|---:|---:|---:|",
    ]
    for bucket, counts in sorted(coverage["buckets"].items()):
        passing = int(counts["passing_screen"])
        bucket_label = bucket.replace("_", " ")
        if passing == 0:
            bucket_label += " — no passing candidates"
        lines.append(
            "| %s | %d | %d | %d | %d |"
            % (
                bucket_label,
                counts["configured"],
                counts["attempted"],
                counts["valid"],
                passing,
            )
        )
    return "\n".join(lines)
