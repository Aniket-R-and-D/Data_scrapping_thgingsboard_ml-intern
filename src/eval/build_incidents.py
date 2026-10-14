"""src/eval/build_incidents.py — Stage D incident grouping for clean evaluation sets.

Groups 15-minute shadow/telemetry rows into discrete incidents so that adjacent
snapshots of the same event never cross a split boundary.

Grouping rules (deterministic, per device, ordered by UTC timestamp):
- Each row is classified into one of three episode regimes:
    POWER_INCIDENT      — device_state == 'ONLINE_POWER_LOSS', or risk_tier in
                          ('HIGH', 'CRITICAL'), or 'RULE_AC_UNDER_50V' /
                          'RULE_AC_SURGE_OVER_250V' triggered while online.
    CONNECTIVITY_EPISODE — device_state in ('STALE_DATA', 'OFFLINE_LONG') or
                          risk_tier == 'CONNECTIVITY_ISSUE'.
    NORMAL_SEGMENT      — all other online rows (ONLINE_OK, LOW/MEDIUM without
                          acute voltage collapse).
- Consecutive rows on the same device in the same regime are merged into one
  incident as long as the gap between consecutive rows is <= 6 hours (for
  POWER_INCIDENT / CONNECTIVITY_EPISODE) and the segment duration does not
  exceed 24 hours (for NORMAL_SEGMENT, which is chunked into 24-hour windows so
  a 12-day normal run does not become a single giant incident straddling the
  temporal cutoff). Any regime change or gap > 6 hours starts a new incident.
- Every row receives a deterministic incident_id:
  'INC-<device_id[:8]>-<regime_short>-<4-digit-seq>'.

This guarantees: (1) adjacent snapshots of any outage or connectivity blackout
stay in the same incident_id; (2) an incident never spans two splits once
splits assign whole incident_ids.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SHADOW_LOG = ROOT / "data" / "processed" / "remediated_shadow_predictions.jsonl"
FEATURES_PARQUET = ROOT / "data" / "processed" / "remediated_features_matrix.parquet"
OUT_DIR = ROOT / "data" / "eval"
INCIDENTS_PARQUET = OUT_DIR / "incidents_table.parquet"
ROWS_WITH_INCIDENTS_PARQUET = OUT_DIR / "rows_with_incidents.parquet"

MERGE_GAP = pd.Timedelta(hours=6)
NORMAL_MAX_SPAN = pd.Timedelta(hours=24)


def _classify_regime(row: pd.Series) -> str:
    state = row["device_state"]
    tier = row["risk_tier"]
    rules = row["rules_triggered"] if isinstance(row["rules_triggered"], list) else []
    if state in ("STALE_DATA", "OFFLINE_LONG") or tier == "CONNECTIVITY_ISSUE":
        return "CONNECTIVITY"
    if (
        state == "ONLINE_POWER_LOSS"
        or tier in ("HIGH", "CRITICAL")
        or "RULE_AC_UNDER_50V" in rules
        or "RULE_AC_SURGE_OVER_250V" in rules
    ):
        return "POWER"
    return "NORMAL"


def build_incidents() -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    preds: List[Dict[str, Any]] = []
    with open(SHADOW_LOG, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                preds.append(json.loads(line))
    dfp = pd.DataFrame(preds)
    dfp["ts_str"] = dfp["timestamp"].astype(str)

    feat_cols = [
        "device_id", "timestamp", "is_battery_sensor_present", "is_ac_sensor_present",
        "ac_voltage_mean_1h", "battery_voltage_mean_1h", "time_since_mains_loss_hours",
    ]
    feats = pd.read_parquet(FEATURES_PARQUET, columns=feat_cols)
    feats["ts_str"] = feats["timestamp"].astype(str)

    df = dfp.merge(
        feats.drop(columns=["timestamp"]),
        on=["device_id", "ts_str"],
        how="inner",
    )
    df["dt"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["device_id", "dt"]).reset_index(drop=True)
    df["regime"] = [_classify_regime(r) for _, r in df.iterrows()]

    incident_ids: List[str] = []
    incidents_meta: List[Dict[str, Any]] = []

    for dev_id, grp in df.groupby("device_id", sort=False):
        seq = 0
        cur_regime = None
        cur_start = None
        cur_last = None
        cur_id = None
        cur_states: List[str] = []
        cur_tiers: List[str] = []
        cur_count = 0
        dev_name = grp["device_name"].iloc[0]
        bank = grp["bank"].iloc[0]

        def _flush():
            if cur_id is None:
                return
            incidents_meta.append({
                "incident_id": cur_id,
                "device_id": dev_id,
                "device_name": dev_name,
                "bank": bank,
                "regime": cur_regime,
                "incident_start_utc": cur_start.isoformat(),
                "incident_end_utc": cur_last.isoformat(),
                "snapshot_count": cur_count,
                "device_states_seen": ",".join(sorted(set(cur_states))),
                "risk_tiers_seen": ",".join(sorted(set(cur_tiers))),
            })

        for _, row in grp.iterrows():
            dt = row["dt"]
            reg = row["regime"]
            new_ep = False
            if cur_id is None or reg != cur_regime:
                new_ep = True
            elif (dt - cur_last) > MERGE_GAP:
                new_ep = True
            elif reg == "NORMAL" and (dt - cur_start) >= NORMAL_MAX_SPAN:
                new_ep = True

            if new_ep:
                _flush()
                seq += 1
                cur_regime = reg
                cur_start = dt
                cur_last = dt
                cur_id = f"INC-{dev_id[:8]}-{reg[:4]}-{seq:04d}"
                cur_states = [str(row["device_state"])]
                cur_tiers = [str(row["risk_tier"])]
                cur_count = 1
            else:
                cur_last = dt
                cur_states.append(str(row["device_state"]))
                cur_tiers.append(str(row["risk_tier"]))
                cur_count += 1
            incident_ids.append(cur_id)
        _flush()

    df["incident_id"] = incident_ids
    df["rules_triggered_json"] = [json.dumps(sorted(r)) for r in df["rules_triggered"]]
    df_out = df.drop(columns=["rules_triggered", "dt"])
    df_out.to_parquet(ROWS_WITH_INCIDENTS_PARQUET, index=False)

    inc_df = pd.DataFrame(incidents_meta)
    inc_df.to_parquet(INCIDENTS_PARQUET, index=False)

    summary = {
        "total_rows": len(df),
        "total_devices": int(df["device_id"].nunique()),
        "total_incidents": len(inc_df),
        "incidents_by_regime": inc_df["regime"].value_counts().to_dict(),
    }
    print("Built incidents:", json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    build_incidents()
    sys.exit(0)
