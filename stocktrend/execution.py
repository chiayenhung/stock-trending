"""Paper-only risk, approval, order-state, and protective-exit primitives."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

from .contracts import SchemaRegistry
from .errors import SafetyViolation, StateTransitionError
from .util import (
    isoformat_utc,
    parse_datetime,
    sha256_json,
    utc_now,
    utc_now_iso,
)


def client_order_id(
    environment: str,
    intent_id: str,
    intent_revision: int,
    order_leg: str,
    order_revision: int,
) -> str:
    digest = sha256_json(
        {
            "environment": environment,
            "intent_id": intent_id,
            "intent_revision": intent_revision,
            "order_leg": order_leg,
            "order_revision": order_revision,
        }
    )
    return "st_%s_%s_%s_r%d" % (
        environment,
        digest[:16],
        order_leg,
        order_revision,
    )


class RiskEngine:
    def __init__(self, risk_policy: Dict[str, Any], registry: SchemaRegistry):
        self.risk_policy = risk_policy
        self.registry = registry

    def create_intent(
        self,
        proposal: Dict[str, Any],
        quote_fact: Dict[str, Any],
        account: Dict[str, float],
        as_of: str,
        environment: str = "paper",
    ) -> Dict[str, Any]:
        if environment == "live":
            raise SafetyViolation("live execution is not implemented or enabled")
        if environment != "paper":
            raise SafetyViolation("execution environment must be paper")
        if not proposal.get("execution_eligible"):
            raise SafetyViolation("proposal is not execution eligible")
        now = parse_datetime(as_of)
        if parse_datetime(proposal["expires_at"]) <= now:
            raise SafetyViolation("proposal expired")
        policy = self.risk_policy["paper"]
        quote_age = (now - parse_datetime(quote_fact["observed_at"])).total_seconds()
        if quote_age < 0 or quote_age > float(policy["maximum_quote_age_seconds"]):
            raise SafetyViolation("quote is stale or from the future")
        price = float(quote_fact["value"]["price"])
        maximum_entry = proposal.get("maximum_entry_price")
        if proposal["signal_type"] in ("enter_long", "add_long"):
            if maximum_entry is None or price > float(maximum_entry):
                raise SafetyViolation("entry price protection failed")
            if proposal.get("stop_price") is None or proposal.get("target_price") is None:
                raise SafetyViolation("protective exit plan is incomplete")
            side = "buy"
        else:
            side = "sell"
        maximum_order = float(policy["maximum_order_notional_usd"])
        maximum_position = float(policy["maximum_position_notional_usd"])
        current_position = float(account.get("position_notional_usd", 0.0))
        portfolio = float(account.get("portfolio_notional_usd", 0.0))
        buying_power = float(account.get("buying_power_usd", 0.0))
        cash_reserve = float(policy["minimum_cash_reserve_usd"])
        available_cash = max(0.0, buying_power - cash_reserve)
        available_position = max(0.0, maximum_position - current_position)
        available_portfolio = max(
            0.0,
            float(policy["maximum_portfolio_notional_usd"]) - portfolio,
        )
        notional = min(maximum_order, available_cash, available_position, available_portfolio)
        quantity = int(notional // price)
        if quantity < 1:
            raise SafetyViolation("risk limits allow no quantity")
        intent_seed = {
            "signal_id": proposal["signal_id"],
            "signal_revision": proposal["revision"],
            "environment": environment,
            "price": price,
            "quantity": quantity,
            "quote_fact_id": quote_fact["fact_id"],
        }
        intent = {
            "schema_version": "1.0.0",
            "intent_id": "intent_%s" % sha256_json(intent_seed)[:20],
            "intent_revision": 1,
            "signal_id": proposal["signal_id"],
            "signal_revision": proposal["revision"],
            "environment": environment,
            "symbol": proposal["symbol"],
            "venue": proposal["venue"],
            "side": side,
            "quantity": quantity,
            "order_type": "limit",
            "limit_price": min(price, float(maximum_entry))
            if maximum_entry is not None
            else price,
            "stop_price": proposal.get("stop_price"),
            "target_price": proposal.get("target_price"),
            "risk_policy_version": self.risk_policy["policy_version"],
            "approval_required": True,
            "expires_at": proposal["expires_at"],
            "quote_fact_id": quote_fact["fact_id"],
        }
        self.registry.validate("execution_intent", intent)
        return intent


class ApprovalService:
    def __init__(self, approval_policy: Dict[str, Any], registry: SchemaRegistry):
        self.approval_policy = approval_policy
        self.registry = registry

    def decide(
        self,
        intent: Dict[str, Any],
        approver: str,
        approved: bool,
        decided_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        decided = parse_datetime(decided_at) if decided_at else utc_now()
        policy_expiry = decided + timedelta(
            seconds=int(self.approval_policy["paper"]["approval_ttl_seconds"])
        )
        intent_expiry = parse_datetime(intent["expires_at"])
        expires = min(policy_expiry, intent_expiry)
        record = {
            "schema_version": "1.0.0",
            "approval_id": "approval_%s" % uuid.uuid4().hex,
            "intent_id": intent["intent_id"],
            "intent_revision": intent["intent_revision"],
            "decision": "approved" if approved else "rejected",
            "approver": approver,
            "decided_at": isoformat_utc(decided),
            "expires_at": isoformat_utc(expires),
            "bound_intent_hash": sha256_json(intent),
            "protective_exits_authorized": bool(
                approved
                and intent.get("stop_price") is not None
                and intent.get("target_price") is not None
            ),
        }
        self.registry.validate("approval_record", record)
        return record


class OrderStateMachine:
    TRANSITIONS = {
        None: {"proposed"},
        "proposed": {"validated"},
        "validated": {"pending_approval"},
        "pending_approval": {"approved", "rejected", "expired"},
        "approved": {"submitting"},
        "submitting": {"submitted", "error"},
        "submitted": {"acknowledged", "rejected", "cancel_pending", "error"},
        "acknowledged": {
            "partially_filled",
            "filled",
            "cancel_pending",
            "rejected",
            "error",
        },
        "partially_filled": {"filled", "cancel_pending", "error"},
        "cancel_pending": {"canceled", "filled", "error"},
        "filled": set(),
        "canceled": set(),
        "rejected": set(),
        "expired": set(),
        "error": {"reconciliation_required"},
        "reconciliation_required": {"reconciled"},
        "reconciled": set(),
    }

    def __init__(
        self,
        registry: SchemaRegistry,
        intent: Dict[str, Any],
        order_leg: str,
        order_revision: int = 1,
    ):
        self.registry = registry
        self.intent = intent
        self.order_leg = order_leg
        self.order_revision = order_revision
        self.state: Optional[str] = None
        self.events: List[Dict[str, Any]] = []
        self.client_order_id = client_order_id(
            intent["environment"],
            intent["intent_id"],
            intent["intent_revision"],
            order_leg,
            order_revision,
        )

    def transition(
        self,
        new_state: str,
        occurred_at: Optional[str] = None,
        broker_order_id: Optional[str] = None,
        filled_quantity: int = 0,
        average_fill_price: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        if new_state not in self.TRANSITIONS.get(self.state, set()):
            raise StateTransitionError("%s -> %s is not allowed" % (self.state, new_state))
        event = {
            "schema_version": "1.0.0",
            "event_id": "event_%s" % uuid.uuid4().hex,
            "client_order_id": self.client_order_id,
            "intent_id": self.intent["intent_id"],
            "order_leg": self.order_leg,
            "order_revision": self.order_revision,
            "from_state": self.state,
            "to_state": new_state,
            "occurred_at": occurred_at or utc_now_iso(),
            "broker_order_id": broker_order_id,
            "filled_quantity": filled_quantity,
            "average_fill_price": average_fill_price,
            "reason": reason,
        }
        self.registry.validate("order_event", event)
        self.state = new_state
        self.events.append(event)
        return event


@dataclass
class PaperExecutionResult:
    entry_events: List[Dict[str, Any]]
    protective_exit_events: List[Dict[str, Any]]


class PaperBroker:
    def __init__(self, registry: SchemaRegistry):
        self.registry = registry

    def execute(
        self,
        intent: Dict[str, Any],
        approval: Dict[str, Any],
        as_of: Optional[str] = None,
    ) -> PaperExecutionResult:
        now = parse_datetime(as_of) if as_of else utc_now()
        if intent["environment"] != "paper":
            raise SafetyViolation("paper broker accepts only paper intents")
        if approval["decision"] != "approved":
            raise SafetyViolation("intent is not approved")
        if approval["bound_intent_hash"] != sha256_json(intent):
            raise SafetyViolation("approval does not bind to current intent")
        if parse_datetime(approval["expires_at"]) <= now:
            raise SafetyViolation("approval expired")
        if not approval["protective_exits_authorized"]:
            raise SafetyViolation("protective exits were not authorized")
        occurred_at = isoformat_utc(now)
        machine = OrderStateMachine(self.registry, intent, "entry")
        for state in (
            "proposed",
            "validated",
            "pending_approval",
            "approved",
            "submitting",
            "submitted",
            "acknowledged",
        ):
            machine.transition(state, occurred_at=occurred_at)
        machine.transition(
            "filled",
            occurred_at=occurred_at,
            broker_order_id="paper_%s" % uuid.uuid4().hex[:16],
            filled_quantity=intent["quantity"],
            average_fill_price=float(intent["limit_price"]),
        )
        protective_events: List[Dict[str, Any]] = []
        for leg in ("stop", "target"):
            exit_machine = OrderStateMachine(self.registry, intent, leg)
            for state in (
                "proposed",
                "validated",
                "pending_approval",
                "approved",
                "submitting",
                "submitted",
                "acknowledged",
            ):
                protective_events.append(
                    exit_machine.transition(state, occurred_at=occurred_at)
                )
        return PaperExecutionResult(
            entry_events=machine.events,
            protective_exit_events=protective_events,
        )


class LiveBroker:
    def execute(self, intent: Dict[str, Any], approval: Dict[str, Any]) -> None:
        del intent, approval
        raise SafetyViolation("live broker is intentionally not implemented")
