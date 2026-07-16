"""Final deterministic artifact committer for a completed batch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from .contracts import SchemaRegistry
from .outbox import PublicationOutbox
from .state import RunStore
from .util import atomic_write_json, load_json, utc_now_iso


class ArtifactCommitter:
    """Commit approved run artifacts through the idempotent publication outbox."""

    def __init__(
        self,
        root: Path,
        store: RunStore,
        registry: SchemaRegistry,
    ):
        self.root = root
        self.store = store
        self.publisher = PublicationOutbox(root, registry)

    def commit(self, run_id: str, relative_paths: Iterable[str]) -> Dict[str, Any]:
        receipt_path = self.root / "state" / "commits" / ("%s.json" % run_id)
        if receipt_path.exists():
            return load_json(receipt_path)
        manifest = self.store.load_manifest(run_id)
        if manifest["state"] != "finalized":
            raise ValueError("committer requires a finalized run")
        committed: List[Dict[str, str]] = []
        for relative_path in relative_paths:
            artifact_hash = manifest["artifact_hashes"].get(relative_path)
            if artifact_hash is None:
                raise ValueError("artifact is not recorded: %s" % relative_path)
            source_path = self.store.run_dir(run_id) / relative_path
            item = self.publisher.enqueue_artifact(
                run_id,
                source_path,
                artifact_hash,
            )
            acknowledged = self.publisher.publish_artifact(item)
            committed.append(
                {
                    "relative_path": relative_path,
                    "artifact_hash": artifact_hash,
                    "operation_id": acknowledged["operation_id"],
                }
            )
        receipt = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "committed_at": utc_now_iso(),
            "artifacts": committed,
        }
        atomic_write_json(receipt_path, receipt)
        return receipt
