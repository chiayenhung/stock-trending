from __future__ import annotations

import argparse
from pathlib import Path

from stocktrend.cli import build_parser
from stocktrend.config import ConfigBundle
from stocktrend.contracts import SchemaRegistry


def test_configuration_and_contracts_are_research_only(project_root: Path) -> None:
    config = ConfigBundle.load(project_root)
    registry = SchemaRegistry(project_root / "schemas")
    assert config.workflow["scope"] == "research_only"
    assert config.strategy["status"] == "research_only"
    assert registry.get("research_signal")["properties"]["research_only"] == {
        "const": True
    }


def test_cli_exposes_only_research_and_delivery_commands() -> None:
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(subparsers.choices) == {
        "demo",
        "run",
        "source",
        "source-status",
        "analyze-live-data",
        "validate",
        "email-ack",
    }
