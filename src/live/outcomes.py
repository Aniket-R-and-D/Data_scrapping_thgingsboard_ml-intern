"""src/live/outcomes.py — G2 delayed outcome evaluator and interim reports.

Rules (never use future data for features; labels only after the window closes):
- A prediction made at time t has outcome window (t, t + 24h].
- It is MATURE only when now >= t + 24h + SETTLE and the device's live fetch
  cursor has passed t + 24h (complete follow-up). Otherwise PENDING or
  INCOMPLETE; those rows are excluded from every metric.
- Power outcome uses the R2 label definition (configs/config.yaml outage_label),
  re-implemented here per device and tested against
  data/processed/remediated_power_outage_events.parquet.
- Connectivity outcome: any telemetry gap > 60 min inside the window
  (offline episode) and whether any telemetry arrived (recovered).

Reports are aggregate-only (no device names or IDs): reports/ is public.

Usage:
    python -m src.live.outcomes --report day1     # writes reports/live_shadow_interim_day1.{md,json}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live import shadow_run as sr

CONFIG = ROOT / "configs" / "config.yaml"
REPORTS = ROOT / "reports"
WINDOW = timedelta(hours=24)
SETTLE = timedelta(hours=1)
OFFLINE_GAP = timedelta(minutes=60)
POWER_ALERT_TIERS = ("HIGH", "CRITICAL")


# --------------------------------------------------------------------------- #
# R2 label definition, per device
# --------------------------------------------------------------------------- #
def _series_15m(df: pd.DataFrame, battery: bool = False) -> Optional[pd.Series]:
    if df is None or df.empty:
        return None
    d = pd.DataFrame({"dt": pd.to_datetime(df["ts"], unit="ms", utc=True, errors="coerce"),
                      "val": pd.to_numeric(df["value"], errors="coerce")}).dropna()
    if d.empty or (battery and d["val"].max() <= 0.5):
        return None
    return d.set_index("dt")["val"].resample("15min").mean()


def outage_events(ac: pd.DataFrame, batt: pd.DataFrame, mains: pd.DataFrame,
                  dev_alarms: pd.DataFrame, cfg: Dict[str, Any]) -> List[Tuple[float, float, str]]:
    """Collapsed electrical outage events (start_ms, end_ms, reason) — R2 semantics."""
    sustained = cfg["sustained_minutes"]
    events: List[Tuple[float, float, str]] = []

    if dev_alarms is not None and not dev_alarms.empty:
        pat = "|".join(cfg["electrical_alarm_types"])
        el = dev_alarms[dev_alarms["alarm_type"].astype(str).str.upper().str.contains(pat, regex=True)]
        s = pd.to_datetime(el["start"], format="ISO8601", utc=True, errors="coerce")
        c = pd.to_datetime(el["clear"], format="ISO8601", utc=True, errors="coerce")
        c = c.fillna(s + pd.Timedelta(minutes=sustained))
        s_ms = s.astype("int64") // 1000
        c_ms = np.minimum(c.astype("int64") // 1000, s_ms + 24 * 3600 * 1000)
        dur = (c_ms - s_ms) / 60000.0
        for a_s, a_c, d, t in zip(s_ms, c_ms, dur, el["alarm_type"].astype(str)):
            if d >= sustained and not np.isnan(a_s) and not np.isnan(a_c) and a_c >= a_s:
                events.append((float(a_s), float(a_c), t))

    parts = [x.rename(n) for x, n in ((_series_15m(ac), "ac"), (_series_15m(batt, True), "batt"),
                                      (_series_15m(mains), "mains")) if x is not None]
    if parts:
        comb = pd.concat(parts, axis=1)
        lost = pd.Series(False, index=comb.index)
        if "ac" in comb:
            lost |= comb["ac"].notna() & (comb["ac"] < cfg["ac_outage_volts"])
        if "mains" in comb:
            lost |= comb["mains"].notna() & (comb["mains"] == 0.0)
        if "batt" in comb:
            lost &= comb["batt"].notna() & (comb["batt"] > 0.5) & (comb["batt"] < cfg["battery_critical_volts"])
        for t in comb.index[lost.values]:
            ms = t.timestamp() * 1000.0
            events.append((ms, ms + 15 * 60 * 1000, "PURE_ELECTRICAL_TELEMETRY"))

    if not events:
        return []
    events.sort(key=lambda x: x[0])
    out = []
    cs, ce, cr = events[0]
    for s_, e_, r_ in events[1:]:
        if s_ <= ce + 7200 * 1000:
            ce = max(ce, e_)
            if r_ not in cr:
                cr += f" + {r_}"
        else:
            out.append((cs, ce, cr))
            cs, ce, cr = s_, e_, r_
    out.append((cs, ce, cr))
    return out


def _read(dev_dir: Path, key: str) -> pd.DataFrame:
    p = dev_dir / f"{key}.parquet"
    return pd.read_parquet(p, columns=["ts", "value"]) if p.exists() else pd.DataFrame(columns=["ts", "value"])


def _telemetry_ts(dev_dir: Path) -> np.ndarray:
    ts = [pd.read_parquet(p, columns=["ts"])["ts"].to_numpy() for p in dev_dir.glob("*.parquet")]
    return np.unique(np.concatenate(ts)) if ts else np.array([], dtype=np.int64)


# --------------------------------------------------------------------------- #
# Outcome join
# --------------------------------------------------------------------------- #
def load_predictions() -> pd.DataFrame:
    if not sr.LOG_JSONL.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in sr.LOG_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # A crash between the JSONL and SQLite writes can re-run a cycle: keep the first record.
    df = df.drop_duplicates(["cycle_id", "device_id"], keep="first").reset_index(drop=True)
    df["pt"] = pd.to_datetime(df["prediction_time"], utc=True)
    return df


def assign_outcomes(preds: pd.DataFrame, now: datetime) -> pd.DataFrame:
    with open(CONFIG, encoding="utf-8") as f:
        label_cfg = yaml.safe_load(f)["outage_label"]
    state = json.loads(sr.FETCH_STATE.read_text()) if sr.FETCH_STATE.exists() else {}
    alarms = pd.read_parquet(sr.ALARM_CACHE) if sr.ALARM_CACHE.exists() else pd.DataFrame(columns=sr.ALARM_COLUMNS)
    df = preds.copy()
    df["outcome_status"] = "PENDING"
    df["y_power_outage_24h"] = pd.NA
    df["offline_episode_24h"] = pd.NA
    df["recovered_24h"] = pd.NA

    win_end = df["pt"] + WINDOW
    matured_time = win_end + SETTLE <= pd.Timestamp(now)
    cursor_ok = [state.get(d, -1) >= int(w.timestamp() * 1000) for d, w in zip(df["device_id"], win_end)]
    ok_input = df["input_complete"].fillna(False).astype(bool) & df["scoring_error"].isna()
    df.loc[matured_time & ~(ok_input & pd.Series(cursor_ok, index=df.index)), "outcome_status"] = "INCOMPLETE"
    mature = matured_time & ok_input & pd.Series(cursor_ok, index=df.index)
    df.loc[mature, "outcome_status"] = "MATURE"

    for did, idx in df[mature].groupby("device_id").groups.items():
        dev_dir = sr.CACHE_DIR / did
        ev = outage_events(_read(dev_dir, "ac_voltage"), _read(dev_dir, "battery_voltage"),
                           _read(dev_dir, "statusbox_mains_on"),
                           alarms[alarms["device_id"].astype(str) == did], label_cfg)
        starts = np.array([e[0] for e in ev])
        tel = _telemetry_ts(dev_dir)
        for i in idx:
            t0 = df.at[i, "pt"].timestamp() * 1000.0
            t1 = t0 + WINDOW.total_seconds() * 1000.0
            df.at[i, "y_power_outage_24h"] = int(np.any((starts > t0) & (starts <= t1))) if len(starts) else 0
            inwin = tel[(tel > t0) & (tel <= t1)]
            pts = np.concatenate([[t0], inwin, [t1]])
            df.at[i, "offline_episode_24h"] = int(np.max(np.diff(pts)) > OFFLINE_GAP.total_seconds() * 1000)
            df.at[i, "recovered_24h"] = int(len(inwin) > 0)
    return df


def _power_metrics(m: pd.DataFrame) -> Dict[str, Any]:
    p = m[(m["risk_tier"] != "CONNECTIVITY_ISSUE") & m["in_training_fleet"].astype(bool)]
    if p.empty:
        return {"rows": 0}
    y = p["y_power_outage_24h"].astype(int)
    alert = p["risk_tier"].isin(POWER_ALERT_TIERS)
    tp = int((alert & (y == 1)).sum())
    return {
        "rows": int(len(p)), "positives": int(y.sum()), "alerts_high_or_critical": int(alert.sum()),
        "true_positive_alerts": tp,
        "precision_high_or_critical": round(tp / alert.sum(), 4) if alert.sum() else None,
        "recall_high_or_critical": round(tp / y.sum(), 4) if y.sum() else None,
    }


def _conn_metrics(m: pd.DataFrame) -> Dict[str, Any]:
    c = m[m["risk_tier"] == "CONNECTIVITY_ISSUE"]
    return {"connectivity_alert_rows": int(len(c)),
            "recovered_within_24h": int(c["recovered_24h"].astype(int).sum()) if len(c) else 0}


def summarize(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cycles = ([json.loads(l) for l in sr.CYCLES_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
              if sr.CYCLES_JSONL.exists() else [])
    preds = load_predictions()
    out: Dict[str, Any] = {"generated_utc": now.isoformat(), "cycles_logged": len(cycles),
                           "cycle_status_counts": pd.Series([c.get("status") for c in cycles]).value_counts().to_dict()}
    if preds.empty:
        out["prediction_rows"] = 0
        return out
    df = assign_outcomes(preds, now)
    first = df["pt"].min()
    expected = int((pd.Timestamp(now) - first) / pd.Timedelta(minutes=15)) + 1
    scored_cycles = df["cycle_id"].nunique()
    m = df[df["outcome_status"] == "MATURE"]
    out.update({
        "first_prediction_utc": first.isoformat(), "last_prediction_utc": df["pt"].max().isoformat(),
        "expected_cycles_since_first": expected, "scored_cycles": int(scored_cycles),
        "cycle_coverage": round(scored_cycles / expected, 4),
        "prediction_rows": int(len(df)), "devices": int(df["device_id"].nunique()),
        "devices_in_training_fleet": int(df.loc[df["in_training_fleet"].astype(bool), "device_id"].nunique()),
        "tier_counts": {str(k): int(v) for k, v in df["risk_tier"].value_counts(dropna=False).items()},
        "state_counts": {str(k): int(v) for k, v in df["device_state"].value_counts(dropna=False).items()},
        "validator_failures": int((df["validator_valid"] == False).sum()),  # noqa: E712
        "scoring_errors": int(df["scoring_error"].notna().sum()),
        "incomplete_input_rows": int((~df["input_complete"].fillna(False).astype(bool)).sum()),
        "llm_used_rows": int(df["llm_used"].astype(bool).sum()),
        "action_taken_values": sorted(df["action_taken"].dropna().unique().tolist()),
        "outcome_status_counts": df["outcome_status"].value_counts().to_dict(),
        "mature_rows": int(len(m)),
    })
    if len(m):
        out["overall"] = {"power": _power_metrics(m), "connectivity": _conn_metrics(m)}
        m = m.assign(week=m["pt"].dt.strftime("%G-W%V"))
        out["weekly"] = {w: {"power": _power_metrics(g), "connectivity": _conn_metrics(g)}
                         for w, g in m.groupby("week")}
        trail = m[m["pt"] >= pd.Timestamp(now) - pd.Timedelta(days=8)]
        out["trailing_7d"] = {"power": _power_metrics(trail), "connectivity": _conn_metrics(trail)}
    return out


def write_report(label: str, now: Optional[datetime] = None) -> Path:
    s = summarize(now)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"live_shadow_interim_{label}.json").write_text(json.dumps(s, indent=2, default=str))
    lines = [f"# Live shadow interim report — {label}", "",
             f"Generated (UTC): {s['generated_utc']}", "",
             "Aggregate only; no device identifiers. Mode: read-only, LOG_ONLY_NO_DISPATCH.", "",
             "## Operational status", ""]
    for k in ("first_prediction_utc", "last_prediction_utc", "cycles_logged", "cycle_status_counts",
              "expected_cycles_since_first", "scored_cycles", "cycle_coverage", "devices",
              "devices_in_training_fleet", "prediction_rows", "tier_counts", "state_counts",
              "validator_failures", "scoring_errors", "incomplete_input_rows", "llm_used_rows",
              "action_taken_values"):
        if k in s:
            lines.append(f"- {k}: {s[k]}")
    lines += ["", "## Outcomes (24h window + 1h settle; complete follow-up only)", ""]
    lines.append(f"- outcome_status_counts: {s.get('outcome_status_counts', {})}")
    if s.get("mature_rows"):
        lines.append(f"- overall: {s['overall']}")
        lines.append(f"- trailing_7d: {s['trailing_7d']}")
        for w, v in s["weekly"].items():
            lines.append(f"- week {w}: {v}")
    else:
        lines.append("- 0 outcome-labelled rows: no prediction has completed its 24h window. "
                     "No accuracy metrics are reported.")
    path = REPORTS / f"live_shadow_interim_{label}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, help="label, e.g. day1, day3, day7, final")
    print(write_report(ap.parse_args(argv).report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
