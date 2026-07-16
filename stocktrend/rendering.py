"""Deterministic digest rendering and artifact QA."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from .errors import ContractError


def render_digest(
    template_path: Path,
    run_id: str,
    session_date: str,
    context: Dict[str, Any],
    proposals: Iterable[Dict[str, Any]],
    validation_reports: Iterable[Dict[str, Any]],
    degraded_reasons: List[str],
    coverage: Dict[str, Any],
) -> str:
    template = template_path.read_text(encoding="utf-8")
    proposal_sections = []
    for proposal in proposals:
        proposal_sections.append(
            "\n".join(
                [
                    "### %s" % proposal["symbol"],
                    "",
                    "- Signal: %s" % proposal["signal_type"],
                    "- Proposed maximum entry: %s"
                    % _money(proposal["maximum_entry_price"]),
                    "- Proposed stop: %s" % _money(proposal["stop_price"]),
                    "- Proposed target: %s" % _money(proposal["target_price"]),
                    "- Time exit: %s sessions" % proposal["time_exit_sessions"],
                    "- Confidence assessment: %s" % proposal["confidence_bucket"],
                    "- Execution eligible: %s"
                    % ("yes" if proposal["execution_eligible"] else "no"),
                    "- Evidence claims: %s"
                    % ", ".join(proposal["evidence_claim_ids"]),
                ]
            )
        )
    reports = list(validation_reports)
    passed = sum(1 for report in reports if report["verdict"] == "pass")
    validation_summary = (
        "%d/%d actionable proposals passed independent cross-vendor validation."
        % (passed, len(reports))
        if reports
        else "No actionable proposals required semantic validation."
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
        "{{candidate_sections}}": "\n\n".join(proposal_sections)
        or "No screened candidates.",
        "{{validation_summary}}": validation_summary,
    }
    output = template
    for slot, value in values.items():
        output = output.replace(slot, value)
    return output


def artifact_qa(
    digest: str,
    proposals: Iterable[Dict[str, Any]],
    known_claim_ids: set,
) -> None:
    if "{{" in digest or "}}" in digest:
        raise ContractError("unfilled digest template slot")
    for proposal in proposals:
        missing = set(proposal["evidence_claim_ids"]) - known_claim_ids
        if missing:
            raise ContractError(
                "digest proposal references unknown claims: %s"
                % ", ".join(sorted(missing))
            )
    if "<script" in digest.lower():
        raise ContractError("unsafe script content in digest")


def _money(value: Any) -> str:
    return "n/a" if value is None else "$%.2f" % float(value)


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
