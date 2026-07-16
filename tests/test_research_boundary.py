from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from stocktrend.cli import build_parser
from stocktrend.config import ConfigBundle
from stocktrend.contracts import SchemaRegistry
from stocktrend.errors import SafetyViolation


def test_configuration_and_contracts_are_research_only(project_root: Path) -> None:
    config = ConfigBundle.load(project_root)
    registry = SchemaRegistry(project_root / "schemas")
    assert config.workflow["scope"] == "research_only"
    assert config.strategy["status"] == "research_only"
    assert registry.get("research_signal")["properties"]["research_only"] == {
        "const": True
    }
    assert registry.get("research_signal")["properties"]["schema_version"] == {
        "const": "2.0.0"
    }
    assert config.strategy["outlook_horizons"] == {
        "short_5d": 5,
        "medium_1m": 21,
        "cycle_3m": 63,
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


def test_outlook_probability_basis_must_remain_uncalibrated(
    project_root: Path,
) -> None:
    strategy_path = project_root / "spec" / "strategy.yaml"
    strategy = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
    strategy["outlook_probability_basis"] = "historical_win_rate"
    strategy_path.write_text(
        yaml.safe_dump(strategy, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(SafetyViolation, match="explicitly uncalibrated"):
        ConfigBundle.load(project_root)
