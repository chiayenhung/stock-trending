from __future__ import annotations

from pathlib import Path

from stocktrend.demo import create_demo_clients
from stocktrend.providers import ScriptedClient
from stocktrend.util import load_json
from stocktrend.workflow import AnalysisWorkflow


def test_demo_workflow_finalizes_and_is_idempotent(project_root: Path) -> None:
    producer, validator = create_demo_clients("openai", "anthropic")
    workflow = AnalysisWorkflow(project_root, producer, validator)
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    first = workflow.run(document)
    second = workflow.run(document)
    assert first["run_id"] == second["run_id"]
    assert first["manifest"]["state"] == "finalized"
    proposals = load_json(
        Path(first["run_directory"]) / "validation" / "signal_proposals.json"
    )["signal_proposals"]
    assert len(proposals) == 1
    assert proposals[0]["symbol"] == "NVDA"
    assert proposals[0]["execution_eligible"] is True
    reports = load_json(
        Path(first["run_directory"]) / "validation" / "reports.json"
    )["reports"]
    assert reports[0]["producer"]["vendor"] == "openai"
    assert reports[0]["validator"]["vendor"] == "anthropic"
    assert reports[0]["vendor_separation"] is True
    assert Path(first["digest"]).exists()
    trace = load_json(Path(first["run_directory"]) / "trace" / "events.json")
    assert trace[0]["event"] == "run_created"
    assert trace[-1]["state"] == "finalized"


def test_same_vendor_forces_research_only(project_root: Path) -> None:
    producer, validator = create_demo_clients("openai", "openai")
    workflow = AnalysisWorkflow(project_root, producer, validator)
    document = load_json(project_root / "tests" / "fixtures" / "demo_observations.json")
    result = workflow.run(document)
    proposals = load_json(
        Path(result["run_directory"]) / "validation" / "signal_proposals.json"
    )["signal_proposals"]
    assert proposals[0]["execution_eligible"] is False
    assert "VENDOR_MATCH" in proposals[0]["eligibility_reasons"]
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
    proposals = load_json(
        Path(result["run_directory"]) / "validation" / "signal_proposals.json"
    )["signal_proposals"]
    assert proposals[0]["execution_eligible"] is False
    assert "VALIDATOR_UNAVAILABLE" in proposals[0]["eligibility_reasons"]
    assert "INDEPENDENT_VALIDATOR_UNAVAILABLE" in result["manifest"]["degraded_reasons"]
