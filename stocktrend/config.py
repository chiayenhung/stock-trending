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
            evaluation=_load_yaml(spec / "evaluation-policy.yaml"),
            tiers=_load_yaml(spec / "tiers.yaml"),
            sources=_load_yaml(spec / "sources.yaml"),
            universe=_load_yaml(spec / "universe.yaml"),
        )
        SchemaRegistry(root / "schemas").validate("universe", bundle.universe)
        bundle.enforce_safety()
        return bundle

    def enforce_safety(self) -> None:
        if self.workflow.get("scope") != "research_only":
            raise SafetyViolation("workflow scope must remain research_only")
        if self.strategy.get("status") != "research_only":
            raise SafetyViolation("strategy status must remain research_only")
        required_outlooks = {
            "short_5d": 5,
            "medium_1m": 21,
            "cycle_3m": 63,
        }
        if self.strategy.get("outlook_horizons") != required_outlooks:
            raise ConfigurationError(
                "strategy must configure exact 5-, 21-, and 63-session outlooks"
            )
        if (
            self.strategy.get("outlook_probability_basis")
            != "model_estimate_uncalibrated"
        ):
            raise SafetyViolation(
                "outlook probabilities must remain explicitly uncalibrated"
            )
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
            if name in ("news", "industry", "social"):
                expected_method = (
                    "deterministic_browser_capture"
                    if name == "social"
                    else "deterministic_html"
                )
                if extraction.get("method") != expected_method:
                    raise ConfigurationError(
                        "%s extraction must use %s" % (name, expected_method)
                    )
                if extraction.get("llm_fallback_enabled") is not False:
                    raise SafetyViolation(
                        "%s enrichment LLM fallback must remain disabled" % name
                    )
                if extraction.get("model") is not None:
                    raise SafetyViolation(
                        "%s enrichment must not configure a model" % name
                    )
            if name == "news" and not source.get("user_agent_env"):
                raise ConfigurationError(
                    "news public-web source must declare a user-agent environment variable"
                )
            if name == "social":
                if source.get("browser_capture_required") is not True:
                    raise SafetyViolation(
                        "social sourcing requires an explicit browser capture"
                    )
                if source.get("adapter") != "x_browser_snapshot":
                    raise ConfigurationError(
                        "enabled social source must use the X browser snapshot adapter"
                    )
                allowed_host = str(source.get("allowed_host", "")).lower().rstrip(".")
                allowlist = {
                    str(item).lower().rstrip(".")
                    for item in source.get("allowlist", [])
                }
                if allowlist and (
                    not allowed_host or allowed_host not in allowlist
                ):
                    raise SafetyViolation(
                        "social browser host must be explicitly allowlisted"
                    )
                if int(source.get("lookback_days", 0)) != 5:
                    raise ConfigurationError(
                        "X browser capture must use the configured five-day window"
                    )
                snapshot_path = str(source.get("snapshot_path", ""))
                if not snapshot_path.startswith("state/social/"):
                    raise SafetyViolation(
                        "social browser snapshots must stay in operational state"
                    )
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
