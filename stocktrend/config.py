"""Load and enforce repository policy configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from .contracts import SchemaRegistry
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
    universe: Dict[str, Any]

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
            universe=_load_yaml(spec / "universe.yaml"),
        )
        SchemaRegistry(root / "schemas").validate("universe", bundle.universe)
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
        production = self.sources.get("production", {})
        approved_adapter = production.get("approved_adapter")
        adapters = self.sources.get("adapters", {})
        if approved_adapter not in adapters:
            raise ConfigurationError("approved source adapter is not configured")
        for name, adapter in adapters.items():
            if adapter.get("credential_scope") != "read_only":
                raise SafetyViolation(
                    "source adapter %s must use read-only credentials" % name
                )
            if adapter.get("redirects_allowed") is not False:
                raise SafetyViolation(
                    "source adapter %s must reject redirects" % name
                )
        source_policies = self.sources.get("sources", {})
        for name in ("news", "industry", "social"):
            source = source_policies.get(name, {})
            if source.get("enabled") is not True:
                continue
            if source.get("content_is_untrusted") is not True:
                raise SafetyViolation(
                    "enabled %s content must remain untrusted" % name
                )
            extraction = source.get("extraction", {})
            if name in ("news", "industry"):
                if extraction.get("method") != "deterministic_html":
                    raise ConfigurationError(
                        "%s public-web extraction must be deterministic" % name
                    )
                if extraction.get("llm_fallback_enabled") is not False:
                    raise SafetyViolation(
                        "%s public-web LLM fallback must remain disabled" % name
                    )
                if extraction.get("model") is not None:
                    raise SafetyViolation(
                        "%s public-web extraction must not configure a model" % name
                    )
            if name == "news" and not source.get("user_agent_env"):
                raise ConfigurationError(
                    "news public-web source must declare a user-agent environment variable"
                )
            if name == "social" and source.get("official_api_required") is not True:
                raise SafetyViolation("social sourcing requires an official API")
        required_buckets = self.universe.get("required_buckets", [])
        if len(required_buckets) != len(set(required_buckets)):
            raise ConfigurationError("universe required buckets must be unique")
        configured_buckets = {
            item.get("bucket")
            for item in self.universe.get("instruments", [])
            if item.get("active") is True
        }
        missing_buckets = set(required_buckets) - configured_buckets
        if missing_buckets:
            raise ConfigurationError(
                "universe is missing required buckets: %s"
                % ", ".join(sorted(missing_buckets))
            )
        symbols = [
            item.get("symbol") for item in self.universe.get("instruments", [])
        ]
        if len(symbols) != len(set(symbols)):
            raise ConfigurationError("universe symbols must be unique")

    @property
    def actionable_signals(self) -> set:
        return set(self.strategy.get("actionable_signal_types", []))
