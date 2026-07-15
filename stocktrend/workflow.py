"""Analysis workflow orchestration with fail-closed cross-vendor validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import ConfigBundle
from .contracts import SchemaRegistry
from .errors import ContractError
from .facts import FactsBuilder
from .outbox import PublicationOutbox
from .providers import JsonModelClient
from .rendering import artifact_qa, render_digest
from .screening import screen_candidates
from .state import RunIdentity, RunStore
from .util import load_json, sha256_json
from .validation import (
    CrossVendorSemanticValidator,
    deterministic_proposal_errors,
)


class AnalysisWorkflow:
    def __init__(
        self,
        root: Path,
        producer: JsonModelClient,
        validator: JsonModelClient,
    ):
        self.root = root
        self.config = ConfigBundle.load(root)
        self.registry = SchemaRegistry(root / "schemas")
        self.store = RunStore(
            root,
            lease_seconds=int(
                self.config.workflow.get("state", {}).get("lease_seconds", 300)
            ),
        )
        self.producer = producer
        self.validator_client = validator
        self.prompts = {
            name: (root / "prompts" / name / "v1.md").read_text(encoding="utf-8")
            for name in (
                "market_context",
                "stock_analyst",
                "synthesizer",
                "semantic_validator",
            )
        }
        self.semantic_validator = CrossVendorSemanticValidator(
            self.registry,
            validator,
            self.prompts["semantic_validator"],
        )

    def run(
        self,
        input_document: Dict[str, Any],
        execution_mode: str = "analysis_only",
        run_revision: int = 1,
    ) -> Dict[str, Any]:
        self._validate_input(input_document)
        session_date = input_document["session_date"]
        as_of = input_document["as_of"]
        identity = RunIdentity(
            workflow_version=str(self.config.workflow["workflow_version"]),
            strategy_id=self.config.strategy["strategy_id"],
            strategy_version=self.config.strategy["strategy_version"],
            venue=input_document.get(
                "reference_venue", self.config.workflow["reference_market"]
            ),
            exchange_session_date=session_date,
            analysis_window=input_document.get("analysis_window", "close"),
            execution_mode=execution_mode,
        )
        versions = {
            "input_hash": sha256_json(input_document),
            "implementation_hash": sha256_json(
                {
                    str(path.relative_to(self.root)): path.read_text(encoding="utf-8")
                    for path in sorted((self.root / "stocktrend").glob("*.py"))
                }
            ),
            "schemas": "1.0.0",
            "schema_bundle_hash": sha256_json(
                {name: self.registry.get(name) for name in self.registry.names()}
            ),
            "strategy": self.config.strategy["strategy_version"],
            "strategy_hash": sha256_json(self.config.strategy),
            "risk_policy_hash": sha256_json(self.config.risk),
            "approval_policy_hash": sha256_json(self.config.approval),
            "tier_policy_hash": sha256_json(self.config.tiers),
            "workflow": str(self.config.workflow["workflow_version"]),
            "producer_vendor": self.producer.vendor_id,
            "producer_model": self.producer.model,
            "validator_vendor": self.validator_client.vendor_id,
            "validator_model": self.validator_client.model,
            "prompt": "v1",
            "prompt_bundle_hash": sha256_json(self.prompts),
        }
        manifest = self.store.create_or_resume(identity, versions, run_revision)
        run_id = manifest["run_id"]
        if manifest["state"] == "finalized":
            return self._result(run_id)
        if manifest["state"] == "failed":
            raise ContractError("cannot resume failed run without a new revision")
        with self.store.lock_for(identity.logical_key()):
            try:
                self._run_stages(run_id, input_document, as_of, session_date)
            except Exception:
                current = self.store.load_manifest(run_id)
                if current["state"] not in ("failed", "finalized"):
                    self.store.transition(run_id, "failed")
                raise
        return self._result(run_id)

    def _run_stages(
        self,
        run_id: str,
        input_document: Dict[str, Any],
        as_of: str,
        session_date: str,
    ) -> None:
        manifest = self.store.load_manifest(run_id)
        if manifest["state"] == "created":
            self.store.transition(run_id, "ingesting")
            self.store.write_json(run_id, "inputs/observations.json", input_document)
            manifest = self.store.load_manifest(run_id)

        if manifest["state"] == "ingesting":
            facts_block = FactsBuilder().build(
                run_id,
                input_document["observations"],
                as_of,
            )
            for fact in facts_block["facts"]:
                self.registry.validate("fact", fact)
            self.registry.validate("facts_block", facts_block)
            self.store.write_json(run_id, "normalized/facts_block.json", facts_block)
            self.store.transition(run_id, "normalized")
            manifest = self.store.load_manifest(run_id)
        else:
            facts_block = load_json(
                self.store.run_dir(run_id) / "normalized" / "facts_block.json"
            )

        if manifest["state"] == "normalized":
            candidates = screen_candidates(facts_block, self.config.strategy)
            self.store.write_json(
                run_id,
                "screen/candidates.json",
                {"schema_version": "1.0.0", "candidates": candidates},
            )
            self.store.transition(run_id, "screened")
            manifest = self.store.load_manifest(run_id)
        else:
            candidates = load_json(
                self.store.run_dir(run_id) / "screen" / "candidates.json"
            )["candidates"]

        if manifest["state"] == "screened":
            context, analysts, proposals = self._analyze(
                facts_block,
                candidates,
                as_of,
            )
            self.store.write_json(run_id, "analysis/cycle_context.json", context)
            self.store.write_json(
                run_id,
                "analysis/analyst_outputs.json",
                {"schema_version": "1.0.0", "analyst_outputs": analysts},
            )
            self.store.write_json(
                run_id,
                "analysis/raw_signal_proposals.json",
                {"schema_version": "1.0.0", "signal_proposals": proposals},
            )
            self.store.transition(run_id, "analyzed")
            manifest = self.store.load_manifest(run_id)
        else:
            context = load_json(
                self.store.run_dir(run_id) / "analysis" / "cycle_context.json"
            )
            analysts = load_json(
                self.store.run_dir(run_id) / "analysis" / "analyst_outputs.json"
            )["analyst_outputs"]
            proposals = load_json(
                self.store.run_dir(run_id) / "analysis" / "raw_signal_proposals.json"
            )["signal_proposals"]

        if manifest["state"] == "analyzed":
            claims = [
                claim for analyst in analysts for claim in analyst.get("claims", [])
            ]
            validated, reports, verdicts = self._validate_proposals(
                proposals,
                claims,
                facts_block,
                run_id,
            )
            self.store.write_json(
                run_id,
                "validation/signal_proposals.json",
                {"schema_version": "1.0.0", "signal_proposals": validated},
            )
            self.store.write_json(
                run_id,
                "validation/reports.json",
                {"schema_version": "1.0.0", "reports": reports},
            )
            self.store.write_json(
                run_id,
                "validation/verdicts.json",
                {"schema_version": "1.0.0", "verdicts": verdicts},
            )
            self.store.transition(run_id, "validated")
            manifest = self.store.load_manifest(run_id)
        else:
            validated = load_json(
                self.store.run_dir(run_id) / "validation" / "signal_proposals.json"
            )["signal_proposals"]
            reports = load_json(
                self.store.run_dir(run_id) / "validation" / "reports.json"
            )["reports"]
            claims = [claim for analyst in analysts for claim in analyst["claims"]]

        if manifest["state"] == "validated":
            degraded = self.store.load_manifest(run_id)["degraded_reasons"]
            digest = render_digest(
                self.root / "templates" / "digest.md",
                run_id,
                session_date,
                context,
                validated,
                reports,
                degraded,
            )
            artifact_qa(
                digest,
                validated,
                {claim["claim_id"] for claim in claims},
            )
            self.store.write_text(run_id, "rendered/digest.md", digest)
            self.store.transition(run_id, "rendered")
            manifest = self.store.load_manifest(run_id)

        if manifest["state"] == "rendered":
            self.store.transition(run_id, "finalized")
            finalized = self.store.load_manifest(run_id)
            self.registry.validate("run_manifest", finalized)
            digest_path = self.store.run_dir(run_id) / "rendered" / "digest.md"
            digest_hash = finalized["artifact_hashes"]["rendered/digest.md"]
            outbox = PublicationOutbox(self.root, self.registry)
            item = outbox.enqueue_artifact(run_id, digest_path, digest_hash)
            outbox.publish_artifact(item)

    def _analyze(
        self,
        facts_block: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        as_of: str,
    ) -> tuple:
        context = self.producer.generate_json(
            "market_context",
            self.prompts["market_context"],
            {
                "external_content_policy": "untrusted_data_only",
                "facts_block": facts_block,
            },
            self.registry.get("cycle_context"),
        )
        context["producer"] = self._producer_metadata()
        self.registry.validate("cycle_context", context)
        analysts = []
        for candidate in candidates:
            related_facts = [
                fact
                for fact in facts_block["facts"]
                if fact["instrument_id"] == candidate["instrument_id"]
            ]
            analyst = self.producer.generate_json(
                "stock_analyst",
                self.prompts["stock_analyst"],
                {
                    "external_content_policy": "untrusted_data_only",
                    "candidate": candidate,
                    "facts": related_facts,
                    "cycle_context": context,
                    "strategy": self.config.strategy,
                },
                self.registry.get("analyst_output"),
            )
            analyst["producer"] = self._producer_metadata()
            self.registry.validate("analyst_output", analyst)
            analysts.append(analyst)
        proposals: List[Dict[str, Any]] = []
        if analysts:
            signal_schema = self.registry.get("signal_proposal")
            synthesis_schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["proposals"],
                "properties": {
                    "proposals": {
                        "type": "array",
                        "items": signal_schema,
                    }
                },
            }
            synthesis = self.producer.generate_json(
                "synthesizer",
                self.prompts["synthesizer"],
                {
                    "as_of": as_of,
                    "analyst_outputs": analysts,
                    "cycle_context": context,
                    "strategy": self.config.strategy,
                    "candidate_venues": {
                        candidate["symbol"]: candidate["venue"]
                        for candidate in candidates
                    },
                },
                synthesis_schema,
            )
            proposals = synthesis["proposals"]
        analysis_ids = {item["analyst_output_id"] for item in analysts}
        for proposal in proposals:
            producer = proposal.setdefault("producer", {})
            producer.update(self._producer_metadata())
            producer["analyst_output_ids"] = [
                value
                for value in producer.get("analyst_output_ids", [])
                if value in analysis_ids
            ]
            self.registry.validate("signal_proposal", proposal)
        return context, analysts, proposals

    def _validate_proposals(
        self,
        proposals: List[Dict[str, Any]],
        claims: List[Dict[str, Any]],
        facts_block: Dict[str, Any],
        run_id: str,
    ) -> tuple:
        validated = []
        reports = []
        verdicts = []
        for proposal in proposals:
            errors = deterministic_proposal_errors(
                proposal,
                claims,
                facts_block,
                self.config.strategy,
            )
            proposal["execution_eligible"] = False
            proposal["eligibility_reasons"] = errors or ["PENDING_SEMANTIC_VALIDATION"]
            if proposal["signal_type"] in self.config.actionable_signals:
                outcome = self.semantic_validator.validate(
                    proposal,
                    claims,
                    facts_block,
                    self.producer,
                )
                reports.append(outcome.report)
                verdicts.append(outcome.verdict)
                if outcome.passed and not errors:
                    proposal["execution_eligible"] = True
                    proposal["eligibility_reasons"] = ["ALL_VALIDATION_GATES_PASSED"]
                else:
                    reason_codes = list(outcome.report["reason_codes"])
                    proposal["eligibility_reasons"] = sorted(
                        set(errors + reason_codes + ["RESEARCH_ONLY"])
                    )
                    if outcome.report["verdict"] == "unavailable":
                        self.store.add_degraded_reason(
                            run_id,
                            "INDEPENDENT_VALIDATOR_UNAVAILABLE",
                        )
                    if not outcome.report["vendor_separation"]:
                        self.store.add_degraded_reason(
                            run_id,
                            "VALIDATOR_VENDOR_MATCH",
                        )
            else:
                proposal["eligibility_reasons"] = ["NON_ACTIONABLE_SIGNAL"]
            self.registry.validate("signal_proposal", proposal)
            validated.append(proposal)
        return validated, reports, verdicts

    def _producer_metadata(self) -> Dict[str, str]:
        return {
            "vendor": self.producer.vendor_id,
            "model": self.producer.model,
            "prompt_version": "v1",
        }

    def _result(self, run_id: str) -> Dict[str, Any]:
        manifest = self.store.load_manifest(run_id)
        return {
            "run_id": run_id,
            "manifest": manifest,
            "run_directory": str(self.store.run_dir(run_id)),
            "digest": str(self.root / "artifacts" / run_id / "digest.md"),
        }

    @staticmethod
    def _validate_input(document: Dict[str, Any]) -> None:
        required = ["session_date", "as_of", "observations"]
        missing = [name for name in required if name not in document]
        if missing:
            raise ContractError("input missing fields: %s" % ", ".join(missing))
        if not isinstance(document["observations"], list):
            raise ContractError("observations must be an array")
