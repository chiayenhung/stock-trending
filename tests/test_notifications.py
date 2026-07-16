from __future__ import annotations

from pathlib import Path

from stocktrend.demo import create_demo_clients
from stocktrend.notifications import CompletionEmailOutbox
from stocktrend.contracts import SchemaRegistry
from stocktrend.util import load_json
from stocktrend.workflow import AnalysisWorkflow


def test_batch_generates_two_sanitized_email_requests_before_committer(
    project_root: Path,
) -> None:
    producer, validator = create_demo_clients("openai", "anthropic")
    workflow = AnalysisWorkflow(project_root, producer, validator)
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    document["batch_id"] = "batch-test-001"
    first = workflow.run(document)
    second = workflow.run(document)
    assert [item["operation_id"] for item in first["notifications"]] == [
        item["operation_id"] for item in second["notifications"]
    ]
    assert len(first["notifications"]) == 2
    assert {item["email_kind"] for item in first["notifications"]} == {
        "trending_analysis",
        "system_logs",
    }
    assert {item["batch_id"] for item in first["notifications"]} == {
        "batch-test-001"
    }
    assert {item["status"] for item in first["notifications"]} == {"pending"}
    assert {item["body_mime_type"] for item in first["notifications"]} == {
        "text/html; charset=utf-8"
    }
    analysis_request = next(
        item
        for item in first["notifications"]
        if item["email_kind"] == "trending_analysis"
    )
    analysis_html = Path(analysis_request["body_path"]).read_text(encoding="utf-8")
    assert analysis_request["schema_version"] == "2.0.0"
    assert analysis_request["body_path"].endswith("trending_analysis.html")
    assert analysis_html.startswith("<!doctype html>")
    assert "Top 5 Research Opportunities" in analysis_html
    assert "Downside Risk Warnings" in analysis_html
    assert "未來 5 天勝率（短線）" in analysis_html
    assert "未來 1 個月勝率（中線）" in analysis_html
    assert "未來 3 個月勝率（Cycle 反應）" in analysis_html
    assert "NVDA" in analysis_html
    assert "64%" in analysis_html
    assert "profit guarantee" in analysis_html
    assert "<script" not in analysis_html.lower()
    system_log = load_json(Path(first["system_log"]))
    assert system_log["snapshot_stage"] == "pre_committer"
    assert system_log["security"] == {
        "credentials_included": False,
        "raw_provider_prompts_included": False,
        "raw_provider_responses_included": False,
    }
    serialized = Path(first["system_log"]).read_text(encoding="utf-8")
    assert "API_KEY" not in serialized
    assert [
        item["horizon_key"]
        for item in system_log["research_signal_summary"][0]["horizon_outlooks"]
    ] == ["short_5d", "medium_1m", "cycle_3m"]

    registry = SchemaRegistry(project_root / "schemas")
    logs_request = next(
        item for item in first["notifications"] if item["email_kind"] == "system_logs"
    )
    acknowledged = CompletionEmailOutbox(project_root, registry).acknowledge(
        logs_request["operation_id"],
        "gmail-message-id",
    )
    assert acknowledged["status"] == "acknowledged"
    assert acknowledged["provider_message_id"] == "gmail-message-id"
