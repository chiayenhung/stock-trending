"""Durable, idempotent publication outbox."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from .contracts import SchemaRegistry
from .util import atomic_write_json, load_json, sha256_text, utc_now_iso


class PublicationOutbox:
    def __init__(self, root: Path, registry: SchemaRegistry):
        self.root = root
        self.registry = registry
        self.outbox_dir = root / "state" / "outbox"

    def enqueue_artifact(
        self,
        run_id: str,
        source_path: Path,
        artifact_hash: str,
    ) -> Dict[str, Any]:
        operation_id = "publish_%s_%s" % (run_id, artifact_hash[:12])
        item_path = self.outbox_dir / ("%s.json" % operation_id)
        if item_path.exists():
            return load_json(item_path)
        item = {
            "schema_version": "1.0.0",
            "operation_id": operation_id,
            "run_id": run_id,
            "channel": "artifact",
            "artifact_path": str(source_path),
            "artifact_hash": artifact_hash,
            "status": "pending",
            "created_at": utc_now_iso(),
            "acknowledged_at": None,
        }
        self.registry.validate("delivery_outbox_item", item)
        atomic_write_json(item_path, item)
        return item

    def publish_artifact(self, item: Dict[str, Any]) -> Dict[str, Any]:
        item_path = self.outbox_dir / ("%s.json" % item["operation_id"])
        current = load_json(item_path)
        if current["status"] == "acknowledged":
            return current
        source = Path(current["artifact_path"])
        content = source.read_text(encoding="utf-8")
        if sha256_text(content) != current["artifact_hash"]:
            raise ValueError("artifact hash changed after enqueue")
        target = self.root / "artifacts" / current["run_id"] / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(target))
        current["status"] = "acknowledged"
        current["acknowledged_at"] = utc_now_iso()
        self.registry.validate("delivery_outbox_item", current)
        atomic_write_json(item_path, current)
        return current
