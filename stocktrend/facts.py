"""Point-in-time fact normalization and deterministic identifiers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List

from .errors import ContractError
from .security import validate_public_https_url
from .util import parse_datetime, sha256_json


class FactsBuilder:
    def build(
        self,
        run_id: str,
        observations: Iterable[Dict[str, Any]],
        as_of: str,
    ) -> Dict[str, Any]:
        as_of_time = parse_datetime(as_of)
        facts: List[Dict[str, Any]] = []
        seen = set()
        for observation in observations:
            fact = self._normalize(observation, as_of_time)
            if fact["fact_id"] not in seen:
                facts.append(fact)
                seen.add(fact["fact_id"])
        if not facts:
            raise ContractError("facts block cannot be empty")
        facts.sort(key=lambda item: (item["symbol"], item["fact_type"], item["observed_at"]))
        return {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "as_of": as_of,
            "facts": facts,
        }

    def _normalize(
        self,
        observation: Dict[str, Any],
        as_of: datetime,
    ) -> Dict[str, Any]:
        required = [
            "fact_type",
            "instrument_id",
            "symbol",
            "venue",
            "observed_at",
            "retrieved_at",
            "value",
            "source",
        ]
        missing = [key for key in required if key not in observation]
        if missing:
            raise ContractError("observation missing fields: %s" % ", ".join(missing))
        observed_at = parse_datetime(observation["observed_at"])
        retrieved_at = parse_datetime(observation["retrieved_at"])
        if observed_at > as_of or retrieved_at > as_of:
            raise ContractError("future data violates point-in-time boundary")
        canonical_observation = dict(observation)
        raw_hash = sha256_json(canonical_observation)
        fact_id = "fact_%s" % raw_hash[:24]
        source = dict(observation["source"])
        source.setdefault("url", None)
        validate_public_https_url(source["url"])
        source.setdefault("license_class", "internal")
        source.setdefault("adapter_version", "1.0.0")
        trust = dict(observation.get("trust", {}))
        trust.setdefault("provenance_class", "unknown")
        trust.setdefault("extraction_method", "deterministic")
        trust.setdefault("corroboration_state", "not_applicable")
        return {
            "schema_version": "1.0.0",
            "fact_id": fact_id,
            "fact_type": observation["fact_type"],
            "instrument_id": observation["instrument_id"],
            "symbol": str(observation["symbol"]).upper(),
            "venue": observation["venue"],
            "observed_at": observation["observed_at"],
            "effective_at": observation.get("effective_at"),
            "retrieved_at": observation["retrieved_at"],
            "market_session": observation.get("market_session", "regular"),
            "value": observation["value"],
            "unit": observation.get("unit"),
            "currency": observation.get("currency"),
            "scale": observation.get("scale", 1.0),
            "precision": observation.get("precision", 4),
            "adjustment_status": observation.get("adjustment_status", "not_applicable"),
            "corporate_action_ref": observation.get("corporate_action_ref"),
            "source": source,
            "trust": trust,
            "raw_hash": raw_hash,
            "revision_status": observation.get("revision_status", "current"),
        }


def facts_by_id(facts_block: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {fact["fact_id"]: fact for fact in facts_block["facts"]}
