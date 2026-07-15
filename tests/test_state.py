from __future__ import annotations

from pathlib import Path

import pytest

from stocktrend.errors import StateTransitionError
from stocktrend.state import RunIdentity, RunStore


def identity() -> RunIdentity:
    return RunIdentity(
        workflow_version="2.2.0",
        strategy_id="test",
        strategy_version="1.0.0",
        venue="XNAS",
        exchange_session_date="2026-07-15",
        analysis_window="close",
        execution_mode="analysis_only",
    )


def test_logical_run_revision_is_idempotent(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    first = store.create_or_resume(identity(), {"workflow": "2.2.0"}, 1)
    second = store.create_or_resume(identity(), {"workflow": "2.2.0"}, 1)
    assert first["run_id"] == second["run_id"]
    revised = store.create_or_resume(identity(), {"workflow": "2.2.0"}, 2)
    assert revised["run_id"] != first["run_id"]


def test_state_machine_rejects_invalid_transition(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    manifest = store.create_or_resume(identity(), {"workflow": "2.2.0"}, 1)
    with pytest.raises(StateTransitionError):
        store.transition(manifest["run_id"], "finalized")


def test_changed_dependencies_require_new_revision(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.create_or_resume(identity(), {"input_hash": "first"}, 1)
    with pytest.raises(StateTransitionError, match="new run revision"):
        store.create_or_resume(identity(), {"input_hash": "changed"}, 1)
