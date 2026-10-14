"""G2 live shadow tests (offline: ThingsBoard replaced by a replay source).

1. Train/serve parity: live features == stored training matrix row.
2. No future data: points with ts >= cutoff never change features.
3. Read-only session blocks every non-GET except the login POST.
4. Prediction log is append-only (UPDATE/DELETE abort) and cycles are idempotent.
5. Experimental LLM enabled -> cycle refused.
6. Outcome labels reproduce the stored R2 outage events.
7. Outcomes are PENDING before 24h+settle and INCOMPLETE without follow-up.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.live import outcomes, shadow_run as sr
from src.predict.train_remediated_predictor import CLEAN_FEATURES
from src.tickets import llm_bridge

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "data" / "processed" / "remediated_features_matrix.parquet"
EVENTS = ROOT / "data" / "processed" / "remediated_power_outage_events.parquet"
# Latest training slot with ONLINE_OK and ONLINE_POWER_LOSS devices present.
MATRIX_END = datetime(2026, 10, 12, 22, 45, tzinfo=timezone.utc)
NOW = MATRIX_END + timedelta(minutes=16)  # cutoff 23:00 -> last complete bin 22:45
# Training grid origin. Live uses a rolling 90-day window; pin it here so the
# parity check is exact (unbounded forward-fills depend on the grid origin).
TRAIN_GRID_START = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)

needs_data = pytest.mark.skipif(
    not (MATRIX.exists() and sr.REGISTRY.exists() and sr.RAW_TELEMETRY.exists()),
    reason="historical data/ not present (gitignored)",
)
needs_models = pytest.mark.skipif(
    not (ROOT / "models/outage_predictor/remediated_lightgbm_clean.joblib").exists(),
    reason="model weights not present (untracked)",
)


class ReplaySource:
    """Stands in for ThingsBoard: returns no new points unless `extra` is set."""

    verify_tls = True

    def __init__(self, extra=None):
        self.extra = extra or {}

    def fetch_timeseries(self, did, keys, start_ms, end_ms):
        return list(self.extra.get(did, []))

    def fetch_alarms(self, did, start_ms, end_ms):
        return []


@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "LOOKBACK", MATRIX_END - TRAIN_GRID_START)
    d = tmp_path / "live"
    for name, rel in [("LIVE_DIR", ""), ("CACHE_DIR", "telemetry_cache"), ("ALARM_CACHE", "alarms_cache.parquet"),
                      ("FETCH_STATE", "fetch_state.json"), ("LOG_JSONL", "shadow_predictions.jsonl"),
                      ("LOG_DB", "shadow_predictions.db"), ("CYCLES_JSONL", "cycles.jsonl"),
                      ("LOCK_FILE", "shadow.lock"), ("STOP_FILE", "STOP")]:
        monkeypatch.setattr(sr, name, d / rel if rel else d)
    d.mkdir()
    return d


@pytest.fixture(scope="module")
def sample_devices():
    if not MATRIX.exists():
        pytest.skip("matrix missing")
    m = pd.read_parquet(MATRIX, columns=["device_id", "timestamp", "device_state"])
    last = m[m["timestamp"] == MATRIX_END.strftime("%Y-%m-%d %H:%M:%S%z")]
    # one device per observed state; ONLINE states first
    order = {"ONLINE_OK": 0, "ONLINE_POWER_LOSS": 1, "STALE_DATA": 2, "OFFLINE_LONG": 3}
    last = last.assign(o=last["device_state"].map(order)).sort_values("o")
    picks = last.drop_duplicates("device_state")["device_id"].tolist()
    assert len(picks) == 4
    return picks


def _fleet(monkeypatch, dids):
    reg = pd.read_parquet(sr.REGISTRY)
    reg = reg[reg["device_id"].astype(str).isin(dids)]
    monkeypatch.setattr(sr, "load_fleet", lambda: [
        {"device_id": str(r.device_id), "name": str(r.name), "bank": str(r.bank), "in_training_fleet": True}
        for r in reg.itertuples()])


def _spy_features(monkeypatch):
    seen = {}
    real = sr.build_remediated_device_features

    def spy(**kw):
        df = real(**kw)
        seen[kw["device_id"]] = df.iloc[-1]
        return df

    monkeypatch.setattr(sr, "build_remediated_device_features", spy)
    return seen


@needs_data
@needs_models
def test_train_serve_parity(live_dir, monkeypatch, sample_devices):
    _fleet(monkeypatch, sample_devices)
    seen = _spy_features(monkeypatch)
    summary = sr.run_cycle(ReplaySource(), now=NOW)
    assert summary["status"] == "OK", summary
    m = pd.read_parquet(MATRIX)
    m = m[m["timestamp"] == MATRIX_END.strftime("%Y-%m-%d %H:%M:%S%z")].set_index("device_id")
    for did in sample_devices:
        live, train = seen[did], m.loc[did]
        assert live["device_state"] == train["device_state"], did
        np.testing.assert_allclose(live[CLEAN_FEATURES].astype(float).values,
                                   train[CLEAN_FEATURES].astype(float).values, rtol=1e-9, atol=1e-9,
                                   err_msg=did)
    recs = [json.loads(l) for l in sr.LOG_JSONL.read_text().splitlines()]
    assert {r["action_taken"] for r in recs} == {"LOG_ONLY_NO_DISPATCH"}
    assert all(r["llm_used"] is False for r in recs)
    for r in recs:
        for k in ("prediction_time", "device_id", "model_version", "policy_version", "input_payload_hash",
                  "ticket_hash", "rendered_ticket", "validator_valid"):
            assert r[k] is not None, k


@needs_data
@needs_models
def test_future_points_do_not_change_features(live_dir, monkeypatch, sample_devices):
    did = sample_devices[0]
    _fleet(monkeypatch, [did])
    seen = _spy_features(monkeypatch)
    cutoff_ms = int((MATRIX_END + timedelta(minutes=15)).timestamp() * 1000)
    assert seen == {}
    future = [(k, cutoff_ms + i * 60000, "999") for k in ("ac_voltage", "battery_voltage") for i in range(0, 30)]
    sr.run_cycle(ReplaySource({did: future}), now=NOW)
    with_future = seen[did][CLEAN_FEATURES].astype(float).values
    train = pd.read_parquet(MATRIX)
    train = train[(train["device_id"] == did) &
                  (train["timestamp"] == MATRIX_END.strftime("%Y-%m-%d %H:%M:%S%z"))].iloc[0]
    np.testing.assert_allclose(with_future, train[CLEAN_FEATURES].astype(float).values, rtol=1e-9, atol=1e-9)
    # Ticket observation times and telemetry age must also ignore future points.
    rec = json.loads(sr.LOG_JSONL.read_text().splitlines()[0])
    cutoff = pd.Timestamp(MATRIX_END + timedelta(minutes=15))
    assert rec["last_telemetry_ts"] is None or pd.Timestamp(rec["last_telemetry_ts"]) < cutoff
    assert "999" not in rec["rendered_ticket"]


def test_read_only_session_blocks_writes():
    s = sr.ReadOnlySession()
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(sr.WriteAttemptError):
            s.request(method, "https://tb.invalid/api/alarm")
    with pytest.raises(sr.WriteAttemptError):
        s.request("POST", "https://tb.invalid/api/plugins/telemetry/DEVICE/x/attributes/SERVER_SCOPE")


def test_log_is_append_only(live_dir):
    sr.init_db()
    rec = {c: "x" for c in sr.DB_COLUMNS if c != "record_json"}
    sr.append_records([{**rec, "cycle_id": "c1", "device_id": "d1"}])
    con = sqlite3.connect(sr.LOG_DB)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE predictions SET risk_tier = 'LOW'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM predictions")
    con.close()
    assert sr.cycle_already_logged("c1")


@needs_data
@needs_models
def test_cycle_is_idempotent(live_dir, monkeypatch, sample_devices):
    _fleet(monkeypatch, sample_devices[:1])
    assert sr.run_cycle(ReplaySource(), now=NOW)["status"] == "OK"
    assert sr.run_cycle(ReplaySource(), now=NOW + timedelta(minutes=5))["status"] == "SKIPPED_ALREADY_LOGGED"
    assert len(sr.LOG_JSONL.read_text().splitlines()) == 1


def test_llm_enabled_refuses_cycle(live_dir, monkeypatch):
    monkeypatch.setattr(llm_bridge, "is_experimental_llm_enabled", lambda: True)
    assert sr.run_cycle(ReplaySource(), now=NOW)["status"] == "REFUSED_LLM_ENABLED"
    assert not sr.LOG_JSONL.exists()


@pytest.mark.skipif(not EVENTS.exists(), reason="stored events missing")
def test_outcome_labels_reproduce_r2_events():
    cfg = yaml.safe_load((ROOT / "configs/config.yaml").read_text(encoding="utf-8"))["outage_label"]
    ev = pd.read_parquet(EVENTS)
    alarms = pd.read_parquet(sr.RAW_ALARMS)
    dids = ev["device_id"].value_counts().index[:5].tolist()
    for did in dids:
        d = sr.RAW_TELEMETRY / did
        got = outcomes.outage_events(outcomes._read(d, "ac_voltage"), outcomes._read(d, "battery_voltage"),
                                     outcomes._read(d, "statusbox_mains_on"),
                                     alarms[alarms["device_id"].astype(str) == did], cfg)
        want = ev[ev["device_id"] == did].sort_values("start_ts")
        assert [(round(s), round(e)) for s, e, _ in got] == \
               [(round(s), round(e)) for s, e in zip(want["start_ts"], want["end_ts"])], did


def test_outcome_maturity_rules(live_dir):
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    rows = []
    for name, age_h, complete in [("pending", 10, True), ("incomplete", 30, False), ("mature", 30, True)]:
        rows.append({"device_id": name, "prediction_time": (now - timedelta(hours=age_h)).isoformat(),
                     "input_complete": complete, "scoring_error": None, "risk_tier": "LOW",
                     "in_training_fleet": True})
    sr.FETCH_STATE.write_text(json.dumps({r["device_id"]: int(now.timestamp() * 1000) for r in rows}))
    df = pd.DataFrame(rows)
    df["pt"] = pd.to_datetime(df["prediction_time"], utc=True)
    out = outcomes.assign_outcomes(df, now).set_index("device_id")["outcome_status"]
    assert out.to_dict() == {"pending": "PENDING", "incomplete": "INCOMPLETE", "mature": "MATURE"}


def test_stale_ticket_shows_last_raw_reading_not_imputation(tmp_path):
    cutoff = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    dev = tmp_path / "dev"
    dev.mkdir()
    last = int((cutoff - timedelta(hours=3)).timestamp() * 1000)
    pd.DataFrame({"ts": [last - 60000, last], "value": ["231", "0"]}).to_parquet(dev / "ac_voltage.parquet")
    row = {"device_id": "d1", "device_name": "SITE-1", "bank": "BOI", "device_state": "STALE_DATA",
           "risk_tier": "CONNECTIVITY_ISSUE", "calibrated_prob": 0.01, "rules_triggered_json": "[]",
           "model_version": sr.MODEL_VERSION, "is_ac_sensor_present": 1, "is_battery_sensor_present": 0,
           "ac_voltage_mean_1h": 220.0, "battery_voltage_mean_1h": 13.5, "time_since_mains_loss_hours": 168.0}
    ti = sr.build_ticket(row, dev, cutoff + timedelta(minutes=1))
    ac = ti.measurement("ac_voltage")
    assert (ac.value, ac.freshness) == (0.0, "STALE")
    assert ti.measurement("time_since_mains_loss").freshness == "UNAVAILABLE"
    assert ti.measurement("battery_voltage").freshness == "UNAVAILABLE"
    text = sr.render_connectivity_ticket_without_probability(ti).text
    assert "220" not in text and "168" not in text and "13.5" not in text


@needs_data
@needs_models
def test_alarm_fetch_failure_marks_cycle_degraded(live_dir, monkeypatch, sample_devices):
    _fleet(monkeypatch, sample_devices[:1])

    class BadAlarms(ReplaySource):
        def fetch_alarms(self, *a):
            raise RuntimeError("GET failed: /api/alarm")

    s = sr.run_cycle(BadAlarms(), now=NOW)
    assert s["status"] == "DEGRADED" and s["alarm_fetch_failures"] == 1 and s["complete_input_devices"] == 0
    rec = json.loads(sr.LOG_JSONL.read_text().splitlines()[0])
    assert rec["input_complete"] is False
