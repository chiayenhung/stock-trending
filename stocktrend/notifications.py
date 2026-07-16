"""Completion summaries and durable email delivery requests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import SchemaRegistry
from .state import RunStore
from .util import (
    atomic_write_json,
    load_json,
    sha256_json,
    sha256_text,
    utc_now_iso,
)


def configured_recipient(workflow_config: Dict[str, Any]) -> str:
    return os.environ.get(
        "STOCKTREND_SUMMARY_EMAIL",
        str(
            workflow_config.get("notifications", {}).get(
                "summary_email_recipient", ""
            )
        ),
    ).strip()


def _proposal_summary(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "signal_id": item["signal_id"],
            "symbol": item["symbol"],
            "signal_type": item["signal_type"],
            "execution_eligible": item["execution_eligible"],
            "eligibility_reasons": item["eligibility_reasons"],
        }
        for item in proposals
    ]


class CompletionEmailOutbox:
    def __init__(self, root: Path, registry: SchemaRegistry):
        self.root = root
        self.registry = registry
        self.outbox_dir = root / "state" / "outbox"

    def enqueue(
        self,
        run_id: str,
        batch_id: str,
        email_kind: str,
        recipient: str,
        subject: str,
        body_path: Path,
        attachment_paths: List[Path],
    ) -> Dict[str, Any]:
        fingerprint = sha256_json(
            {
                "run_id": run_id,
                "batch_id": batch_id,
                "email_kind": email_kind,
                "recipient": recipient,
                "subject": subject,
                "body_hash": sha256_text(body_path.read_text(encoding="utf-8")),
                "attachment_hashes": [
                    sha256_text(path.read_text(encoding="utf-8"))
                    for path in attachment_paths
                ],
            }
        )
        operation_id = "email_%s_%s_%s" % (
            run_id,
            email_kind,
            fingerprint[:12],
        )
        item_path = self.outbox_dir / ("%s.json" % operation_id)
        if item_path.exists():
            return load_json(item_path)
        item = {
            "schema_version": "1.0.0",
            "operation_id": operation_id,
            "run_id": run_id,
            "batch_id": batch_id,
            "channel": "email",
            "email_kind": email_kind,
            "recipient": recipient,
            "subject": subject,
            "body_path": str(body_path),
            "attachment_paths": [str(path) for path in attachment_paths],
            "delivery_requires_state": "committed",
            "status": "blocked",
            "created_at": utc_now_iso(),
            "acknowledged_at": None,
            "provider_message_id": None,
        }
        self.registry.validate("email_delivery_request", item)
        atomic_write_json(item_path, item)
        return item

    def activate_for_committed_run(self, run_id: str) -> List[Dict[str, Any]]:
        manifest = load_json(
            self.root / "state" / "runs" / run_id / "manifest.json"
        )
        if manifest["state"] != "committed":
            raise ValueError("email delivery requires a committed run")
        activated = []
        for path in sorted(self.outbox_dir.glob("email_%s_*.json" % run_id)):
            item = load_json(path)
            if item.get("channel") != "email" or item.get("run_id") != run_id:
                continue
            if item["status"] == "blocked":
                item["status"] = "pending"
                self.registry.validate("email_delivery_request", item)
                atomic_write_json(path, item)
            activated.append(item)
        return activated

    def acknowledge(
        self,
        operation_id: str,
        provider_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item_path = self.outbox_dir / ("%s.json" % operation_id)
        item = load_json(item_path)
        if item["status"] == "acknowledged":
            return item
        manifest = load_json(
            self.root / "state" / "runs" / item["run_id"] / "manifest.json"
        )
        if manifest["state"] != item["delivery_requires_state"]:
            raise ValueError("email delivery acknowledgement is not yet allowed")
        if item["status"] != "pending":
            raise ValueError("email request is not pending delivery")
        item["status"] = "acknowledged"
        item["acknowledged_at"] = utc_now_iso()
        item["provider_message_id"] = provider_message_id
        self.registry.validate("email_delivery_request", item)
        atomic_write_json(item_path, item)
        return item


class BatchEmailGenerator:
    """Generate two deterministic email packages before the committer runs."""

    ANALYSIS_BODY = "rendered/emails/trending_analysis.md"
    LOGS_BODY = "rendered/emails/system_logs.md"
    SYSTEM_LOG = "rendered/emails/system_log.json"

    def __init__(
        self,
        root: Path,
        store: RunStore,
        registry: SchemaRegistry,
    ):
        self.root = root
        self.store = store
        self.outbox = CompletionEmailOutbox(root, registry)

    def generate(
        self,
        run_id: str,
        batch_id: str,
        recipient: str,
    ) -> List[Dict[str, Any]]:
        run_dir = self.store.run_dir(run_id)
        manifest = self.store.load_manifest(run_id)
        digest_path = run_dir / "rendered" / "digest.md"
        digest = digest_path.read_text(encoding="utf-8")
        proposals = load_json(
            run_dir / "validation" / "signal_proposals.json"
        )["signal_proposals"]
        reports = load_json(run_dir / "validation" / "reports.json")["reports"]
        trace = load_json(run_dir / "trace" / "events.json")
        source_input = load_json(run_dir / "inputs" / "observations.json").get(
            "source_snapshot"
        )
        screen_coverage = load_json(run_dir / "screen" / "coverage.json")
        eligible_count = sum(bool(item["execution_eligible"]) for item in proposals)
        producer = manifest["versions"]

        analysis_body = "\n".join(
            [
                "# Trending analysis results",
                "",
                "- Batch: `%s`" % batch_id,
                "- Run: `%s`" % run_id,
                "- Passing validation gates: `%d/%d`"
                % (eligible_count, len(proposals)),
                "",
                digest,
            ]
        )
        logs_body = "\n".join(
            [
                "# System logs",
                "",
                "- Batch: `%s`" % batch_id,
                "- Run: `%s`" % run_id,
                "- Snapshot stage: `pre_committer`",
                "- Producer: `%s / %s`"
                % (producer["producer_vendor"], producer["producer_model"]),
                "- Validator: `%s / %s`"
                % (producer["validator_vendor"], producer["validator_model"]),
                "- Degraded reasons: `%s`"
                % (", ".join(manifest["degraded_reasons"]) or "none"),
                "- Source coverage: `%s`"
                % (
                    source_input.get("coverage_status", "test_or_unspecified")
                    if source_input
                    else "test_or_unspecified"
                ),
                "",
                "The sanitized system log is attached. Credentials and raw model payloads are excluded.",
                "",
            ]
        )
        system_log = {
            "schema_version": "1.0.0",
            "batch_id": batch_id,
            "run_id": run_id,
            "snapshot_stage": "pre_committer",
            "snapshot_at": manifest["updated_at"],
            "manifest": manifest,
            "proposal_summary": _proposal_summary(proposals),
            "validation_reports": reports,
            "source_snapshot": source_input,
            "screen_coverage": screen_coverage,
            "trace": trace,
            "security": {
                "credentials_included": False,
                "raw_provider_prompts_included": False,
                "raw_provider_responses_included": False,
            },
        }
        self.store.write_text(run_id, self.ANALYSIS_BODY, analysis_body)
        self.store.write_text(run_id, self.LOGS_BODY, logs_body)
        self.store.write_json(run_id, self.SYSTEM_LOG, system_log)

        if not recipient:
            return []
        analysis_path = run_dir / self.ANALYSIS_BODY
        logs_path = run_dir / self.LOGS_BODY
        system_log_path = run_dir / self.SYSTEM_LOG
        return [
            self.outbox.enqueue(
                run_id,
                batch_id,
                "trending_analysis",
                recipient,
                "Trending analysis results — batch %s" % batch_id,
                analysis_path,
                [digest_path],
            ),
            self.outbox.enqueue(
                run_id,
                batch_id,
                "system_logs",
                recipient,
                "System logs — batch %s" % batch_id,
                logs_path,
                [system_log_path],
            ),
        ]


def load_batch_email_requests(root: Path, run_id: str) -> List[Dict[str, Any]]:
    requests = []
    for path in sorted((root / "state" / "outbox").glob("email_%s_*.json" % run_id)):
        item = load_json(path)
        if item.get("channel") == "email" and item.get("run_id") == run_id:
            requests.append(item)
    return requests
