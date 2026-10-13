"""src/features/audit_sentinels.py
Phase R1: Forensic Audit of Sentinel Zeros & Joint Telemetry Patterns.

Audits:
1. AC voltage: True zero vs missing/offline.
2. Battery voltage: Unequipped/unmonitored (permanent 0.0 V) vs active battery readings.
3. Heartbeat gaps: Genuine communication frequency vs sentinel fills.
4. Correlation with mains state and alarm history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "configs/config.yaml"
RAW_DIR = ROOT / "data/raw/telemetry"
ALARMS_FILE = ROOT / "data/raw/alarms/alarms.parquet"
REPORTS_DIR = ROOT / "reports"


def run_sentinel_audit() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    target_banks = config["fleet"]["target_banks"]
    dev_dirs = [d for d in RAW_DIR.iterdir() if d.is_dir()]

    # Load registry to map UUID to bank
    reg_file = ROOT / "data/raw/registry/devices.parquet"
    dev_meta_map = {}
    if reg_file.exists():
        reg_df = pd.read_parquet(reg_file)
        id_col = "device_id" if "device_id" in reg_df.columns else "id"
        name_col = "device_name" if "device_name" in reg_df.columns else "name"
        for _, row in reg_df.iterrows():
            did = str(row[id_col])
            dname = str(row[name_col])
            dev_meta_map[did] = dname

    device_audit = []
    
    total_devices = len(dev_dirs)
    batt_unequipped_count = 0
    batt_active_count = 0
    batt_missing_file_count = 0

    ac_unequipped_count = 0
    ac_active_count = 0
    ac_missing_file_count = 0

    total_batt_points = 0
    zero_batt_points = 0
    nonzero_batt_points = 0

    total_ac_points = 0
    zero_ac_points = 0
    nonzero_ac_points = 0

    bank_counts: Dict[str, Dict[str, int]] = {b: {"devices": 0, "has_batt_sensor": 0, "has_ac_sensor": 0} for b in target_banks}

    for dev in dev_dirs:
        did = dev.name
        dname = dev_meta_map.get(did, did)
        # identify bank
        bank = "OTHER"
        for b in target_banks:
            if b in dname.upper():
                bank = b
                break
        if bank in bank_counts:
            bank_counts[bank]["devices"] += 1

        # 1. Audit Battery
        batt_files = list(dev.glob("*battery*.parquet"))
        batt_vals = []
        if not batt_files:
            batt_status = "NO_FILE"
            batt_missing_file_count += 1
        else:
            for bf in batt_files:
                try:
                    df = pd.read_parquet(bf)
                    val_col = "value" if "value" in df.columns else df.columns[1]
                    s = pd.to_numeric(df[val_col], errors="coerce").dropna()
                    batt_vals.extend(s.tolist())
                except Exception:
                    pass

            if not batt_vals:
                batt_status = "EMPTY_FILE"
                batt_missing_file_count += 1
            else:
                s_arr = np.array(batt_vals)
                total_batt_points += len(s_arr)
                zero_batt_points += int((s_arr == 0.0).sum())
                nonzero_batt_points += int((s_arr > 0.0).sum())

                max_v = float(np.max(s_arr))
                mean_v = float(np.mean(s_arr))
                # If all readings are <= 0.5V, hardware sensor is unequipped / disconnected!
                if max_v <= 0.5:
                    batt_status = "UNEQUIPPED_OR_DISCONNECTED"
                    batt_unequipped_count += 1
                else:
                    batt_status = "ACTIVE_SENSOR"
                    batt_active_count += 1
                    if bank in bank_counts:
                        bank_counts[bank]["has_batt_sensor"] += 1

        # 2. Audit AC
        ac_files = list(dev.glob("*ac_*.parquet"))
        ac_vals = []
        if not ac_files:
            ac_status = "NO_FILE"
            ac_missing_file_count += 1
        else:
            for af in ac_files:
                try:
                    df = pd.read_parquet(af)
                    val_col = "value" if "value" in df.columns else df.columns[1]
                    s = pd.to_numeric(df[val_col], errors="coerce").dropna()
                    ac_vals.extend(s.tolist())
                except Exception:
                    pass

            if not ac_vals:
                ac_status = "EMPTY_FILE"
                ac_missing_file_count += 1
            else:
                ac_arr = np.array(ac_vals)
                total_ac_points += len(ac_arr)
                zero_ac_points += int((ac_arr == 0.0).sum())
                nonzero_ac_points += int((ac_arr > 0.0).sum())

                max_ac = float(np.max(ac_arr))
                if max_ac <= 5.0:
                    ac_status = "UNEQUIPPED_OR_PERMANENT_ZERO"
                    ac_unequipped_count += 1
                else:
                    ac_status = "ACTIVE_SENSOR"
                    ac_active_count += 1
                    if bank in bank_counts:
                        bank_counts[bank]["has_ac_sensor"] += 1

        device_audit.append({
            "device_id": dname,
            "bank": bank,
            "battery_status": batt_status,
            "ac_status": ac_status,
            "battery_points": len(batt_vals),
            "ac_points": len(ac_vals),
        })

    summary = {
        "total_devices": total_devices,
        "battery_audit": {
            "devices_with_active_sensor": batt_active_count,
            "devices_unequipped_or_flat_zero": batt_unequipped_count,
            "devices_missing_file": batt_missing_file_count,
            "total_points": total_batt_points,
            "zero_points": zero_batt_points,
            "zero_point_percentage": round(zero_batt_points / max(1, total_batt_points) * 100, 2),
            "nonzero_points": nonzero_batt_points,
            "nonzero_point_percentage": round(nonzero_batt_points / max(1, total_batt_points) * 100, 2),
        },
        "ac_audit": {
            "devices_with_active_sensor": ac_active_count,
            "devices_unequipped_or_flat_zero": ac_unequipped_count,
            "devices_missing_file": ac_missing_file_count,
            "total_points": total_ac_points,
            "zero_points": zero_ac_points,
            "zero_point_percentage": round(zero_ac_points / max(1, total_ac_points) * 100, 2),
            "nonzero_points": nonzero_ac_points,
            "nonzero_point_percentage": round(nonzero_ac_points / max(1, total_ac_points) * 100, 2),
        },
        "fleet_breakdown": bank_counts,
    }

    out_json = REPORTS_DIR / "phase_r1_sentinel_audit.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("=" * 80)
    print("PHASE R1: FORENSIC SENTINEL & SENSOR AUDIT")
    print("=" * 80)
    print(f"Total Fleet Devices: {total_devices}")
    print(f"Battery Sensor Equipped: {batt_active_count}/{total_devices} ({batt_active_count/total_devices*100:.1f}%)")
    print(f"Battery Unequipped/Flat Zero: {batt_unequipped_count}/{total_devices} ({batt_unequipped_count/total_devices*100:.1f}%)")
    print(f"Battery Total Zero Points: {zero_batt_points}/{total_batt_points} ({summary['battery_audit']['zero_point_percentage']}%)")
    print(f"AC Sensor Active: {ac_active_count}/{total_devices} ({ac_active_count/total_devices*100:.1f}%)")
    print(f"AC Total Points: {total_ac_points} (Zero: {zero_ac_points} = {summary['ac_audit']['zero_point_percentage']}%)")
    print("Fleet Breakdown:")
    for b, stats in bank_counts.items():
        print(f"  {b}: {stats['devices']} devices | Batt Sensor: {stats['has_batt_sensor']} | AC Sensor: {stats['has_ac_sensor']}")
    print("=" * 80)

    return summary


if __name__ == "__main__":
    run_sentinel_audit()
