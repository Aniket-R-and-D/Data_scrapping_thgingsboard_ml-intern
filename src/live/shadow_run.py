"""src/live/shadow_run.py — G2 live shadow scorer (read-only, LOG_ONLY_NO_DISPATCH).

Replaces the simulated R4 daemon (src/shadow/live_shadow_job.py drew states and
probabilities from np.random). This module scores REAL telemetry:

  1. Fail closed: release startup check, pinned-artifact hashes, unsafe flags,
     experimental LLM must be off.
  2. Incrementally fetch telemetry + alarms from ThingsBoard through a session
     that refuses every non-GET request except the login POST.
  3. Build features with the exact training feature builder
     (build_remediated_device_features) over the same 90-day window, using only
     data with ts < data_cutoff (the current 15-minute slot boundary).
  4. Score with the pinned remediated LightGBM + Platt pair, assign the policy
     tier, render the deterministic ticket, run the Stage C field validator.
  5. Append one record per device to an append-only JSONL log and an SQLite
     table whose UPDATE/DELETE triggers abort.

Nothing here creates alarms, dispatches, or notifies. action_taken is always
"LOG_ONLY_NO_DISPATCH".

Usage:
    python -m src.live.shadow_run --once      # one cycle (scheduler entry point)
    python -m src.live.shadow_run --loop      # foreground, one cycle per 15-min slot
    # optional: --log-file PATH --max-backfill-hours 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.eval.build_splits import _row_to_ticket_input
from src.features.build_remediated_features import build_remediated_device_features
from src.live import gate_a
from src.predict.train_remediated_predictor import CLEAN_FEATURES
from src.shadow.run_shadow_remediation import assign_risk_tier
from src.tickets import llm_bridge
from src.tickets.causes import derive_possible_causes
from src.tickets.field_validator import validate_candidate
from src.tickets.policy import FRESHNESS_CURRENT_MAX_AGE, POLICY_VERSION, STALE_STATES
from src.tickets.renderer import render_connectivity_ticket_without_probability, render_ticket
from src.tickets.schema import Measurement, TicketInput
from src.tickets.startup_check import (
    DEFAULT_RELEASE_CONFIG,
    _sha256_file,
    require_pinned_artifact,
    run_startup_check,
)

LOG = logging.getLogger("src.live.shadow_run")

LIVE_DIR = ROOT / "data" / "live"
CACHE_DIR = LIVE_DIR / "telemetry_cache"
ALARM_CACHE = LIVE_DIR / "alarms_cache.parquet"
FETCH_STATE = LIVE_DIR / "fetch_state.json"
LOG_JSONL = LIVE_DIR / "shadow_predictions.jsonl"
LOG_DB = LIVE_DIR / "shadow_predictions.db"
CYCLES_JSONL = LIVE_DIR / "cycles.jsonl"
LOCK_FILE = LIVE_DIR / "shadow.lock"
STOP_FILE = LIVE_DIR / "STOP"

RAW_TELEMETRY = ROOT / "data" / "raw" / "telemetry"
RAW_ALARMS = ROOT / "data" / "raw" / "alarms" / "alarms.parquet"
REGISTRY = ROOT / "data" / "raw" / "registry" / "devices.parquet"
TRAINING_MATRIX = ROOT / "data" / "processed" / "remediated_features_matrix.parquet"

SCHEMA_VERSION = "live-shadow-v1"
ACTION_TAKEN = "LOG_ONLY_NO_DISPATCH"
MODEL_VERSION = "v2.0-remediated-lightgbm-clean"
SLOT = timedelta(minutes=15)
LOOKBACK = timedelta(days=90)          # same window as the training matrix
ALARM_REFETCH = timedelta(hours=26)    # re-read recent alarms so clear times update
# A killed cycle (console closed, Ctrl+C) leaves its lock behind. Cycles take ~1-5 min and the
# scheduled task's execution limit is 14 min, so an older lock is never a live cycle.
LOCK_STALE_AFTER = timedelta(minutes=14)
# Crash-marker file: overwritten on every abnormal cycle exit (latest crash only).
CRASH_MARKER = LIVE_DIR / "last_crash.json"
# Bounded backfill default: one cycle ingests at most this much backlog per device
# (oldest-first), so a resume after an outage cannot issue one oversized request.
# 0 or negative means uncapped (full catch-up; use only after stable cycles).
DEFAULT_MAX_BACKFILL_HOURS = 1.0
PAGE_LIMIT = 1000

# Every telemetry key read by build_remediated_device_features (file stem == TB key).
FEATURE_KEYS = (
    "ac_voltage", "ac_result", "ac_status", "battery_voltage", "battery_status",
    "statusbox_mains_on", "power_off_last", "Nvr_DVR_last", "dvr_nvr_off",
    "heartBeatCCTV", "fasLastHour", "fasUptime", "fire_alarm_fault_last", "disk",
    "hdd_error_last", "hddError", "uptimeHeartbeat", "heartbeat", "gatewayUptime",
    "lastActivityTime",
)
AC_KEYS = ("ac_voltage", "ac_result", "ac_status")
BATT_KEYS = ("battery_voltage", "battery_status")
JSON_NUM_PATTERN = r"{key}[\"':\s]+([0-9.]+)"  # same as build_remediated_features.read_ts_series
ALARM_COLUMNS = ["device_id", "alarm_type", "severity", "status", "start", "end", "clear"]


# --------------------------------------------------------------------------- #
# Read-only ThingsBoard access
# --------------------------------------------------------------------------- #
class WriteAttemptError(RuntimeError):
    """Raised on any HTTP request that could mutate ThingsBoard state."""


class ReadOnlySession(requests.Session):
    """requests.Session that only permits GET, plus POST to /api/auth/login."""

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        m = str(method).upper()
        if m == "GET" or (m == "POST" and str(url).rstrip("/").endswith("/api/auth/login")):
            return super().request(method, url, *args, **kwargs)
        raise WriteAttemptError(f"Blocked {m} {url}: live shadow is read-only.")


class ThingsBoardReadOnly:
    """Minimal read-only client. TLS verification ON unless TB_VERIFY_TLS=false."""

    def __init__(self, host: str, email: str, password: str, verify_tls: bool = True, delay_s: float = 0.04):
        self.host = host.rstrip("/")
        self._creds = {"username": email, "password": password}
        self.verify_tls = verify_tls
        self.delay_s = delay_s
        self.session = ReadOnlySession()
        self._login()

    @classmethod
    def from_env(cls) -> "ThingsBoardReadOnly":
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        verify = os.environ.get("TB_VERIFY_TLS", "true").strip().lower() not in ("0", "false", "no")
        return cls(os.environ["TB_HOST"], os.environ["TB_EMAIL"], os.environ["TB_PASSWORD"], verify_tls=verify)

    def _login(self) -> None:
        r = self.session.post(f"{self.host}/api/auth/login", json=self._creds, timeout=20, verify=self.verify_tls)
        r.raise_for_status()
        self.session.headers["X-Authorization"] = f"Bearer {r.json()['token']}"

    def _get(self, url: str) -> Any:
        for attempt in range(5):
            time.sleep(self.delay_s)
            try:
                r = self.session.get(url, timeout=30, verify=self.verify_tls)
            except requests.RequestException:
                time.sleep(min(30.0, 2.0 ** attempt))
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                self._login()
            elif r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(30.0, 2.0 ** attempt))
            else:
                break
        raise RuntimeError(f"GET failed: {url.split('?')[0]}")

    def fetch_timeseries(self, device_id: str, keys: Iterable[str], start_ms: int, end_ms: int) -> List[Tuple[str, int, str]]:
        """All points with start_ms <= ts <= end_ms for keys.

        ThingsBoard applies `limit` per key, so a dense key can be truncated in a
        multi-key call. Any key that hits the limit is refetched alone, paging
        backwards from its oldest returned timestamp.
        """
        keys = list(keys)
        base = f"{self.host}/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries"
        data = self._get(f"{base}?keys={','.join(keys)}&startTs={start_ms}&endTs={end_ms}"
                         f"&limit={PAGE_LIMIT}&agg=NONE&orderBy=DESC") or {}
        sizes = [len(v) for v in data.values()]
        if sizes and (max(sizes) >= PAGE_LIMIT or sum(sizes) >= PAGE_LIMIT):
            # Possibly truncated (whether `limit` is per key or global): page each key alone.
            return [pt for k in keys for pt in self._fetch_key(base, k, start_ms, end_ms)]
        return [(k, int(p["ts"]), str(p.get("value"))) for k, pts in data.items() for p in pts]

    def _fetch_key(self, base: str, key: str, start_ms: int, end_ms: int) -> List[Tuple[str, int, str]]:
        out: List[Tuple[str, int, str]] = []
        cursor_end = end_ms
        while cursor_end >= start_ms:
            pts = (self._get(f"{base}?keys={key}&startTs={start_ms}&endTs={cursor_end}"
                             f"&limit={PAGE_LIMIT}&agg=NONE&orderBy=DESC") or {}).get(key, [])
            out.extend((key, int(p["ts"]), str(p.get("value"))) for p in pts)
            if len(pts) < PAGE_LIMIT:
                break
            cursor_end = min(int(p["ts"]) for p in pts) - 1
        return out

    def fetch_alarms(self, device_id: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
        """Alarms normalised to the data/raw/alarms/alarms.parquet schema (ISO strings)."""
        rows: List[Dict[str, Any]] = []
        page = 0
        while True:
            body = self._get(f"{self.host}/api/alarm/DEVICE/{device_id}?pageSize=1000&page={page}"
                             f"&startTime={start_ms}&endTime={end_ms}&searchStatus=ANY") or {}
            for a in body.get("data", []):
                rows.append({
                    "device_id": device_id,
                    "alarm_type": a.get("type"),
                    "severity": a.get("severity"),
                    "status": a.get("status"),
                    "start": _iso_ms(a.get("startTs")),
                    "end": _iso_ms(a.get("endTs")),
                    "clear": _iso_ms(a.get("clearTs")),
                })
            if not body.get("hasNext"):
                return rows
            page += 1


def _iso_ms(ms: Any) -> Optional[str]:
    if ms in (None, 0, "0"):
        return None
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Local caches (data/live is gitignored; tenant data)
# --------------------------------------------------------------------------- #
def _load_state() -> Dict[str, int]:
    return json.loads(FETCH_STATE.read_text()) if FETCH_STATE.exists() else {}


def _save_state(state: Dict[str, int]) -> None:
    tmp = FETCH_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=0, sort_keys=True))
    os.replace(tmp, FETCH_STATE)


def _seed_device(device_id: str, floor_ms: int) -> int:
    """Copy historical raw telemetry (read-only source) into the live cache.

    Returns the cursor to resume live fetching from: the newest raw timestamp
    (re-fetching overlap is harmless; rows are de-duplicated on ts).
    """
    dev_cache = CACHE_DIR / device_id
    dev_cache.mkdir(parents=True, exist_ok=True)
    newest = floor_ms
    for key in FEATURE_KEYS:
        src = RAW_TELEMETRY / device_id / f"{key}.parquet"
        if not src.exists():
            continue
        df = pd.read_parquet(src, columns=["ts", "value"])
        df = df[df["ts"] >= floor_ms]
        if df.empty:
            continue
        df.assign(value=df["value"].astype(str)).to_parquet(dev_cache / f"{key}.parquet", index=False)
        newest = max(newest, int(df["ts"].max()))
    return newest


def _append_points(device_id: str, points: List[Tuple[str, int, str]], floor_ms: int) -> None:
    dev_cache = CACHE_DIR / device_id
    dev_cache.mkdir(parents=True, exist_ok=True)
    if not points:
        return
    new = pd.DataFrame(points, columns=["key", "ts", "value"])
    for key, grp in new.groupby("key"):
        path = dev_cache / f"{key}.parquet"
        parts = [grp[["ts", "value"]]]
        if path.exists():
            parts.insert(0, pd.read_parquet(path))
        comb = pd.concat(parts, ignore_index=True)
        comb = comb[comb["ts"] >= floor_ms].drop_duplicates("ts", keep="last").sort_values("ts")
        tmp = path.with_suffix(".tmp")
        comb.to_parquet(tmp, index=False)
        os.replace(tmp, path)


def _upsert_alarms(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    new = pd.DataFrame(rows, columns=ALARM_COLUMNS)
    old = pd.read_parquet(ALARM_CACHE) if ALARM_CACHE.exists() else pd.DataFrame(columns=ALARM_COLUMNS)
    comb = pd.concat([old, new], ignore_index=True)
    comb = comb.drop_duplicates(["device_id", "alarm_type", "start"], keep="last")
    tmp = ALARM_CACHE.with_suffix(".tmp")
    comb.to_parquet(tmp, index=False)
    os.replace(tmp, ALARM_CACHE)


def _seed_alarms(floor_iso: str) -> None:
    if ALARM_CACHE.exists() or not RAW_ALARMS.exists():
        return
    raw = pd.read_parquet(RAW_ALARMS)
    raw = raw[pd.to_datetime(raw["start"], format="ISO8601", utc=True, errors="coerce") >= pd.Timestamp(floor_iso)]
    raw[ALARM_COLUMNS].to_parquet(ALARM_CACHE, index=False)


def _clipped_device_dir(device_id: str, cutoff_ms: int, work_dir: Path) -> Path:
    """Point-in-time view of the cache: only rows with ts < cutoff_ms."""
    out = work_dir / device_id
    out.mkdir(parents=True, exist_ok=True)
    for path in (CACHE_DIR / device_id).glob("*.parquet"):
        df = pd.read_parquet(path)
        df[df["ts"] < cutoff_ms].to_parquet(out / path.name, index=False)
    return out


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
@dataclass
class Release:
    model: Any
    calibrator: Any
    model_sha256: str
    calibrator_sha256: str
    release_config_sha256: str
    git_commit: str


def load_release(config_path: Path = DEFAULT_RELEASE_CONFIG) -> Release:
    """Fail closed unless the release verifies; load only pinned artifacts."""
    check = run_startup_check(config_path)
    if not check.passed:
        raise RuntimeError(f"Release startup check FAILED: {check.errors}")
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    ops = cfg["operational_defaults"]
    assert ops["experimental_llm_enabled"] is False
    assert ops["dispatch"] == ops["alarm_creation"] == ops["notifications"] == "disabled"
    assert ops["telemetry_mode"] == "read-only"
    mv = cfg["model_versions"]
    model_path = ROOT / mv["lightgbm_outage_predictor"]["path"]
    cal_path = ROOT / mv["platt_calibrator"]["path"]
    m_sha = require_pinned_artifact(model_path, config_path)
    c_sha = require_pinned_artifact(cal_path, config_path)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        commit = "unknown"
    return Release(joblib.load(model_path), joblib.load(cal_path), m_sha, c_sha, _sha256_file(config_path), commit)


def _last_obs_ms(dev_dir: Path, keys: Iterable[str]) -> Optional[int]:
    best = None
    for k in keys:
        p = dev_dir / f"{k}.parquet"
        if p.exists():
            ts = pd.read_parquet(p, columns=["ts"])["ts"]
            if len(ts):
                best = max(best or 0, int(ts.max()))
    return best


def _last_raw(dev_dir: Path, keys: Iterable[str], json_key: str) -> Tuple[Optional[float], Optional[int]]:
    """Most recent parseable numeric reading (value, ts) across keys.

    JSON status keys are parsed with the feature builder's regex.
    """
    best: Tuple[Optional[float], Optional[int]] = (None, None)
    for k in keys:
        p = dev_dir / f"{k}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p, columns=["ts", "value"])
        if k.endswith("_status"):
            v = df["value"].astype(str).str.strip().str.lower()
            num = pd.to_numeric(v.str.extract(JSON_NUM_PATTERN.format(key=json_key), expand=False), errors="coerce")
        else:
            num = pd.to_numeric(df["value"], errors="coerce")
        ok = df[num.notna()]
        if len(ok):
            i = ok["ts"].idxmax()
            if best[1] is None or int(ok.at[i, "ts"]) > best[1]:
                best = (float(num[i]), int(ok.at[i, "ts"]))
    return best


def _display(mean_1h: float, raw: Tuple[Optional[float], Optional[int]], pred_ts: datetime,
             decimals: int) -> Tuple[Optional[float], Optional[int]]:
    """CURRENT readings show the 1h mean; STALE readings show the last raw value.

    Never shows the builder's nominal imputations (220 V / 13.5 V) as a measurement.
    """
    value, ts = raw
    if ts is None:
        return None, None
    age = pred_ts - datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
    shown = mean_1h if age <= FRESHNESS_CURRENT_MAX_AGE else value
    return round(float(shown), decimals), ts


def _measurement(fld: str, value: Optional[float], unit: str, obs_ms: Optional[int], pred_ts: datetime) -> Measurement:
    if value is None or obs_ms is None or not np.isfinite(value):
        return Measurement(fld, None, None, None, "UNAVAILABLE")
    obs = datetime.fromtimestamp(obs_ms / 1000.0, tz=timezone.utc)
    fresh = "CURRENT" if pred_ts - obs <= FRESHNESS_CURRENT_MAX_AGE else "STALE"
    return Measurement(fld, value, unit, obs, fresh)


def build_ticket(row: Dict[str, Any], dev_dir: Path, pred_ts: datetime) -> TicketInput:
    """Stage D ticket narrative, with REAL observation times and freshness.

    _row_to_ticket_input fabricates observed_at (pred-2h / pred-5min); here the
    measurements are rebuilt from the last observation actually in the cache.
    Unequipped or never-observed sensors are UNAVAILABLE, never imputed values.
    """
    ac_val, ac_obs = (_display(row["ac_voltage_mean_1h"], _last_raw(dev_dir, AC_KEYS, "ac_voltage"), pred_ts, 1)
                      if row["is_ac_sensor_present"] else (None, None))
    b_val, b_obs = (_display(row["battery_voltage_mean_1h"], _last_raw(dev_dir, BATT_KEYS, "battery_voltage"), pred_ts, 2)
                    if row["is_battery_sensor_present"] else (None, None))
    if b_val is not None and b_val <= 0.5:  # R1 sentinel: unequipped/idle channel reads ~0 V
        b_val, b_obs = None, None
    elec_obs = max([t for t in (ac_obs, b_obs, _last_obs_ms(dev_dir, ("statusbox_mains_on", "power_off_last"))) if t]
                   or [0]) or None
    mains_h: Optional[float] = round(float(row["time_since_mains_loss_hours"]), 1)
    # ponytail: 168.0 h is the builder's "no mains loss in window" fill, not a measurement.
    if mains_h == 168.0:
        mains_h, elec_obs = None, None
    shown = {**row, "timestamp": pred_ts.isoformat()}
    if ac_val is not None:
        shown["ac_voltage_mean_1h"] = ac_val          # facts line must cite the displayed value
    if b_val is not None:
        shown["battery_voltage_mean_1h"] = b_val
    if mains_h is not None:
        shown["time_since_mains_loss_hours"] = mains_h
    base = _row_to_ticket_input(pd.Series(shown))
    measurements = [
        _measurement("ac_voltage", ac_val, "V", ac_obs, pred_ts),
        _measurement("battery_voltage", b_val, "V", b_obs, pred_ts),
        _measurement("time_since_mains_loss", mains_h, "hours", elec_obs, pred_ts),
    ]
    fields = {k: getattr(base, k) for k in base.__dataclass_fields__}
    fields["measurements"] = measurements
    # Re-derive causes from the REAL measurements (the base used fabricated freshness).
    fields["possible_causes"] = derive_possible_causes(
        base.device_state, base.risk_tier, measurements, base.triggered_rules)
    return TicketInput.build(**fields)


def score_device(
    rel: Release,
    device: Dict[str, str],
    clipped_dir: Path,
    alarms: pd.DataFrame,
    cutoff: datetime,
    pred_ts: datetime,
) -> Dict[str, Any]:
    """Score one device from point-in-time data (all inputs have ts < cutoff)."""
    slot = cutoff - SLOT  # last COMPLETE bin: [slot, cutoff)
    feats = build_remediated_device_features(
        device_id=device["device_id"], device_name=device["name"], bank=device["bank"],
        raw_dev_dir=clipped_dir, outage_spans=[], alarms_dev_df=alarms,
        start_dt=slot - LOOKBACK, end_dt=slot, resample_interval="15min",
    )
    row = feats.iloc[-1].to_dict()
    x = np.array([[row[c] for c in CLEAN_FEATURES]], dtype=np.float64)
    x = np.nan_to_num(x, nan=0.0)  # matches training: .fillna(0.0)
    raw_p = float(rel.model.predict_proba(x)[:, 1][0])
    prob = float(rel.calibrator.predict_proba(np.array([[raw_p]]))[:, 1][0])
    state = str(row["device_state"])
    surge = bool(row["ac_voltage_max_1h"] > 250.0)
    tier, conn_alert = assign_risk_tier(prob, state, ac_surge=surge)
    rules = []
    if surge:
        rules.append("RULE_AC_SURGE_OVER_250V")
    if row["ac_voltage_min_1h"] < 50.0:
        rules.append("RULE_AC_UNDER_50V")
    if row["time_since_mains_loss_hours"] <= 1.0:
        rules.append("RULE_MAINS_LOSS_RECENT")

    tick_row = {**row, "risk_tier": tier, "calibrated_prob": prob,
                "rules_triggered_json": json.dumps(rules), "model_version": MODEL_VERSION}
    ti = build_ticket(tick_row, clipped_dir, pred_ts)
    rendered = (render_connectivity_ticket_without_probability(ti) if state in STALE_STATES
                else render_ticket(ti))
    val = validate_candidate(rendered.text, ti)
    feat_vec = {c: (None if pd.isna(row[c]) else float(row[c])) for c in CLEAN_FEATURES}
    return {
        "device_state": state,
        "risk_tier": tier,
        "calibrated_prob": None if state in STALE_STATES else round(prob, 6),
        "raw_model_prob": round(raw_p, 6),
        "connectivity_alert": int(conn_alert),
        "rules_triggered": rules,
        "feature_slot_ts": slot.isoformat(),
        "feature_vector_hash": hashlib.sha256(json.dumps(feat_vec, sort_keys=True).encode()).hexdigest(),
        "telemetry_age_seconds": None,  # filled by caller from cache
        "heartbeat_gap_seconds": float(row["ablation_heartbeat_gap_seconds"]),  # diagnostic only, not a model input
        "input_payload_hash": rendered.payload_hash,
        "ticket_id": rendered.ticket_id,
        "ticket_hash": hashlib.sha256(rendered.text.encode("utf-8")).hexdigest(),
        "rendered_ticket": rendered.text,
        "validator_valid": bool(val.is_valid),
        "validator_violations": list(val.violations),
    }


# --------------------------------------------------------------------------- #
# Append-only logs
# --------------------------------------------------------------------------- #
DB_COLUMNS = (
    "cycle_id", "prediction_time", "device_id", "model_version", "policy_version",
    "input_payload_hash", "ticket_hash", "validator_valid", "risk_tier", "device_state",
    "calibrated_prob", "input_complete", "action_taken", "record_json",
)


def init_db() -> None:
    con = sqlite3.connect(LOG_DB)
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS predictions (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            {", ".join(c + " TEXT" for c in DB_COLUMNS)},
            UNIQUE (cycle_id, device_id)
        );
        CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
            BEGIN SELECT RAISE(ABORT, 'append-only log: UPDATE forbidden'); END;
        CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
            BEGIN SELECT RAISE(ABORT, 'append-only log: DELETE forbidden'); END;
    """)
    con.close()


