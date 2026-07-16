"""Deterministic and cross-vendor validation for research signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from .contracts import SchemaRegistry
from .facts import facts_by_id
from .providers import JsonModelClient
from .util import utc_now_iso


@dataclass
class SemanticValidationOutcome:
    report: Dict[str, Any]
    verdict: Dict[str, Any]

    @property
    def passed(self) -> bool:
        return (
            self.report["vendor_separation"] is True
            and self.report["verdict"] == "pass"
            and self.verdict.get("verdict") == "pass"
            and not self.verdict.get("unsupported_claim_ids")
            and not self.verdict.get("reason_codes")
        )


def deterministic_research_signal_errors(
    signal: Dict[str, Any],
    claims: Iterable[Dict[str, Any]],
    facts_block: Dict[str, Any],
    strategy: Dict[str, Any],
) -> List[str]:
    errors: List[str] = []
    if signal.get("research_only") is not True:
        errors.append("RESEARCH_ONLY_BOUNDARY_VIOLATION")
    if signal.get("strategy_id") != strategy.get("strategy_id"):
        errors.append("STRATEGY_ID_MISMATCH")
    if signal.get("strategy_version") != strategy.get("strategy_version"):
        errors.append("STRATEGY_VERSION_MISMATCH")
    assessment = signal.get("assessment")
    if assessment not in set(strategy.get("assessments", [])):
        errors.append("ASSESSMENT_NOT_ALLOWED")
    horizon = signal.get("horizon_sessions")
    limits = strategy.get("research_horizon_sessions", {})
    if not isinstance(horizon, int) or not (
        int(limits.get("minimum", 1))
        <= horizon
        <= int(limits.get("maximum", 30))
    ):
        errors.append("RESEARCH_HORIZON_OUT_OF_POLICY")

    configured_outlooks = strategy.get("outlook_horizons", {})
    expected_outlooks = {
        key: int(value) for key, value in configured_outlooks.items()
    }
    outlooks = signal.get("horizon_outlooks", [])
    observed_outlook_keys = [item.get("horizon_key") for item in outlooks]
    if len(observed_outlook_keys) != len(set(observed_outlook_keys)):
        errors.append("DUPLICATE_OUTLOOK_HORIZON")
    if set(observed_outlook_keys) != set(expected_outlooks):
        errors.append("OUTLOOK_HORIZONS_INCOMPLETE")
    signal_claim_ids = set(signal.get("evidence_claim_ids", []))
    for outlook in outlooks:
        key = outlook.get("horizon_key")
        if key in expected_outlooks and outlook.get("horizon_sessions") != expected_outlooks[key]:
            errors.append("OUTLOOK_HORIZON_SESSION_MISMATCH:%s" % key)
        for claim_id in outlook.get("supporting_claim_ids", []):
            if claim_id not in signal_claim_ids:
                errors.append("OUTLOOK_CLAIM_NOT_CITED:%s" % claim_id)

    claim_items = list(claims)
    claim_ids = [claim["claim_id"] for claim in claim_items]
    if len(claim_ids) != len(set(claim_ids)):
        errors.append("DUPLICATE_CLAIM_ID")
    claim_map = {claim["claim_id"]: claim for claim in claim_items}
    fact_map = facts_by_id(facts_block)
    cited_facts: List[Dict[str, Any]] = []
    for claim_id in signal.get("evidence_claim_ids", []):
        claim = claim_map.get(claim_id)
        if claim is None:
            errors.append("UNKNOWN_CLAIM_ID:%s" % claim_id)
            continue
        for fact_id in claim.get("supporting_fact_ids", []):
            fact = fact_map.get(fact_id)
            if fact is None:
                errors.append("UNKNOWN_FACT_ID:%s" % fact_id)
                continue
            if fact.get("instrument_id") != signal.get("instrument_id"):
                errors.append("CROSS_INSTRUMENT_EVIDENCE:%s" % fact_id)
            cited_facts.append(fact)

    required = set(strategy.get("required_fact_types", {}).get(assessment, []))
    observed = {fact["fact_type"] for fact in cited_facts}
    for missing in sorted(required - observed):
        errors.append("MISSING_REQUIRED_FACT_TYPE:%s" % missing)
    return sorted(set(errors))


class CrossVendorSemanticValidator:
    def __init__(
        self,
        registry: SchemaRegistry,
        validator: JsonModelClient,
        prompt: str,
    ):
        self.registry = registry
        self.validator = validator
        self.prompt = prompt

    def validate(
        self,
        signal: Dict[str, Any],
        claims: Iterable[Dict[str, Any]],
        facts_block: Dict[str, Any],
        producer: JsonModelClient,
    ) -> SemanticValidationOutcome:
        target_id = signal["research_signal_id"]
        target_claim_ids = set(signal.get("evidence_claim_ids", []))
        separated = producer.vendor_id != self.validator.vendor_id
        if not separated:
            verdict = {
                "schema_version": "1.0.0",
                "target_id": target_id,
                "verdict": "indeterminate",
                "supported_claim_ids": [],
                "unsupported_claim_ids": sorted(target_claim_ids),
                "reason_codes": ["VENDOR_MATCH"],
                "summary": "Producer and validator vendors match.",
            }
            return self._outcome(signal, producer, verdict, False, "unavailable")

        claim_map = {claim["claim_id"]: claim for claim in claims}
        selected_claims = [
            claim_map[claim_id]
            for claim_id in signal.get("evidence_claim_ids", [])
            if claim_id in claim_map
        ]
        fact_map = facts_by_id(facts_block)
        selected_fact_ids = {
            fact_id
            for claim in selected_claims
            for fact_id in claim.get("supporting_fact_ids", [])
        }
        evidence = [
            {
                "fact_id": fact["fact_id"],
                "fact_type": fact["fact_type"],
                "symbol": fact["symbol"],
                "observed_at": fact["observed_at"],
                "value": fact["value"],
                "unit": fact["unit"],
                "currency": fact["currency"],
                "source_provider": fact["source"]["provider"],
                "provenance_class": fact["trust"]["provenance_class"],
            }
            for fact_id, fact in fact_map.items()
            if fact_id in selected_fact_ids
        ]
        try:
            verdict = self.validator.generate_json(
                "semantic_validator",
                self.prompt,
                {
                    "evidence_boundary": "untrusted_data_only",
                    "task_specification": {
                        "require_direct_support": True,
                        "same_ticker_is_not_corroboration": True,
                    },
                    "target": signal,
                    "claims": selected_claims,
                    "evidence": evidence,
                },
                self.registry.get("semantic_verdict"),
            )
            self.registry.validate("semantic_verdict", verdict)
            supported = set(verdict["supported_claim_ids"])
            unsupported = set(verdict["unsupported_claim_ids"])
            coverage_valid = (
                not (supported & unsupported)
                and supported | unsupported == target_claim_ids
            )
            pass_consistent = (
                verdict["verdict"] != "pass"
                or (not unsupported and not verdict["reason_codes"])
            )
            if verdict["target_id"] != target_id:
                verdict["verdict"] = "indeterminate"
                verdict["reason_codes"].append("TARGET_ID_MISMATCH")
            elif not coverage_valid or not pass_consistent:
                verdict["verdict"] = "indeterminate"
                verdict["reason_codes"].append(
                    "VERDICT_CLAIM_COVERAGE_MISMATCH"
                )
            verdict["reason_codes"] = sorted(set(verdict["reason_codes"]))
            report_verdict = verdict["verdict"]
        except Exception as exc:
            verdict = {
                "schema_version": "1.0.0",
                "target_id": target_id,
                "verdict": "indeterminate",
                "supported_claim_ids": [],
                "unsupported_claim_ids": sorted(target_claim_ids),
                "reason_codes": ["VALIDATOR_UNAVAILABLE"],
                "summary": "Independent validator unavailable: %s"
                % exc.__class__.__name__,
            }
            report_verdict = "unavailable"
        return self._outcome(signal, producer, verdict, True, report_verdict)

    def _outcome(
        self,
        signal: Dict[str, Any],
        producer: JsonModelClient,
        verdict: Dict[str, Any],
        separated: bool,
        report_verdict: str,
    ) -> SemanticValidationOutcome:
        report = {
            "schema_version": "1.0.0",
            "target_id": signal["research_signal_id"],
            "producer": {"vendor": producer.vendor_id, "model": producer.model},
            "validator": {
                "vendor": self.validator.vendor_id,
                "model": self.validator.model,
            },
            "vendor_separation": separated,
            "verdict": report_verdict,
            "reason_codes": list(verdict.get("reason_codes", [])),
            "validated_at": utc_now_iso(),
        }
        self.registry.validate("validation_report", report)
        return SemanticValidationOutcome(report=report, verdict=verdict)
