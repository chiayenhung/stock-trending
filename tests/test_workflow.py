from __future__ import annotations

from pathlib import Path

from stocktrend.committer import ArtifactCommitter
from stocktrend.demo import create_demo_clients
from stocktrend.providers import ScriptedClient
from stocktrend.util import load_json
from stocktrend.workflow import AnalysisWorkflow


def test_demo_workflow_finalizes_and_is_idempotent(
    project_root: Path,
    monkeypatch,
) -> None:
    original_commit = ArtifactCommitter.commit

    def guarded_commit(self, run_id, relative_paths):
        requests = [
            load_json(path)
            for path in sorted(
                (project_root / "state" / "outbox").glob(
                    "email_%s_*.json" % run_id
                )
            )
        ]
        assert len(requests) == 2
        assert {item["status"] for item in requests} == {"blocked"}
        return original_commit(self, run_id, relative_paths)

    monkeypatch.setattr(ArtifactCommitter, "commit", guarded_commit)
    producer, validator = create_demo_clients("openai", "anthropic")
    workflow = AnalysisWorkflow(project_root, producer, validator)
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    first = workflow.run(document)
    second = workflow.run(document)
    assert first["run_id"] == second["run_id"]
    assert first["manifest"]["state"] == "committed"
    signals = load_json(
        Path(first["run_directory"]) / "validation" / "research_signals.json"
    )["research_signals"]
    assert len(signals) == 1
    assert signals[0]["symbol"] == "NVDA"
    assert signals[0]["research_only"] is True
    assert signals[0]["validation_status"] == "pass"
    assert signals[0]["schema_version"] == "2.0.0"
    assert signals[0]["signal_strength_score"] == 9.1
    assert [item["horizon_sessions"] for item in signals[0]["horizon_outlooks"]] == [
        5,
        21,
        63,
    ]
    reports = load_json(
        Path(first["run_directory"]) / "validation" / "reports.json"
    )["reports"]
    assert reports[0]["producer"]["vendor"] == "openai"
    assert reports[0]["validator"]["vendor"] == "anthropic"
    assert reports[0]["vendor_separation"] is True
    assert Path(first["digest"]).exists()
    assert Path(first["trending_analysis_email"]).exists()
    assert Path(first["system_logs_email"]).exists()
    assert Path(first["system_log"]).exists()
    assert Path(first["commit_receipt"]).exists()
    assert [item["email_kind"] for item in first["notifications"]] == [
        "system_logs",
        "trending_analysis",
    ]
    trace = load_json(Path(first["run_directory"]) / "trace" / "events.json")
    assert trace[0]["event"] == "run_created"
    states = [item["state"] for item in trace]
    assert states[-3:] == ["emails_generated", "finalized", "committed"]


def test_same_vendor_forces_research_only(project_root: Path) -> None:
    producer, validator = create_demo_clients("openai", "openai")
    workflow = AnalysisWorkflow(project_root, producer, validator)
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    result = workflow.run(document)
    signals = load_json(
        Path(result["run_directory"]) / "validation" / "research_signals.json"
    )["research_signals"]
    assert signals[0]["research_only"] is True
    assert signals[0]["validation_status"] == "unavailable"
    assert "VENDOR_MATCH" in signals[0]["validation_reason_codes"]
    assert "VALIDATOR_VENDOR_MATCH" in result["manifest"]["degraded_reasons"]


def test_validator_outage_forces_research_only(project_root: Path) -> None:
    producer, _ = create_demo_clients("openai", "anthropic")

    def fail(task_name, payload):
        del task_name, payload
        raise RuntimeError("simulated outage")

    validator = ScriptedClient(
        "anthropic",
        "outage-validator",
        handlers={},
        default_handler=fail,
    )
    workflow = AnalysisWorkflow(project_root, producer, validator)
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    result = workflow.run(document)
    signals = load_json(
        Path(result["run_directory"]) / "validation" / "research_signals.json"
    )["research_signals"]
    assert signals[0]["research_only"] is True
    assert signals[0]["validation_status"] == "unavailable"
    assert "VALIDATOR_UNAVAILABLE" in signals[0]["validation_reason_codes"]
    assert "INDEPENDENT_VALIDATOR_UNAVAILABLE" in result["manifest"]["degraded_reasons"]


def test_inconsistent_pass_verdict_is_indeterminate(project_root: Path) -> None:
    producer, _ = create_demo_clients("openai", "anthropic")

    def inconsistent(payload):
        target = payload["target"]
        return {
            "schema_version": "1.0.0",
            "target_id": target["research_signal_id"],
            "verdict": "pass",
            "supported_claim_ids": [],
            "unsupported_claim_ids": list(target["evidence_claim_ids"]),
            "reason_codes": ["UNSUPPORTED_CLAIMS_PRESENT"],
            "summary": "Contradictory pass verdict.",
        }

    validator = ScriptedClient(
        "anthropic",
        "inconsistent-validator",
        handlers={"semantic_validator": inconsistent},
    )
    result = AnalysisWorkflow(project_root, producer, validator).run(
        load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    )
    signals = load_json(
        Path(result["run_directory"]) / "validation" / "research_signals.json"
    )["research_signals"]
    assert signals[0]["validation_status"] == "indeterminate"
    assert "VERDICT_CLAIM_COVERAGE_MISMATCH" in signals[0][
        "validation_reason_codes"
    ]


def test_source_degradation_is_visible_on_research_run(project_root: Path) -> None:
    producer, validator = create_demo_clients("openai", "anthropic")
    workflow = AnalysisWorkflow(
        project_root,
        producer,
        validator,
        initial_degraded_reasons=["SOURCE_COVERAGE_INCOMPLETE"],
    )
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    result = workflow.run(document)
    signals = load_json(
        Path(result["run_directory"]) / "validation" / "research_signals.json"
    )["research_signals"]
    assert signals[0]["research_only"] is True
    assert "SOURCE_COVERAGE_INCOMPLETE" in result["manifest"]["degraded_reasons"]