def cycle_already_logged(cycle_id: str) -> bool:
    if not LOG_DB.exists():
        return False
    con = sqlite3.connect(LOG_DB)
    n = con.execute("SELECT COUNT(*) FROM predictions WHERE cycle_id = ?", (cycle_id,)).fetchone()[0]
    con.close()
    return n > 0


def append_records(records: List[Dict[str, Any]]) -> None:
    with open(LOG_JSONL, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    con = sqlite3.connect(LOG_DB)
    con.executemany(
        f"INSERT OR IGNORE INTO predictions ({', '.join(DB_COLUMNS)}) VALUES ({', '.join('?' * len(DB_COLUMNS))})",
        [tuple(json.dumps(r[c]) if c == "record_json" else (None if r.get(c) is None else str(r.get(c)))
               for c in DB_COLUMNS) for r in ({**x, "record_json": x} for x in records)],
    )
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# Cycle
# --------------------------------------------------------------------------- #
def load_fleet() -> List[Dict[str, str]]:
    reg = pd.read_parquet(REGISTRY)
    reg = reg[reg["eligible"] == True]  # noqa: E712
    training = set(pd.read_parquet(TRAINING_MATRIX, columns=["device_id"])["device_id"].astype(str).unique()) \
        if TRAINING_MATRIX.exists() else set()
    return [{"device_id": str(r.device_id), "name": str(r.name), "bank": str(r.bank),
             "in_training_fleet": str(r.device_id) in training} for r in reg.itertuples()]


def run_cycle(source, now: Optional[datetime] = None,
              max_backfill_hours: float = DEFAULT_MAX_BACKFILL_HOURS) -> Dict[str, Any]:
    """One scoring cycle. `source` provides fetch_timeseries / fetch_alarms (read-only).

    max_backfill_hours bounds how much backlog ONE cycle ingests per device
    (oldest-first; the cursor advances by at most this much, so a resume after
    an outage drains gradually instead of issuing one oversized request).
    Values <= 0 mean uncapped (full catch-up; use only after stable cycles).
    Scoring always uses the point-in-time cache below the slot cutoff; during
    a drain the cache is honestly stale until the backlog is consumed.
    """
    import tempfile

    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    cutoff = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15)
    cycle_id = cutoff.isoformat()
    summary: Dict[str, Any] = {"cycle_id": cycle_id, "started": now.isoformat(), "status": "STARTED"}
    stage_holder = ["startup_checks"]
    try:
        return _run_cycle_body(source, now, cutoff, cycle_id, summary, max_backfill_hours,
                               stage_holder=stage_holder)
    except BaseException as e:
        LOG.exception("cycle %s crashed in stage %s", cycle_id, stage_holder[0])
        _flush_logs()
        _write_crash_marker(cycle_id, stage_holder[0], e)
        raise


