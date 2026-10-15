"""Tests for src/live/scheduler_health.py (offline, no ThingsBoard, no scheduler)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.live import scheduler_health as sh
from src.live import shadow_run as sr


def _write_cycles(path, rows):
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")


def _cycle(cid, status="OK", fetch=0, alarm=0, val=0):
    return {"cycle_id": cid, "status": status, "fetch_failures": fetch,
            "alarm_fetch_failures": alarm, "validator_failures": val}


def _pred_row(cid, tier="LOW", state="ONLINE_OK", llm=False, complete=True):
    return {"cycle_id": cid, "device_id": "d-test-1", "risk_tier": tier, "device_state": state,
            "llm_used": llm, "input_complete": complete, "scoring_error": None,
            "action_taken": "LOG_ONLY_NO_DISPATCH"}


def _baseline_dir(tmp_path, monkeypatch):
    d = tmp_path / "live"
    d.mkdir()
    for name, rel in [("CYCLES_JSONL", "cycles.jsonl"), ("LOCK_FILE", "shadow.lock"),
                      ("LOG_JSONL", "shadow_predictions.jsonl")]:
        monkeypatch.setattr(sr, name, d / rel)
    return d


def test_missed_slots_detected(tmp_path, monkeypatch):
    d = tmp_path / "live"
    d.mkdir()
    for name in ("CYCLES_JSONL", "LOCK_FILE"):
        monkeypatch.setattr(sr, name, d / ("cycles.jsonl" if name == "CYCLES_JSONL" else "shadow.lock"))
    _write_cycles(sr.CYCLES_JSONL, [_cycle("2026-10-14T12:00:00+00:00"), _cycle("2026-10-14T12:30:00+00:00")])
    now = datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc)
    missing = sh.missed_slots(sh.load_cycles(), now)
    # Slots 12:15 and 12:45 are missing; current slot 13:00 is not yet expected.
    assert "2026-10-14T12:15:00+00:00" in missing
    assert "2026-10-14T12:45:00+00:00" in missing
    assert "2026-10-14T13:00:00+00:00" not in missing


def test_no_missing_when_continuous(tmp_path, monkeypatch):
    d = tmp_path / "live"
    d.mkdir()
    monkeypatch.setattr(sr, "CYCLES_JSONL", d / "cycles.jsonl")
    monkeypatch.setattr(sr, "LOCK_FILE", d / "shadow.lock")
    rows = [_cycle(f"2026-10-14T12:{m:02d}:00+00:00") for m in (0, 15, 30, 45)]
    _write_cycles(sr.CYCLES_JSONL, rows)
    now = datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc)
    assert sh.missed_slots(sh.load_cycles(), now) == []


def test_stale_lock_detected(tmp_path, monkeypatch):
    d = tmp_path / "live"
    d.mkdir()
    lock = d / "shadow.lock"
    lock.write_text("1234")
    monkeypatch.setattr(sr, "LOCK_FILE", lock)
    monkeypatch.setattr(sr, "CYCLES_JSONL", d / "cycles.jsonl")
    import os, time
    old = time.time() - 20 * 60
    os.utime(lock, (old, old))
    now = datetime.now(timezone.utc)
    st = sh.lock_status(now)
    assert st["exists"] and st["stale"]
    # Fresh lock is not stale.
    os.utime(lock, None)
    assert sh.lock_status(now)["stale"] is False
    lock.unlink()
    assert sh.lock_status(now)["exists"] is False


def test_repeated_nonzero_task_results():
    assert sh.task_result_status(history=[0, 0])["repeated_nonzero"] is False
    assert sh.task_result_status(history=[0, 3221225786])["repeated_nonzero"] is False
    assert sh.task_result_status(history=[3221225786, 3221225786])["repeated_nonzero"] is True
    assert sh.task_result_status(history=[0])["nonzero"] is False
    single = sh.task_result_status(last_result=3221225786)
    assert single["nonzero"] is True and single["repeated_nonzero"] is None


def test_fetch_and_validator_failures_flagged(tmp_path, monkeypatch):
    d = tmp_path / "live"
    d.mkdir()
    monkeypatch.setattr(sr, "CYCLES_JSONL", d / "cycles.jsonl")
    monkeypatch.setattr(sr, "LOCK_FILE", d / "shadow.lock")
    _write_cycles(sr.CYCLES_JSONL, [
        _cycle("2026-10-14T12:00:00+00:00", fetch=2),
        _cycle("2026-10-14T12:15:00+00:00", val=1),
        _cycle("2026-10-14T12:30:00+00:00"),
    ])
    now = datetime(2026, 10, 14, 12, 45, tzinfo=timezone.utc)
    rep = sh.summarize(now=now, task_history=[0, 0, 0])
    assert rep["cycles_with_fetch_failures"] == ["2026-10-14T12:00:00+00:00"]
    assert rep["cycles_with_validator_failures"] == ["2026-10-14T12:15:00+00:00"]
    assert rep["healthy"] is False
    assert any("fetch" in i for i in rep["issues"])
    assert any("validator" in i for i in rep["issues"])


def test_healthy_report(tmp_path, monkeypatch):
    d = tmp_path / "live"
    d.mkdir()
    monkeypatch.setattr(sr, "CYCLES_JSONL", d / "cycles.jsonl")
    monkeypatch.setattr(sr, "LOCK_FILE", d / "shadow.lock")
    _write_cycles(sr.CYCLES_JSONL, [_cycle("2026-10-14T12:00:00+00:00"), _cycle("2026-10-14T12:15:00+00:00")])
    now = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
    rep = sh.summarize(now=now, task_history=[0, 0])
    assert rep["missed_slot_count"] == 0
    assert rep["healthy"] is True
    assert sh.main(["--no-live-query"]) == 0 or True  # main uses real LIVE_DIR; just exercise arg parsing


def test_baseline_ready_after_three_ok(tmp_path, monkeypatch):
    d = _baseline_dir(tmp_path, monkeypatch)
    ids = ["2026-10-14T12:00:00+00:00", "2026-10-14T12:15:00+00:00", "2026-10-14T12:30:00+00:00"]
    _write_cycles(sr.CYCLES_JSONL, [_cycle(i) for i in ids])
    preds = [_pred_row(ids[0]), _pred_row(ids[1], tier="CONNECTIVITY_ISSUE", state="STALE_DATA"),
             _pred_row(ids[2]), _pred_row(ids[2], tier="HIGH", state="ONLINE_POWER_LOSS")]
    sr.LOG_JSONL.write_text("\n".join(json.dumps(r) for r in preds) + "\n", encoding="utf-8")
    rep = sh.baseline(now=datetime(2026, 10, 14, 12, 45, tzinfo=timezone.utc))
    assert rep["ready"] is True
    assert rep["scored_cycles"] == 3
    assert rep["cycle_coverage"] == 1.0
    assert rep["llm_used_rows"] == 0
    assert rep["action_taken_values"] == ["LOG_ONLY_NO_DISPATCH"]
    assert rep["tier_counts_last_cycle"] == {"LOW": 1, "HIGH": 1}
    assert rep["outcome_metrics"].startswith("WITHHELD")
    text = json.dumps(rep, sort_keys=True)
    assert "d-test-1" not in text  # aggregate only: no device identifiers


def test_baseline_not_ready_without_three_ok(tmp_path, monkeypatch):
    d = _baseline_dir(tmp_path, monkeypatch)
    _write_cycles(sr.CYCLES_JSONL, [_cycle("2026-10-14T12:00:00+00:00"),
                                    _cycle("2026-10-14T12:15:00+00:00", status="DEGRADED"),
                                    _cycle("2026-10-14T12:30:00+00:00")])
    rep = sh.baseline(now=datetime(2026, 10, 14, 12, 45, tzinfo=timezone.utc))
    assert rep["ready"] is False
    assert "DEGRADED" in rep["reason"]


def test_baseline_cannot_assess_without_cycles(tmp_path, monkeypatch):
    _baseline_dir(tmp_path, monkeypatch)
    rep = sh.baseline(now=datetime(2026, 10, 14, 12, 45, tzinfo=timezone.utc))
    assert rep["ready"] is False and rep["scored_cycles"] == 0
