"""Deterministic and mandatory cross-vendor semantic validation."""

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
        )


def deterministic_proposal_errors(
    proposal: Dict[str, Any],
    claims: Iterable[Dict[str, Any]],
    facts_block: Dict[str, Any],
    strategy: Dict[str, Any],
) -> List[str]:
    errors: List[str] = []
    if proposal.get("execution_eligible") is True:
        errors.append("PRODUCER_SET_EXECUTION_ELIGIBLE")
    claim_map = {claim["claim_id"]: claim for claim in claims}
    fact_map = facts_by_id(facts_block)
    cited_facts: List[Dict[str, Any]] = []
    for claim_id in proposal.get("evidence_claim_ids", []):
        claim = claim_map.get(claim_id)
        if claim is None:
            errors.append("UNKNOWN_CLAIM_ID:%s" % claim_id)
            continue
        for fact_id in claim.get("supporting_fact_ids", []):
            fact = fact_map.get(fact_id)
            if fact is None:
                errors.append("UNKNOWN_FACT_ID:%s" % fact_id)
            else:
                cited_facts.append(fact)
    signal_type = proposal.get("signal_type")
    actionable = signal_type in set(strategy.get("actionable_signal_types", []))
    if actionable:
        required = set(strategy.get("required_fact_types", {}).get(signal_type, []))
        observed = {fact["fact_type"] for fact in cited_facts}
        for missing in sorted(required - observed):
            errors.append("MISSING_REQUIRED_FACT_TYPE:%s" % missing)
        if proposal.get("stop_price") is None:
            errors.append("MISSING_STOP")
        if proposal.get("target_price") is None:
            errors.append("MISSING_TARGET")
        if not proposal.get("thesis_invalidation"):
            errors.append("MISSING_THESIS_INVALIDATION")
        if cited_facts and all(fact["fact_type"] == "social_post" for fact in cited_facts):
            errors.append("SOCIAL_ONLY_ACTIONABLE_EVIDENCE")
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
        proposal: Dict[str, Any],
        claims: Iterable[Dict[str, Any]],
        facts_block: Dict[str, Any],
        producer: JsonModelClient,
    ) -> SemanticValidationOutcome:
        separated = producer.vendor_id != self.validator.vendor_id
        if not separated:
            verdict = {
                "schema_version": "1.0.0",
                "target_id": proposal["signal_id"],
                "verdict": "indeterminate",
                "supported_claim_ids": [],
                "unsupported_claim_ids": list(proposal.get("evidence_claim_ids", [])),
                "reason_codes": ["VENDOR_MATCH"],
                "summary": "Producer and validator vendors match.",
            }
            return self._outcome(proposal, producer, verdict, False, "unavailable")
        claim_map = {claim["claim_id"]: claim for claim in claims}
        selected_claims = [
            claim_map[claim_id]
            for claim_id in proposal.get("evidence_claim_ids", [])
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
                    "target": proposal,
                    "claims": selected_claims,
                    "evidence": evidence,
                },
                self.registry.get("semantic_verdict"),
            )
            self.registry.validate("semantic_verdict", verdict)
            if verdict["target_id"] != proposal["signal_id"]:
                verdict["verdict"] = "indeterminate"
                verdict["reason_codes"].append("TARGET_ID_MISMATCH")
            report_verdict = verdict["verdict"]
        except Exception as exc:
            verdict = {
                "schema_version": "1.0.0",
                "target_id": proposal["signal_id"],
                "verdict": "indeterminate",
                "supported_claim_ids": [],
                "unsupported_claim_ids": list(proposal.get("evidence_claim_ids", [])),
                "reason_codes": ["VALIDATOR_UNAVAILABLE"],
                "summary": "Independent validator unavailable: %s"
                % exc.__class__.__name__,
            }
            report_verdict = "unavailable"
        return self._outcome(proposal, producer, verdict, True, report_verdict)

    def _outcome(
        self,
        proposal: Dict[str, Any],
        producer: JsonModelClient,
        verdict: Dict[str, Any],
        separated: bool,
        report_verdict: str,
    ) -> SemanticValidationOutcome:
        report = {
            "schema_version": "1.0.0",
            "target_id": proposal["signal_id"],
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
