"""Analysis workflow orchestration with fail-closed cross-vendor validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .committer import ArtifactCommitter
from .config import ConfigBundle
from .contracts import SchemaRegistry
from .errors import ContractError
from .facts import FactsBuilder
from .notifications import (
    BatchEmailGenerator,
    CompletionEmailOutbox,
    configured_recipient,
    load_batch_email_requests,
)
from .providers import JsonModelClient
from .rendering import artifact_qa, render_digest
from .screening import screen_candidates, screening_coverage
from .state import RunIdentity, RunStore
from .util import load_json, sha256_json
from .validation import (
    CrossVendorSemanticValidator,
    deterministic_research_signal_errors,
)


class AnalysisWorkflow:
    PROMPT_VERSION = "v2"

    def __init__(
        self,
        root: Path,
        producer: JsonModelClient,
        validator: JsonModelClient,
        notification_recipient: Optional[str] = None,
        initial_degraded_reasons: Optional[List[str]] = None,
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
        self.initial_degraded_reasons = sorted(set(initial_degraded_reasons or []))
        self.notification_recipient = (
            configured_recipient(self.config.workflow)
            if notification_recipient is None
            else notification_recipient.strip()
        )
        self.prompts = {
            name: (
                root / "prompts" / name / ("%s.md" % self.PROMPT_VERSION)
            ).read_text(encoding="utf-8")
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
        )
        versions = {
            "input_hash": sha256_json(input_document),
            "implementation_hash": sha256_json(
                {
                    str(path.relative_to(self.root)): path.read_text(encoding="utf-8")
                    for path in sorted((self.root / "stocktrend").glob("*.py"))
                }
            ),
            "schemas": "2.0.0",
            "schema_bundle_hash": sha256_json(
                {name: self.registry.get(name) for name in self.registry.names()}
            ),
            "strategy": self.config.strategy["strategy_version"],
            "strategy_hash": sha256_json(self.config.strategy),
            "universe": self.config.universe["universe_version"],
            "universe_hash": sha256_json(self.config.universe),
            "tier_policy_hash": sha256_json(self.config.tiers),
            "workflow": str(self.config.workflow["workflow_version"]),
            "workflow_hash": sha256_json(self.config.workflow),
            "notification_recipient_hash": sha256_json(
                {"recipient": self.notification_recipient}
            ),
            "producer_vendor": self.producer.vendor_id,
            "producer_model": self.producer.model,
            "validator_vendor": self.validator_client.vendor_id,
            "validator_model": self.validator_client.model,
            "prompt": self.PROMPT_VERSION,
            "prompt_bundle_hash": sha256_json(self.prompts),
            "initial_degraded_reasons": self.initial_degraded_reasons,
        }
        manifest = self.store.create_or_resume(identity, versions, run_revision)
        run_id = manifest["run_id"]
        for reason in self.initial_degraded_reasons:
            self.store.add_degraded_reason(run_id, reason)
        manifest = self.store.load_manifest(run_id)
        if manifest["state"] == "committed":
            return self._result(run_id)
        if manifest["state"] == "failed":
            raise ContractError("cannot resume failed run without a new revision")
        with self.store.lock_for(identity.logical_key()):
            try:
                self._run_stages(run_id, input_document, as_of, session_date)
            except Exception:
                current = self.store.load_manifest(run_id)
                if current["state"] not in ("failed", "committed"):
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
            source_metadata = input_document.get("source_snapshot", {})
            candidates = screen_candidates(
                facts_block,
                self.config.strategy,
                source_metadata.get("instrument_buckets"),
            )
            coverage = screening_coverage(
                source_metadata.get("coverage"),
                candidates,
            )
            self.store.write_json(
                run_id,
                "screen/candidates.json",
                {"schema_version": "1.0.0", "candidates": candidates},
            )
            self.store.write_json(run_id, "screen/coverage.json", coverage)
            self.store.transition(run_id, "screened")
            manifest = self.store.load_manifest(run_id)
        else:
            candidates = load_json(
                self.store.run_dir(run_id) / "screen" / "candidates.json"
            )["candidates"]
            coverage = load_json(
                self.store.run_dir(run_id) / "screen" / "coverage.json"
            )

        if manifest["state"] == "screened":
            context, analysts, signals = self._analyze(
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
                "analysis/raw_research_signals.json",
                {"schema_version": "1.0.0", "research_signals": signals},
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
            signals = load_json(
                self.store.run_dir(run_id) / "analysis" / "raw_research_signals.json"
            )["research_signals"]

        if manifest["state"] == "analyzed":
            claims = [
                claim for analyst in analysts for claim in analyst.get("claims", [])
            ]
            validated, reports, verdicts = self._validate_research_signals(
                signals,
                claims,
                facts_block,
                run_id,
            )
            self.store.write_json(
                run_id,
                "validation/research_signals.json",
                {"schema_version": "1.0.0", "research_signals": validated},
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
                self.store.run_dir(run_id) / "validation" / "research_signals.json"
            )["research_signals"]
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
                coverage,
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
            batch_id = str(input_document.get("batch_id") or run_id)
            BatchEmailGenerator(self.root, self.store, self.registry).generate(
                run_id,
                batch_id,
                self.notification_recipient,
            )
            self.store.transition(run_id, "emails_generated")
            manifest = self.store.load_manifest(run_id)

        if manifest["state"] == "emails_generated":
            self.store.transition(run_id, "finalized")
            finalized = self.store.load_manifest(run_id)
            self.registry.validate("run_manifest", finalized)
            manifest = finalized

        if manifest["state"] == "finalized":
            ArtifactCommitter(self.root, self.store, self.registry).commit(
                run_id,
                [
                    "rendered/digest.md",
                    BatchEmailGenerator.ANALYSIS_BODY,
                    BatchEmailGenerator.LOGS_BODY,
                    BatchEmailGenerator.SYSTEM_LOG,
                ],
            )
            self.store.transition(run_id, "committed")
            self.registry.validate("run_manifest", self.store.load_manifest(run_id))
            CompletionEmailOutbox(self.root, self.registry).activate_for_committed_run(
                run_id
            )

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
        signals: List[Dict[str, Any]] = []
        if analysts:
            signal_schema = self.registry.get("research_signal")
            synthesis_schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["signals"],
                "properties": {
                    "signals": {
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
            signals = synthesis["signals"]
        analysis_ids = {item["analyst_output_id"] for item in analysts}
        analysis_scores = {
            item["analyst_output_id"]: float(item["score"])
            for item in analysts
        }
        for signal in signals:
            signal["research_only"] = True
            signal["validation_status"] = "pending"
            signal["validation_reason_codes"] = []
            producer = signal.setdefault("producer", {})
            producer.update(self._producer_metadata())
            producer["analyst_output_ids"] = [
                value
                for value in producer.get("analyst_output_ids", [])
                if value in analysis_ids
            ]
            if producer["analyst_output_ids"]:
                signal["signal_strength_score"] = max(
                    analysis_scores[value]
                    for value in producer["analyst_output_ids"]
                )
            self.registry.validate("research_signal", signal)
        return context, analysts, signals

    def _validate_research_signals(
        self,
        signals: List[Dict[str, Any]],
        claims: List[Dict[str, Any]],
        facts_block: Dict[str, Any],
        run_id: str,
    ) -> tuple:
        validated = []
        reports = []
        verdicts = []
        for signal in signals:
            errors = deterministic_research_signal_errors(
                signal,
                claims,
                facts_block,
                self.config.strategy,
            )
            signal["research_only"] = True
            outcome = self.semantic_validator.validate(
                signal,
                claims,
                facts_block,
                self.producer,
            )
            reports.append(outcome.report)
            verdicts.append(outcome.verdict)
            if outcome.passed and not errors:
                signal["validation_status"] = "pass"
                signal["validation_reason_codes"] = []
            else:
                signal["validation_status"] = (
                    "reject" if errors else outcome.report["verdict"]
                )
                signal["validation_reason_codes"] = sorted(
                    set(errors + list(outcome.report["reason_codes"]))
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
            self.registry.validate("research_signal", signal)
            validated.append(signal)
        return validated, reports, verdicts

    def _producer_metadata(self) -> Dict[str, str]:
        return {
            "vendor": self.producer.vendor_id,
            "model": self.producer.model,
            "prompt_version": self.PROMPT_VERSION,
        }

    def _result(self, run_id: str) -> Dict[str, Any]:
        manifest = self.store.load_manifest(run_id)
        if manifest["state"] == "committed":
            CompletionEmailOutbox(self.root, self.registry).activate_for_committed_run(
                run_id
            )
        artifact_dir = self.root / "artifacts" / run_id
        return {
            "run_id": run_id,
            "manifest": manifest,
            "run_directory": str(self.store.run_dir(run_id)),
            "digest": str(artifact_dir / "digest.md"),
            "trending_analysis_email": str(
                artifact_dir / Path(BatchEmailGenerator.ANALYSIS_BODY).name
            ),
            "system_logs_email": str(
                artifact_dir / Path(BatchEmailGenerator.LOGS_BODY).name
            ),
            "system_log": str(
                artifact_dir / Path(BatchEmailGenerator.SYSTEM_LOG).name
            ),
            "commit_receipt": str(
                self.root / "state" / "commits" / ("%s.json" % run_id)
            ),
            "screen_coverage": str(
                self.store.run_dir(run_id) / "screen" / "coverage.json"
            ),
            "notifications": load_batch_email_requests(self.root, run_id),
        }

    @staticmethod
    def _validate_input(document: Dict[str, Any]) -> None:
        required = ["session_date", "as_of", "observations"]
        missing = [name for name in required if name not in document]
        if missing:
            raise ContractError("input missing fields: %s" % ", ".join(missing))
        if not isinstance(document["observations"], list):
            raise ContractError("observations must be an array")
        batch_id = document.get("batch_id")
        if batch_id is not None and (
            not isinstance(batch_id, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", batch_id) is None
        ):
            raise ContractError(
                "batch_id must use 1-128 letters, digits, dot, underscore, colon, or hyphen"
            )
