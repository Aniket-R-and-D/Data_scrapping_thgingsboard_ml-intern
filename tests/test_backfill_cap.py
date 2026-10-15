"""Tests for bounded backfill (--max-backfill-hours) and crash diagnostics.

Offline: ThingsBoard replaced by recording/replay sources. Needs historical
data + pinned models for release load and scoring (same markers as live tests).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.live import shadow_run as sr

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "data" / "processed" / "remediated_features_matrix.parquet"
MATRIX_END = datetime(2026, 10, 12, 22, 45, tzinfo=timezone.utc)
NOW = MATRIX_END + timedelta(minutes=16)
TRAIN_GRID_START = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)

needs_data = pytest.mark.skipif(
    not (MATRIX.exists() and sr.REGISTRY.exists() and sr.RAW_TELEMETRY.exists()),
    reason="historical data/ not present (gitignored)",
)
needs_models = pytest.mark.skipif(
    not (ROOT / "models/outage_predictor/remediated_lightgbm_clean.joblib").exists(),
    reason="model weights not present (untracked)",
)


class RecordingSource:
    """Records requested (start_ms, end_ms) per device; returns no new points."""

    verify_tls = True

    def __init__(self):
        self.calls: dict[str, list[tuple[int, int]]] = {}

    def fetch_timeseries(self, did, keys, start_ms, end_ms):
        self.calls.setdefault(did, []).append((start_ms, end_ms))
        return []

    def fetch_alarms(self, did, start_ms, end_ms):
        return []


@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "LOOKBACK", MATRIX_END - TRAIN_GRID_START)
    d = tmp_path / "live"
    for name, rel in [("LIVE_DIR", ""), ("CACHE_DIR", "telemetry_cache"), ("ALARM_CACHE", "alarms_cache.parquet"),
                      ("FETCH_STATE", "fetch_state.json"), ("LOG_JSONL", "shadow_predictions.jsonl"),
                      ("LOG_DB", "shadow_predictions.db"), ("CYCLES_JSONL", "cycles.jsonl"),
                      ("LOCK_FILE", "shadow.lock"), ("STOP_FILE", "STOP"),
                      ("CRASH_MARKER", "last_crash.json")]:
        monkeypatch.setattr(sr, name, d / rel if rel else d)
    d.mkdir()
    return d


@pytest.fixture
def one_device(monkeypatch):
    reg = pd.read_parquet(sr.REGISTRY)
    did = str(reg.iloc[0]["device_id"])
    row = reg.iloc[0]
    monkeypatch.setattr(sr, "load_fleet", lambda: [
        {"device_id": str(row.device_id), "name": str(row.name), "bank": str(row.bank),
         "in_training_fleet": True}])
    return did


@needs_data
@needs_models
def test_backfill_cap_bounds_request_range(live_dir, monkeypatch, one_device):
    src = RecordingSource()
    cutoff_ms = int((MATRIX_END + timedelta(minutes=15)).timestamp() * 1000)
    old_cursor = cutoff_ms - 5 * 3600 * 1000  # 5 h of backlog
    sr.FETCH_STATE.write_text(json.dumps({one_device: old_cursor}))
    summary = sr.run_cycle(src, now=NOW, max_backfill_hours=1.0)
    assert summary["status"] in ("OK", "DEGRADED"), summary
    (start, end), = src.calls[one_device]
    assert end - start <= 3600 * 1000 + 1, (end - start)
    assert summary["backfill_capped_devices"] == 1
    assert summary["max_backfill_hours"] == 1.0
    state = json.loads(sr.FETCH_STATE.read_text())
    # Oldest-first: cursor advances ~1 h, not all the way to the cutoff.
    assert abs(state[one_device] - (old_cursor + 3600 * 1000)) < 60000
    assert state[one_device] < cutoff_ms - 1


@needs_data
@needs_models
def test_backfill_drains_over_cycles(live_dir, monkeypatch, one_device):
    src = RecordingSource()
    cutoff_ms = int((MATRIX_END + timedelta(minutes=15)).timestamp() * 1000)
    old_cursor = cutoff_ms - 3 * 3600 * 1000
    sr.FETCH_STATE.write_text(json.dumps({one_device: old_cursor}))
    for k in range(3):
        s = sr.run_cycle(src, now=NOW + timedelta(minutes=15 * k), max_backfill_hours=1.0)
        assert s["status"] in ("OK", "DEGRADED"), s
    state = json.loads(sr.FETCH_STATE.read_text())
    assert abs(state[one_device] - (old_cursor + 3 * 3600 * 1000)) < 120000


@needs_data
@needs_models
def test_backfill_uncapped_zero_fetches_full_range(live_dir, monkeypatch, one_device):
    src = RecordingSource()
    cutoff_ms = int((MATRIX_END + timedelta(minutes=15)).timestamp() * 1000)
    old_cursor = cutoff_ms - 5 * 3600 * 1000
    sr.FETCH_STATE.write_text(json.dumps({one_device: old_cursor}))
    summary = sr.run_cycle(src, now=NOW, max_backfill_hours=0)
    assert summary["status"] in ("OK", "DEGRADED"), summary
    (start, end), = src.calls[one_device]
    assert (start, end) == (old_cursor + 1, cutoff_ms - 1)
    assert summary["backfill_capped_devices"] == 0


@needs_data
@needs_models
def test_crash_marker_on_persistence_failure(live_dir, monkeypatch, one_device):
    monkeypatch.setattr(sr, "append_records", lambda records: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        sr.run_cycle(RecordingSource(), now=NOW, max_backfill_hours=0)
    marker = json.loads(sr.CRASH_MARKER.read_text(encoding="utf-8"))
    assert marker["stage"] == "persistence"
    assert marker["exc_type"] == "RuntimeError"
    assert marker["cycle_id"] == (MATRIX_END + timedelta(minutes=15)).isoformat()
    assert "disk full" in marker["error"]


@needs_data
@needs_models
def test_keyboard_interrupt_writes_marker_and_propagates(live_dir, monkeypatch, one_device):
    class Killer(RecordingSource):
        def fetch_timeseries(self, *a):
            raise KeyboardInterrupt("simulated console kill")

    with pytest.raises(KeyboardInterrupt):
        sr.run_cycle(Killer(), now=NOW, max_backfill_hours=0)
    marker = json.loads(sr.CRASH_MARKER.read_text(encoding="utf-8"))
    assert marker["stage"] == "fetch"
    assert marker["exc_type"] == "KeyboardInterrupt"


@needs_data
@needs_models
def test_run_once_failed_on_top_level_error(live_dir, monkeypatch, one_device):
    monkeypatch.setattr(sr, "append_records", lambda records: (_ for _ in ()).throw(RuntimeError("disk full")))

    def factory():
        return RecordingSource()

    rc = sr.run_once(factory, max_backfill_hours=0)
    assert rc == 1
    rows = [json.loads(line) for line in sr.CYCLES_JSONL.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["status"] == "FAILED"
    assert not sr.LOCK_FILE.exists()  # lock released even on failure
    assert json.loads(sr.CRASH_MARKER.read_text(encoding="utf-8"))["stage"] == "persistence"
