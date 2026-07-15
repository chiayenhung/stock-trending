from __future__ import annotations

from pathlib import Path

import pytest

from stocktrend.config import ConfigBundle
from stocktrend.contracts import SchemaRegistry
from stocktrend.demo import create_demo_clients
from stocktrend.errors import SafetyViolation, StateTransitionError
from stocktrend.execution import (
    ApprovalService,
    LiveBroker,
    OrderStateMachine,
    PaperBroker,
    RiskEngine,
)
from stocktrend.util import load_json
from stocktrend.workflow import AnalysisWorkflow


def prepared(project_root: Path):
    producer, validator = create_demo_clients()
    result = AnalysisWorkflow(project_root, producer, validator).run(
        load_json(project_root / "tests" / "fixtures" / "demo_observations.json"),
        execution_mode="paper",
    )
    run_dir = Path(result["run_directory"])
    proposal = load_json(
        run_dir / "validation" / "signal_proposals.json"
    )["signal_proposals"][0]
    quote = next(
        fact
        for fact in load_json(run_dir / "normalized" / "facts_block.json")["facts"]
        if fact["fact_type"] == "quote" and fact["symbol"] == proposal["symbol"]
    )
    registry = SchemaRegistry(project_root / "schemas")
    config = ConfigBundle.load(project_root)
    intent = RiskEngine(config.risk, registry).create_intent(
        proposal,
        quote,
        {
            "buying_power_usd": 10000.0,
            "position_notional_usd": 0.0,
            "portfolio_notional_usd": 0.0,
        },
        as_of=proposal["decision_at"],
    )
    return registry, config, proposal, intent


def test_paper_execution_binds_approval_and_protective_exits(
    project_root: Path,
) -> None:
    registry, config, proposal, intent = prepared(project_root)
    approval = ApprovalService(config.approval, registry).decide(
        intent,
        "test-approver",
        True,
        decided_at=proposal["decision_at"],
    )
    result = PaperBroker(registry).execute(
        intent,
        approval,
        as_of=proposal["decision_at"],
    )
    assert result.entry_events[-1]["to_state"] == "filled"
    assert result.entry_events[-1]["filled_quantity"] == intent["quantity"]
    protective_ids = {
        event["client_order_id"] for event in result.protective_exit_events
    }
    assert len(protective_ids) == 2
    assert {event["order_leg"] for event in result.protective_exit_events} == {
        "stop",
        "target",
    }


def test_changed_intent_invalidates_approval(project_root: Path) -> None:
    registry, config, proposal, intent = prepared(project_root)
    approval = ApprovalService(config.approval, registry).decide(
        intent,
        "test-approver",
        True,
        decided_at=proposal["decision_at"],
    )
    intent["quantity"] += 1
    with pytest.raises(SafetyViolation, match="does not bind"):
        PaperBroker(registry).execute(intent, approval, as_of=proposal["decision_at"])


def test_live_broker_is_disabled() -> None:
    with pytest.raises(SafetyViolation, match="intentionally"):
        LiveBroker().execute({}, {})


def test_order_machine_rejects_skipped_state(project_root: Path) -> None:
    registry, config, proposal, intent = prepared(project_root)
    del config, proposal
    machine = OrderStateMachine(registry, intent, "entry")
    with pytest.raises(StateTransitionError):
        machine.transition("submitted")
