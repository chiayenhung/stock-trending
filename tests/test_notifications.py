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
    system_log = load_json(Path(first["system_log"]))
    assert system_log["snapshot_stage"] == "pre_committer"
    assert system_log["security"] == {
        "credentials_included": False,
        "raw_provider_prompts_included": False,
        "raw_provider_responses_included": False,
    }
    serialized = Path(first["system_log"]).read_text(encoding="utf-8")
    assert "API_KEY" not in serialized

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
