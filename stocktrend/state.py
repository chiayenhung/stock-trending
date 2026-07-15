"""Atomic run storage, logical-run idempotency, and state transitions."""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import LockUnavailableError, StateTransitionError
from .util import (
    atomic_write_json,
    canonical_json,
    load_json,
    sha256_json,
    utc_now,
    utc_now_iso,
)


@dataclass(frozen=True)
class RunIdentity:
    workflow_version: str
    strategy_id: str
    strategy_version: str
    venue: str
    exchange_session_date: str
    analysis_window: str
    execution_mode: str

    def logical_key(self) -> str:
        return sha256_json(asdict(self))


class LogicalRunLock:
    def __init__(self, path: Path, lease_seconds: int = 300):
        self.path = path
        self.lease_seconds = lease_seconds
        self._handle: Optional[Any] = None

    def __enter__(self) -> "LogicalRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise LockUnavailableError("logical run is already locked") from exc
        lease = {
            "pid": os.getpid(),
            "acquired_at": utc_now_iso(),
            "expires_at": (utc_now() + timedelta(seconds=self.lease_seconds))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        self._handle.seek(0)
        self._handle.truncate(0)
        self._handle.write(canonical_json(lease))
        self._handle.flush()
        os.fsync(self._handle.fileno())
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class RunStore:
    TRANSITIONS = {
        "created": {"ingesting", "failed"},
        "ingesting": {"normalized", "failed"},
        "normalized": {"screened", "failed"},
        "screened": {"analyzed", "failed"},
        "analyzed": {"validated", "failed"},
        "validated": {"rendered", "failed"},
        "rendered": {"finalized", "failed"},
        "finalized": set(),
        "failed": set(),
    }

    def __init__(self, root: Path, lease_seconds: int = 300):
        self.root = root
        self.state_root = root / "state"
        self.lease_seconds = lease_seconds

    def lock_for(self, logical_key: str) -> LogicalRunLock:
        return LogicalRunLock(
            self.state_root / "locks" / ("%s.lock" % logical_key),
            self.lease_seconds,
        )

    def create_or_resume(
        self,
        identity: RunIdentity,
        versions: Dict[str, Any],
        run_revision: int = 1,
    ) -> Dict[str, Any]:
        logical_key = identity.logical_key()
        index_path = (
            self.state_root
            / "logical"
            / logical_key
            / ("revision-%d.json" % run_revision)
        )
        with self.lock_for(logical_key):
            if index_path.exists():
                index = load_json(index_path)
                existing = self.load_manifest(index["run_id"])
                if existing["versions"] != versions:
                    raise StateTransitionError(
                        "run dependencies changed; create a new run revision"
                    )
                return existing
            prefix = identity.exchange_session_date.replace("-", "")
            run_id = "run_%s_%s_r%d_%s" % (
                prefix,
                logical_key[:12],
                run_revision,
                uuid.uuid4().hex[:8],
            )
            now = utc_now_iso()
            manifest = {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "logical_key": logical_key,
                "run_revision": run_revision,
                "state": "created",
                "identity": asdict(identity),
                "versions": versions,
                "artifact_hashes": {},
                "degraded_reasons": [],
                "created_at": now,
                "updated_at": now,
            }
            atomic_write_json(self.run_dir(run_id) / "manifest.json", manifest)
            self._append_trace(
                run_id,
                {
                    "event": "run_created",
                    "state": "created",
                    "occurred_at": now,
                },
            )
            atomic_write_json(index_path, {"run_id": run_id})
            return manifest

    def run_dir(self, run_id: str) -> Path:
        return self.state_root / "runs" / run_id

    def load_manifest(self, run_id: str) -> Dict[str, Any]:
        return load_json(self.run_dir(run_id) / "manifest.json")

    def transition(self, run_id: str, new_state: str) -> Dict[str, Any]:
        manifest = self.load_manifest(run_id)
        current = manifest["state"]
        if new_state not in self.TRANSITIONS.get(current, set()):
            raise StateTransitionError("%s -> %s is not allowed" % (current, new_state))
        manifest["state"] = new_state
        manifest["updated_at"] = utc_now_iso()
        atomic_write_json(self.run_dir(run_id) / "manifest.json", manifest)
        self._append_trace(
            run_id,
            {
                "event": "state_transition",
                "from_state": current,
                "state": new_state,
                "occurred_at": manifest["updated_at"],
            },
        )
        return manifest

    def write_json(self, run_id: str, relative_path: str, value: Any) -> str:
        path = self.run_dir(run_id) / relative_path
        artifact_hash = atomic_write_json(path, value)
        self._record_hash(run_id, relative_path, artifact_hash)
        return artifact_hash

    def write_text(self, run_id: str, relative_path: str, content: str) -> str:
        from .util import atomic_write_text, sha256_text

        path = self.run_dir(run_id) / relative_path
        atomic_write_text(path, content)
        artifact_hash = sha256_text(content)
        self._record_hash(run_id, relative_path, artifact_hash)
        return artifact_hash

    def add_degraded_reason(self, run_id: str, reason: str) -> None:
        manifest = self.load_manifest(run_id)
        if reason not in manifest["degraded_reasons"]:
            manifest["degraded_reasons"].append(reason)
            manifest["updated_at"] = utc_now_iso()
            atomic_write_json(self.run_dir(run_id) / "manifest.json", manifest)
            self._append_trace(
                run_id,
                {
                    "event": "degraded",
                    "reason": reason,
                    "state": manifest["state"],
                    "occurred_at": manifest["updated_at"],
                },
            )

    def _record_hash(self, run_id: str, relative_path: str, value: str) -> None:
        manifest = self.load_manifest(run_id)
        manifest["artifact_hashes"][relative_path] = value
        manifest["updated_at"] = utc_now_iso()
        atomic_write_json(self.run_dir(run_id) / "manifest.json", manifest)

    def _append_trace(self, run_id: str, event: Dict[str, Any]) -> None:
        path = self.run_dir(run_id) / "trace" / "events.json"
        events = load_json(path) if path.exists() else []
        events.append(event)
        atomic_write_json(path, events)
