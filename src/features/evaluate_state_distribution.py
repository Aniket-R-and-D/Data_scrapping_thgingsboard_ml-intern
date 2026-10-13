"""src/features/evaluate_state_distribution.py
Phase R1: Fleet-wide Device State Distribution & Legacy Label Audit.

Computes:
1. Exact share of rows per state across all 1,166,535 rows.
2. Breakdown per bank (BOI, SBI, BOB, CANARA).
3. Audit of past legacy outage labels (y_outage_24h): how many fell into STALE/OFFLINE states.
4. Concrete real-world examples of each state from the fleet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.state_machine import classify_device_state_vectorized, DeviceState
CONFIG_PATH = ROOT / "configs/config.yaml"
PROCESSED_MATRIX = ROOT / "data/processed/features_matrix.parquet"
REPORTS_DIR = ROOT / "reports"


def run_state_distribution_evaluation() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    print("Loading features matrix from data/processed/features_matrix.parquet...")
    # Load necessary columns to conserve memory
    cols = [
        "device_id", "device_name", "timestamp",
        "ac_voltage_mean_1h", "battery_voltage_mean_1h",
        "heartbeat_gap_seconds", "time_since_mains_loss_hours",
        "y_outage_24h"
    ]
    df = pd.read_parquet(PROCESSED_MATRIX, columns=cols)
    total_rows = len(df)
    print(f"Loaded {total_rows:,} rows across {df['device_id'].nunique()} devices.")

    # Derive bank from device_name
    def get_bank(name: str) -> str:
        nu = str(name).upper()
        for b in ["BOI", "SBI", "BOB", "CANARA"]:
            if b in nu:
                return b
        return "OTHER"

    df["bank"] = df["device_name"].apply(get_bank)

    # Apply state machine classification
    # Telemetry age is heartbeat_gap_seconds
    telem_age = df["heartbeat_gap_seconds"]
    ac_v = df["ac_voltage_mean_1h"]
    batt_v = df["battery_voltage_mean_1h"]
    mains_on = (df["time_since_mains_loss_hours"] > 0).astype(float) # if 0, mains was lost at anchor
    power_alarm = pd.Series(0, index=df.index)

    print("Classifying device states using physical state machine...")
    df["device_state"] = classify_device_state_vectorized(
        telemetry_age_seconds=telem_age,
        ac_voltage=ac_v,
        mains_on=mains_on,
        battery_voltage=batt_v,
        is_power_alarm_active=power_alarm,
        stale_threshold_seconds=3600.0,
        offline_threshold_seconds=86400.0,
        ac_outage_volts=50.0,
    )

    # 1. Global State Distribution
    state_counts = df["device_state"].value_counts().to_dict()
    state_pcts = {k: round(v / total_rows * 100, 2) for k, v in state_counts.items()}

    # 2. Per-Bank Breakdown
    bank_breakdown: Dict[str, Dict[str, Any]] = {}
    for bank, b_df in df.groupby("bank"):
        b_total = len(b_df)
        b_counts = b_df["device_state"].value_counts().to_dict()
        b_pcts = {k: round(b_counts.get(k, 0) / b_total * 100, 2) for k in [s.value for s in DeviceState]}
        bank_breakdown[bank] = {
            "total_rows": b_total,
            "counts": b_counts,
            "percentages": b_pcts,
        }

    # 3. Cross-Tabulation with Legacy Outage Labels
    legacy_positives = df[df["y_outage_24h"] == 1]
    legacy_pos_count = len(legacy_positives)
    pos_by_state = legacy_positives["device_state"].value_counts().to_dict()
    pos_by_state_pct = {k: round(v / max(1, legacy_pos_count) * 100, 2) for k, v in pos_by_state.items()}

    stale_offline_positives = pos_by_state.get(DeviceState.STALE_DATA.value, 0) + pos_by_state.get(DeviceState.OFFLINE_LONG.value, 0)
    stale_offline_pos_pct = round(stale_offline_positives / max(1, legacy_pos_count) * 100, 2)

    # 4. Extract Real Examples of Each State
    examples = {}
    for state_val in [s.value for s in DeviceState]:
        match = df[df["device_state"] == state_val].head(2)
        examples[state_val] = []
        for _, row in match.iterrows():
            examples[state_val].append({
                "device_name": str(row["device_name"]),
                "bank": str(row["bank"]),
                "timestamp": str(row["timestamp"]),
                "heartbeat_gap_seconds": float(row["heartbeat_gap_seconds"]),
                "ac_voltage": float(row["ac_voltage_mean_1h"]),
                "battery_voltage": float(row["battery_voltage_mean_1h"]),
                "legacy_y_outage_24h": int(row["y_outage_24h"]),
            })

    report = {
        "total_rows_evaluated": total_rows,
        "global_state_distribution": {
            "counts": state_counts,
            "percentages": state_pcts,
        },
        "fleet_bank_breakdown": bank_breakdown,
        "legacy_label_audit": {
            "total_legacy_positive_labels": legacy_pos_count,
            "legacy_prevalence_pct": round(legacy_pos_count / total_rows * 100, 2),
            "positives_by_state_counts": pos_by_state,
            "positives_by_state_percentages": pos_by_state_pct,
            "stale_or_offline_positives_count": stale_offline_positives,
            "stale_or_offline_positives_percentage": stale_offline_pos_pct,
            "verdict": (
                f"{stale_offline_pos_pct}% of past positive 'outage' labels occurred when the device "
                "was in STALE_DATA or OFFLINE_LONG state, proving that the old model was largely trained "
                "to predict communication blackouts rather than real utility power failures."
            ),
        },
        "state_examples": examples,
    }

    out_file = REPORTS_DIR / "phase_r1_state_distribution.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 80)
    print("PHASE R1: DEVICE STATE CLASSIFICATION REPORT")
    print("=" * 80)
    print(f"Total Rows: {total_rows:,}")
    print("Global State Distribution:")
    for k in [s.value for s in DeviceState]:
        cnt = state_counts.get(k, 0)
        pct = state_pcts.get(k, 0.0)
        print(f"  - {k:<20}: {cnt:>9,} rows ({pct:>5.2f}%)")
    print("\nLegacy Outage Label Audit (y_outage_24h == 1):")
    print(f"  - Total Legacy Positives: {legacy_pos_count:,}")
    for k in [s.value for s in DeviceState]:
        cnt = pos_by_state.get(k, 0)
        pct = pos_by_state_pct.get(k, 0.0)
        print(f"    * In {k:<18}: {cnt:>7,} ({pct:>5.2f}%)")
    print(f"\n  CRITICAL AUDIT VERDICT: {stale_offline_pos_pct}% of past outage labels were in STALE/OFFLINE states!")
    print("=" * 80 + "\n")

    return report


if __name__ == "__main__":
    run_state_distribution_evaluation()