def _run_cycle_body(source, now: datetime, cutoff: datetime, cycle_id: str,
                    summary: Dict[str, Any], max_backfill_hours: float,
                    stage_holder: List[str]) -> Dict[str, Any]:
    import tempfile

    def _stage(name: str) -> None:
        stage_holder[0] = name
        LOG.info("cycle %s stage: %s", cycle_id, name)
        _flush_logs()

    _stage("startup_checks")

    if STOP_FILE.exists():
        summary["status"] = "STOP_FILE_PRESENT"
        return summary
    if llm_bridge.is_experimental_llm_enabled():
        summary["status"] = "REFUSED_LLM_ENABLED"
        return summary
    try:
        gate_a.require_approved()
    except gate_a.GateANotApproved as e:
        LOG.warning("cycle %s refused: Gate A approval missing/incomplete: %s", cycle_id, "; ".join(e.reasons))
        _flush_logs()
        summary["status"] = "REFUSED_GATE_A_APPROVAL"
        summary["gate_a_reasons"] = list(e.reasons)
        return summary
    init_db()
    if cycle_already_logged(cycle_id):
        summary["status"] = "SKIPPED_ALREADY_LOGGED"
        return summary

    rel = load_release()  # raises -> caller logs FAILED_STARTUP_CHECK
    fleet = load_fleet()
    floor = cutoff - LOOKBACK - timedelta(days=1)
    floor_ms = int(floor.timestamp() * 1000)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    cap_ms = (int(max_backfill_hours * 3600 * 1000)
              if max_backfill_hours and max_backfill_hours > 0 else None)
    LOG.info("cycle %s starting: %d devices, cutoff %s, max_backfill_hours=%s",
             cycle_id, len(fleet), cutoff.isoformat(),
             "uncapped" if cap_ms is None else max_backfill_hours)
    _flush_logs()
    _stage("fetch")
    state = _load_state()
    _seed_alarms(floor.isoformat())

    fetch_fail = alarm_fail = capped = 0
    fail_msgs: List[str] = []
    new_alarms: List[Dict[str, Any]] = []
    complete: Dict[str, bool] = {}
    for i, dev in enumerate(fleet):
        did = dev["device_id"]
        if did not in state:
            state[did] = _seed_device(did, floor_ms)
        complete[did] = False
        fetch_start = state[did] + 1
        fetch_end = cutoff_ms - 1
        if cap_ms is not None and fetch_end - fetch_start > cap_ms:
            fetch_end = fetch_start + cap_ms  # oldest-first: drain backlog gradually
            capped += 1
        try:
            pts = source.fetch_timeseries(did, FEATURE_KEYS, fetch_start, fetch_end)
            _append_points(did, pts, floor_ms)
            state[did] = fetch_end
        except Exception as e:  # device-level fetch failure: logged, scored as incomplete
            LOG.warning("telemetry fetch failed for %s: %s: %s", did, type(e).__name__, str(e)[:200])
            fetch_fail += 1
            fail_msgs.append(f"telemetry {type(e).__name__}: {str(e)[:120]}")
            continue
        try:
            new_alarms += source.fetch_alarms(did, cutoff_ms - int(ALARM_REFETCH.total_seconds() * 1000), cutoff_ms - 1)
            complete[did] = True
        except Exception as e:
            LOG.warning("alarm fetch failed for %s: %s: %s", did, type(e).__name__, str(e)[:200])
            alarm_fail += 1
            fail_msgs.append(f"alarms {type(e).__name__}: {str(e)[:120]}")
    _upsert_alarms(new_alarms)
    _save_state(state)
    LOG.info("cycle %s fetch done: %d telemetry failures, %d alarm failures, %d devices backfill-capped",
             cycle_id, fetch_fail, alarm_fail, capped)
    _flush_logs()
    _stage("scoring")

    alarms = pd.read_parquet(ALARM_CACHE) if ALARM_CACHE.exists() else pd.DataFrame(columns=ALARM_COLUMNS)
    alarms = alarms[pd.to_datetime(alarms["start"], format="ISO8601", utc=True, errors="coerce") < pd.Timestamp(cutoff)]
    pred_ts = now  # wall-clock scoring time; all inputs have ts < cutoff <= pred_ts
    records: List[Dict[str, Any]] = []
    errors = 0
    with tempfile.TemporaryDirectory(dir=LIVE_DIR) as tmp:
        for j, dev in enumerate(fleet):
            if j and j % 40 == 0:
                LOG.info("cycle %s scoring %d/%d", cycle_id, j, len(fleet))
            did = dev["device_id"]
            base = {
                "schema_version": SCHEMA_VERSION, "cycle_id": cycle_id,
                "prediction_time": pred_ts.isoformat(), "data_cutoff_ts": cutoff.isoformat(),
                "device_id": did, "device_name": dev["name"], "bank": dev["bank"],
                "in_training_fleet": dev["in_training_fleet"], "fleet_definition": "registry_eligible",
                "model_version": MODEL_VERSION, "model_sha256": rel.model_sha256,
                "calibrator_sha256": rel.calibrator_sha256, "policy_version": POLICY_VERSION,
                "release_config_sha256": rel.release_config_sha256, "git_commit": rel.git_commit,
                "input_complete": complete.get(did, False), "llm_used": False,
                "tls_verify": getattr(source, "verify_tls", None), "action_taken": ACTION_TAKEN,
            }
            try:
                clipped = _clipped_device_dir(did, cutoff_ms, Path(tmp))
                dev_alarms = alarms[alarms["device_id"].astype(str) == did]
                out = score_device(rel, dev, clipped, dev_alarms, cutoff, pred_ts)
                last = _last_obs_ms(clipped, FEATURE_KEYS)
                out["last_telemetry_ts"] = _iso_ms(last)
                out["telemetry_age_seconds"] = None if last is None else round((cutoff_ms - last) / 1000.0, 1)
                records.append({**base, **out, "scoring_error": None})
            except Exception as e:
                errors += 1
                LOG.exception("scoring failed for %s", did)
                records.append({**base, "scoring_error": f"{type(e).__name__}: {e}"[:300],
                                "input_payload_hash": None, "ticket_hash": None, "validator_valid": None,
                                "risk_tier": None, "device_state": None, "calibrated_prob": None,
                                "rendered_ticket": None, "validator_violations": None})
    _stage("persistence")
    append_records(records)
    tiers = pd.Series([r["risk_tier"] for r in records]).value_counts(dropna=False).to_dict()
    degraded = fetch_fail or alarm_fail or errors
    summary.update({
        "status": "DEGRADED" if degraded else "OK", "devices": len(fleet),
        "fetch_failures": fetch_fail, "alarm_fetch_failures": alarm_fail,
        "backfill_capped_devices": capped,
        "max_backfill_hours": max_backfill_hours,
        "complete_input_devices": sum(complete.values()),
        "failure_samples": sorted(set(fail_msgs))[:5], "scoring_errors": errors,
        "validator_failures": sum(1 for r in records if r.get("validator_valid") is False),
        "tiers": {str(k): int(v) for k, v in tiers.items()}, "llm_used": 0, "alarms_created": 0,
        "dispatches": 0, "notifications": 0, "finished": datetime.now(timezone.utc).isoformat(),
    })
    _flush_logs()
    return summary


