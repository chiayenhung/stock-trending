"""Load and enforce repository policy configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from .errors import ConfigurationError, SafetyViolation


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigurationError("missing configuration: %s" % path)
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ConfigurationError("configuration must be an object: %s" % path)
    return value


@dataclass(frozen=True)
class ConfigBundle:
    root: Path
    workflow: Dict[str, Any]
    strategy: Dict[str, Any]
    risk: Dict[str, Any]
    approval: Dict[str, Any]
    evaluation: Dict[str, Any]
    tiers: Dict[str, Any]
    sources: Dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> "ConfigBundle":
        spec = root / "spec"
        bundle = cls(
            root=root,
            workflow=_load_yaml(spec / "workflow.yaml"),
            strategy=_load_yaml(spec / "strategy.yaml"),
            risk=_load_yaml(spec / "risk-policy.yaml"),
            approval=_load_yaml(spec / "approval-policy.yaml"),
            evaluation=_load_yaml(spec / "evaluation-policy.yaml"),
            tiers=_load_yaml(spec / "tiers.yaml"),
            sources=_load_yaml(spec / "sources.yaml"),
        )
        bundle.enforce_safety()
        return bundle

    def enforce_safety(self) -> None:
        if self.workflow.get("live_enabled") is not False:
            raise SafetyViolation("workflow live_enabled must remain false")
        if self.risk.get("live_enabled") is not False:
            raise SafetyViolation("risk policy live_enabled must remain false")
        if self.risk.get("approved_for_live") is not False:
            raise SafetyViolation("risk policy approved_for_live must remain false")
        validation = self.tiers.get("validation", {})
        if validation.get("require_different_vendor") is not True:
            raise ConfigurationError("different-vendor semantic validation is required")
        if validation.get("unavailable_policy") != "research_only":
            raise ConfigurationError("validator unavailable policy must be research_only")

    @property
    def actionable_signals(self) -> set:
        return set(self.strategy.get("actionable_signal_types", []))
