"""src/eval/connectivity_episodes.py — connectivity alert decomposition (read-only, local data).

Reconstructs, for every registry-eligible device, the device_state the live
shadow scorer would have assigned at every 15-minute slot of the last 90 days,
using the same feature builder, the same point-in-time cache view (ts < cutoff)
and the same state machine. No ThingsBoard access; reads data/live only.

Outputs:
  reports/connectivity_alert_decomposition.md     aggregate only (public)
  data/live/analysis/connectivity_episodes.csv    per-episode detail (local, gitignored)
  data/live/analysis/connectivity_devices.csv     per-device classification (local)

Usage:
    python -m src.eval.connectivity_episodes --cutoff 2026-10-14T12:00:00+00:00
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.build_remediated_features import build_remediated_device_features
from src.live import outcomes, shadow_run as sr

SLOT_MIN = 15
STATE_KEYS = ("ac_voltage", "ac_result", "ac_status", "battery_voltage", "battery_status",
              "statusbox_mains_on", "power_off_last", "uptimeHeartbeat", "heartbeat",
              "gatewayUptime", "lastActivityTime")   # series that feed telemetry age (any_telem)
OTHER_KEYS = tuple(k for k in sr.FEATURE_KEYS if k not in STATE_KEYS)
INCIDENT_MIN_SLOTS = 5      # connectivity incident = episode lasting > 60 min (>= 5 slots)
WARMUP_SLOTS = 96           # first 24h of the grid: telemetry age is censored (no prior obs)
CONN = ("STALE_DATA", "OFFLINE_LONG")
OUT_LOCAL = ROOT / "data" / "live" / "analysis"
REPORT = ROOT / "reports" / "connectivity_alert_decomposition.md"


def reconstruct_states(cutoff: datetime) -> pd.DataFrame:
    fleet = sr.load_fleet()
    alarms = pd.read_parquet(sr.ALARM_CACHE)
    alarms = alarms[pd.to_datetime(alarms["start"], format="ISO8601", utc=True, errors="coerce") < pd.Timestamp(cutoff)]
    cutoff_ms = int(cutoff.timestamp() * 1000)
    slot = cutoff - timedelta(minutes=SLOT_MIN)
    frames = []
    with tempfile.TemporaryDirectory(dir=sr.LIVE_DIR) as tmp:
        for dev in fleet:
            clipped = sr._clipped_device_dir(dev["device_id"], cutoff_ms, Path(tmp))
            f = build_remediated_device_features(
                device_id=dev["device_id"], device_name=dev["name"], bank=dev["bank"],
                raw_dev_dir=clipped, outage_spans=[],
                alarms_dev_df=alarms[alarms["device_id"].astype(str) == dev["device_id"]],
                start_dt=slot - sr.LOOKBACK, end_dt=slot, resample_interval="15min")
            frames.append(pd.DataFrame({
                "device_id": dev["device_id"], "in_training_fleet": dev["in_training_fleet"],
                "slot": f.index, "state": f["device_state"].values}))
    return pd.concat(frames, ignore_index=True)


def _power_events(did: str) -> np.ndarray:
    """R2-definition electrical outage events (start_ms, end_ms) from the local cache."""
    cfg = yaml.safe_load(outcomes.CONFIG.read_text(encoding="utf-8"))["outage_label"]
    d = sr.CACHE_DIR / did
    al = pd.read_parquet(sr.ALARM_CACHE)
    ev = outcomes.outage_events(outcomes._read(d, "ac_voltage"), outcomes._read(d, "battery_voltage"),
                                outcomes._read(d, "statusbox_mains_on"),
                                al[al["device_id"].astype(str) == did], cfg)
    return np.array([(s, e) for s, e, _ in ev]) if ev else np.zeros((0, 2))


def episodes(states: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for did, g in states.groupby("device_id", sort=False):
        pev = _power_events(did)
        s = g["state"].to_numpy()
        conn = np.isin(s, CONN)
        n = len(s)
        i = 0
        prev_end = None
        while i < n:
            if not conn[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and conn[j + 1]:
                j += 1
            on_ms = g["slot"].iat[i].timestamp() * 1000
            off_ms = (g["slot"].iat[j].timestamp() + SLOT_MIN * 60) * 1000
            overlap = bool(len(pev) and np.any((pev[:, 0] <= off_ms) & (pev[:, 1] >= on_ms - 7200_000)))
            rows.append({
                "device_id": did, "in_training_fleet": bool(g["in_training_fleet"].iat[0]),
                "start_idx": i, "end_idx": j, "start": g["slot"].iat[i], "end": g["slot"].iat[j],
                "slots": j - i + 1, "duration_h": (j - i + 1) * SLOT_MIN / 60.0,
                "left_censored": i < WARMUP_SLOTS, "right_censored": j == n - 1,
                "recovered": j < n - 1,
                "reached_offline_long": bool((s[i:j + 1] == "OFFLINE_LONG").any()),
                "overlaps_power_outage_event": overlap,   # R2 event within [onset-2h, end]
                "continuation_of_prior": prev_end is not None and (i - prev_end) <= 8,  # <= 2h online between
                "trigger": "telemetry_age>60min (state machine; heartbeat keys included)",
            })
            prev_end = j
            i = j + 1
    return pd.DataFrame(rows)


def classify_devices(states: pd.DataFrame, cutoff: datetime) -> pd.DataFrame:
    cutoff_ms = int(cutoff.timestamp() * 1000)
    last_slot = states.groupby("device_id")["slot"].transform("max")
    current = states[states["slot"] == last_slot].set_index("device_id")["state"]
    week = states[states["slot"] > states["slot"].max() - pd.Timedelta(days=7)]
    online_7d = week.assign(on=~week["state"].isin(CONN)).groupby("device_id")["on"].sum()
    alarms = pd.read_parquet(sr.ALARM_CACHE)
    a_start = pd.to_datetime(alarms["start"], format="ISO8601", utc=True, errors="coerce")
    recent_alarm = set(alarms.loc[(a_start >= pd.Timestamp(cutoff) - pd.Timedelta(hours=1)) &
                                  (a_start < pd.Timestamp(cutoff)), "device_id"].astype(str))
    rows = []
    for did, st in current.items():
        if st not in CONN:
            continue
        d = sr.CACHE_DIR / did
        def last(keys):
            ts = [pd.read_parquet(d / f"{k}.parquet", columns=["ts"])["ts"] for k in keys if (d / f"{k}.parquet").exists()]
            ts = [t[t < cutoff_ms] for t in ts]
            ts = [int(t.max()) for t in ts if len(t)]
            return max(ts) if ts else None
        last_state, last_other = last(STATE_KEYS), last(OTHER_KEYS)
        fresh_other = last_other is not None and cutoff_ms - last_other <= 3600_000
        fresh_keys = sorted(k for k in OTHER_KEYS if (d / f"{k}.parquet").exists() and
                            cutoff_ms - int(pd.read_parquet(d / f"{k}.parquet", columns=["ts"])["ts"].max()) <= 3600_000)
        if last_state is None and last_other is None and did not in recent_alarm:
            cls = "insufficient_evidence"          # nothing at all in the 90-day window
        elif fresh_other or did in recent_alarm:
            cls = "online_but_misclassified"       # alive on keys/alarms the state machine ignores
        elif online_7d.get(did, 0) > 0:
            cls = "intermittent"                   # online at least once in the last 7 days
        else:
            cls = "genuinely_stale_offline"
        rows.append({"device_id": did, "current_state": st, "classification": cls,
                     "state_key_age_h": None if last_state is None else round((cutoff_ms - last_state) / 3.6e6, 2),
                     "other_key_age_h": None if last_other is None else round((cutoff_ms - last_other) / 3.6e6, 2),
                     "fresh_non_state_keys": ",".join(fresh_keys), "alarm_in_last_1h": did in recent_alarm,
                     "online_slots_last_7d": int(online_7d.get(did, 0))})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Suppression policies (simulated on per-device slot sequences; one alert = one ticket)
# --------------------------------------------------------------------------- #
def simulate(states: pd.DataFrame, policy: Dict[str, Any]) -> Dict[str, Any]:
    alerts, ttfa, missed, incidents, dev_set = 0, [], 0, 0, set()
    cooldown = policy.get("cooldown_slots")
    min_slots = policy.get("min_slots", 1)
    per_episode = policy.get("per_episode", False)
    for did, g in states.groupby("device_id", sort=False):
        conn = g["state"].isin(CONN).to_numpy()
        n = len(conn)
        last_alert = -10**9
        i = 0
        while i < n:
            if not conn[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and conn[j + 1]:
                j += 1
            in_window = i >= WARMUP_SLOTS      # incidents: onset inside the window
            first = None
            if per_episode:
                k = i + min_slots - 1
                if k <= j and (cooldown is None or k - last_alert >= cooldown):
                    first, last_alert = k, k
                    alerts += k >= WARMUP_SLOTS
            else:
                for k in range(i + min_slots - 1, j + 1):
                    if cooldown is None or k - last_alert >= cooldown:
                        alerts += k >= WARMUP_SLOTS   # alerts: every alert slot inside the window
                        first = k if first is None else first
                        last_alert = k
            if in_window and j - i + 1 >= INCIDENT_MIN_SLOTS:
                incidents += 1
                if first is None:
                    missed += 1
                else:
                    ttfa.append((first - i) * SLOT_MIN)
                    dev_set.add(did)
            i = j + 1
    return {"alerts": alerts, "incidents": incidents, "missed_incidents": missed,
            "mean_time_to_first_alert_min": round(float(np.mean(ttfa)), 1) if ttfa else None}


POLICIES = [
    ("P0 raw (every 15-min cycle)", {}),
    ("P1a min duration 30 min, once per episode", {"min_slots": 3, "per_episode": True}),
    ("P1b min duration 60 min, once per episode", {"min_slots": 5, "per_episode": True}),
    ("P2a cooldown 6 h", {"cooldown_slots": 24}),
    ("P2b cooldown 12 h", {"cooldown_slots": 48}),
    ("P3a escalate after N=4 consecutive missed cycles", {"min_slots": 4, "per_episode": True}),
    ("P3b escalate after N=8 consecutive missed cycles", {"min_slots": 8, "per_episode": True}),
    ("P4 min 60 min + 12 h cooldown", {"min_slots": 5, "cooldown_slots": 48}),
]


def run(cutoff: datetime) -> Dict[str, Any]:
    states = reconstruct_states(cutoff)
    ep = episodes(states)
    dev = classify_devices(states, cutoff)
    OUT_LOCAL.mkdir(parents=True, exist_ok=True)
    ep.to_csv(OUT_LOCAL / "connectivity_episodes.csv", index=False)
    dev.to_csv(OUT_LOCAL / "connectivity_devices.csv", index=False)

    tf = states[states["in_training_fleet"]]
    window_slots = tf.groupby("device_id").size().iloc[0] - WARMUP_SLOTS
    weeks = window_slots * SLOT_MIN / (60 * 24 * 7)
    n_tf = tf["device_id"].nunique()
    dw = n_tf * weeks
    ew = ep[(ep["in_training_fleet"]) & (~ep["left_censored"])]
    raw_alert_slots = int(tf.iloc[:, :].groupby("device_id")["state"].apply(
        lambda s: s.iloc[WARMUP_SLOTS:].isin(CONN).sum()).sum())
    res: Dict[str, Any] = {
        "cutoff": cutoff.isoformat(), "window_days_after_warmup": round(weeks * 7, 2),
        "training_fleet_devices": n_tf, "device_weeks": round(dw, 2),
        "device_classification": dev["classification"].value_counts().to_dict(),
        "device_classification_by_state": {f"{a}/{b}": int(v) for (a, b), v in
                                           dev.groupby(["current_state", "classification"]).size().items()},
        "fresh_non_state_keys_seen": pd.Series(",".join(dev["fresh_non_state_keys"]).split(",")).replace("", np.nan).dropna().value_counts().to_dict(),
        "raw_15min_alerts_in_window": raw_alert_slots,
        "episodes_in_window": int(len(ew)),
        "left_censored_episodes_excluded": int((ep["in_training_fleet"] & ep["left_censored"]).sum()),
        "devices_with_episode": int(ew["device_id"].nunique()),
        "episodes_per_device_per_week": round(len(ew) / dw, 3),
        "duration_h_quantiles": {str(q): round(float(ew["duration_h"].quantile(q)), 2) for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)} if len(ew) else {},
        "episodes_over_1h": int((ew["duration_h"] > 1).sum()),
        "episodes_over_6h": int((ew["duration_h"] > 6).sum()),
        "episodes_over_24h": int((ew["duration_h"] > 24).sum()),
        "episodes_ongoing_at_cutoff": int(ew["right_censored"].sum()),
        "episodes_recovered": int(ew["recovered"].sum()),
        "episodes_reaching_offline_long": int(ew["reached_offline_long"].sum()),
        "episodes_overlapping_power_outage_event": int(ew["overlaps_power_outage_event"].sum()),
        "devices_never_reporting_in_window": int(dev.loc[dev["classification"] == "insufficient_evidence", "device_id"].isin(
            states.loc[states["in_training_fleet"], "device_id"]).sum()),
        "episodes_continuing_prior_within_2h": int(ew["continuation_of_prior"].sum()),
        "mean_raw_alerts_per_episode": round(float(ew["slots"].mean()), 1) if len(ew) else None,
        "share_of_raw_alerts_from_episodes_over_24h": round(float(ew.loc[ew["duration_h"] > 24, "slots"].sum() / max(1, ew["slots"].sum())), 4),
        "policies": {},
    }
    raw = None
    for name, pol in POLICIES:
        r = simulate(tf, pol)
        raw = raw or r["alerts"]
        r["alerts_per_device_per_week"] = round(r["alerts"] / dw, 3)
        r["duplicate_alert_reduction_vs_raw"] = round(1 - r["alerts"] / raw, 4) if raw else None
        r["expected_weekly_alerts_135_devices"] = round(r["alerts"] / dw * 135, 1)
        res["policies"][name] = r
    return res


def write_report(res: Dict[str, Any]) -> Path:
    p = res["policies"]
    lines = [
        "# Connectivity Alert Decomposition",
        "",
        f"Cutoff (UTC): {res['cutoff']}. Source: local live-shadow cache only (`data/live/`), no ThingsBoard access.",
        "States are reconstructed per 15-minute slot with the live scorer's exact feature builder and",
        "point-in-time view. Aggregates only; per-device detail stays in `data/live/analysis/` (gitignored).",
        "No threshold, model, or alerting behaviour was changed.",
        "",
        f"Training fleet: {res['training_fleet_devices']} devices; analysis window {res['window_days_after_warmup']} days "
        f"after a 24 h warm-up ({res['device_weeks']} device-weeks). Episodes starting in the warm-up are",
        f"left-censored and excluded from rates ({res['left_censored_episodes_excluded']} excluded).",
        "",
        "## 1. Currently alerted devices (CONNECTIVITY_ISSUE at cutoff, all 158 eligible)",
        "",
        "| Classification | Devices | Rule |",
        "|---|---|---|",
    ]
    rules = {
        "genuinely_stale_offline": "no fresh data on any cached key or alarm; not online in the last 7 days",
        "online_but_misclassified": "fresh (<= 60 min) data on keys the state machine ignores, or an alarm in the last hour",
        "intermittent": "online at least once in the last 7 days, stale now",
        "insufficient_evidence": "no telemetry or alarm at all in the 90-day window",
    }
    for k, v in sorted(res["device_classification"].items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} | {rules[k]} |")
    lines += ["", f"By current state: {res['device_classification_by_state']}", "",
              f"Non-state keys that were fresh on misclassified devices: {res['fresh_non_state_keys_seen']}", "",
              "## 2. Episode decomposition (training fleet, in window)", "",
              "| Metric | Value |", "|---|---|"]
    for k in ("raw_15min_alerts_in_window", "episodes_in_window", "devices_with_episode",
              "episodes_per_device_per_week", "mean_raw_alerts_per_episode", "duration_h_quantiles",
              "episodes_over_1h", "episodes_over_6h", "episodes_over_24h", "episodes_ongoing_at_cutoff",
              "episodes_recovered", "episodes_reaching_offline_long",
              "episodes_overlapping_power_outage_event", "episodes_continuing_prior_within_2h",
              "devices_never_reporting_in_window",
              "share_of_raw_alerts_from_episodes_over_24h"):
        lines.append(f"| {k} | {res[k]} |")
    lines += ["", "Trigger: every episode is triggered by telemetry age > 60 min in the state machine "
              "(heartbeat keys count as telemetry); no other signal creates CONNECTIVITY_ISSUE.", "",
              "## 3. Suppression policies (simulated; nothing enabled)", "",
              "Incident = connectivity episode lasting > 60 min with onset inside the window. Alerts are counted "
              "per alert slot inside the window, including slots of episodes that began before it. Time to first "
              "alert is measured from STALE onset, which is itself 60 min after the last telemetry. In 15-minute slot space a minimum-duration rule "
              "and an N-consecutive-missed-cycles rule are the same rule family; both are shown as requested.", "",
              "| Policy | Alerts | Alerts/device/week | Duplicate reduction | Mean time to first alert (min) | Missed incidents (of total) | Weekly alerts, 135 devices |",
              "|---|---|---|---|---|---|---|"]
    for name, r in p.items():
        lines.append(f"| {name} | {r['alerts']} | {r['alerts_per_device_per_week']} | {r['duplicate_alert_reduction_vs_raw']} | "
                     f"{r['mean_time_to_first_alert_min']} | {r['missed_incidents']} / {r['incidents']} | {r['expected_weekly_alerts_135_devices']} |")
    path = REPORT
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    logging.disable(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", required=True)
    args = ap.parse_args(argv)
    res = run(datetime.fromisoformat(args.cutoff).astimezone(timezone.utc))
    (OUT_LOCAL / "connectivity_summary.json").write_text(json.dumps(res, indent=2, default=str))
    print(write_report(res))
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