# --------------------------------------------------------------------------- #
# Entry point: lock, cycle, cycle log
# --------------------------------------------------------------------------- #
def _flush_logs() -> None:
    """Flush every attached log handler (best-effort; keeps evidence on hard kills)."""
    try:
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass
    except Exception:
        pass


def _write_crash_marker(cycle_id: Optional[str], stage: str, exc: BaseException) -> None:
    """Best-effort crash marker (latest crash only). Never raises."""
    try:
        import traceback as _tb
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        marker = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "cycle_id": cycle_id,
            "stage": stage,
            "exc_type": type(exc).__name__,
            "error": str(exc)[:500],
            "traceback": "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
        }
        tmp = CRASH_MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(marker, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, CRASH_MARKER)
    except Exception:
        pass


def _acquire_lock() -> bool:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < LOCK_STALE_AFTER.total_seconds():
            return False
        LOG.warning("removing stale lock (%.0fs old)", age)
        LOCK_FILE.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def run_once(source_factory=ThingsBoardReadOnly.from_env,
             max_backfill_hours: float = DEFAULT_MAX_BACKFILL_HOURS) -> int:
    if not _acquire_lock():
        LOG.info("another cycle holds the lock; exiting")
        return 0
    summary: Dict[str, Any] = {}
    try:
        try:
            summary = run_cycle(source_factory(), max_backfill_hours=max_backfill_hours)
        except (KeyboardInterrupt, SystemExit):
            # Crash marker already written by run_cycle; flush and re-raise so the
            # exit code still reflects abnormal termination. Do not swallow.
            _flush_logs()
            raise
        except Exception as e:
            summary = {"status": "FAILED", "error": f"{type(e).__name__}: {e}"[:300],
                       "started": datetime.now(timezone.utc).isoformat()}
            LOG.exception("cycle failed")
        with open(CYCLES_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, sort_keys=True) + "\n")
        LOG.info("cycle %s", json.dumps(summary, sort_keys=True))
        if summary.get("status") == "DEGRADED" and summary.get("complete_input_devices", 0) == 0:
            return 1  # every device failed: not a usable cycle
        return 0 if summary.get("status") in ("OK", "DEGRADED", "SKIPPED_ALREADY_LOGGED", "STOP_FILE_PRESENT") else 1
    finally:
        LOCK_FILE.unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    ap.add_argument("--log-file", type=Path, default=None,
                    help="append logs here (use with pythonw.exe: no console window to close)")
    ap.add_argument("--max-backfill-hours", type=float, default=DEFAULT_MAX_BACKFILL_HOURS,
                    help="per-device backlog ingested per cycle, oldest-first (default %(default)s). "
                         "<= 0 means uncapped full catch-up; raise gradually (1, 6, 24, 0) only "
                         "after consecutive stable cycles.")
    args = ap.parse_args(argv)
    # NOTE: src.features.build_remediated_features configures root logging at
    # import time, so logging.basicConfig() here would be a no-op and --log-file
    # would silently capture nothing (seen with pythonw.exe: 0-byte log files).
    # Attach the file handler explicitly instead.
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if args.log_file:
        fh = logging.FileHandler(args.log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.INFO)
        root.addHandler(fh)
    if sys.stdout is not None and not any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            for h in root.handlers):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    if args.once:
        return run_once(max_backfill_hours=args.max_backfill_hours)
    while not STOP_FILE.exists():
        run_once(max_backfill_hours=args.max_backfill_hours)
        now = datetime.now(timezone.utc)
        nxt = now.replace(second=30, microsecond=0) + timedelta(minutes=15 - now.minute % 15)
        time.sleep(max(5.0, (nxt - datetime.now(timezone.utc)).total_seconds()))
    LOG.info("STOP file present; exiting loop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
