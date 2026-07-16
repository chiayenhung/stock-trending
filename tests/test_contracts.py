from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from stocktrend.contracts import SchemaRegistry


def test_all_contract_schemas_are_valid() -> None:
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"
    for path in sorted(schema_dir.glob("*.schema.json")):
        with path.open("r", encoding="utf-8") as handle:
            Draft202012Validator.check_schema(json.load(handle))


def test_registry_loads_all_expected_contracts() -> None:
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"
    registry = SchemaRegistry(schema_dir)
    assert {
        "fact",
        "facts_block",
        "cycle_context",
        "analyst_output",
        "signal_proposal",
        "semantic_verdict",
        "validation_report",
        "run_manifest",
        "execution_intent",
        "approval_record",
        "order_event",
        "outcome",
        "delivery_outbox_item",
        "email_delivery_request",
        "universe",
        "source_snapshot",
        "source_heartbeat",
    }.issubset(set(registry.names()))
