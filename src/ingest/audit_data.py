#!/usr/bin/env python
"""Phase 1: Comprehensive Data Audit & Outage Label Formulation.

Computes:
1. Device inventory & per-branch breakdown (BOI, SBI, BOB, Canara).
2. Date range of telemetry.
3. Sampling interval, % missing, gap distribution, clock-drift/duplicate timestamps per signal.
4. Physical unit distributions & outlier bounds per signal.
5. Outage label derivation from telemetry states & verified alarm history.
6. Number of independent outage events, per-device outage stats, 24h positive rate.
7. Gate evaluation (checks if independent events >= 30).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("src.ingest.audit_data")


def load_config(config_path: str = "configs/config.yaml") -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def audit_devices(registry_file: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    df = pd.read_parquet(registry_file)
    target_banks = config["fleet"]["target_banks"]

    bank_counts: Dict[str, int] = {}
    for b in target_banks:
        mask = df["name"].astype(str).str.upper().str.startswith(b)
        bank_counts[b] = int(mask.sum())

    total_devices = len(df)
    eligible_devices = int(df["is_target_bank"].sum())
    excluded_devices = total_devices - eligible_devices

    return {
        "total_devices": total_devices,
        "eligible_devices": eligible_devices,
        "excluded_devices": excluded_devices,
        "per_bank_counts": bank_counts,
    }


def audit_telemetry(raw_telemetry_dir: Path, eligible_devices_df: pd.DataFrame, config: Dict[str, Any]) -> Dict[str, Any]:
    device_dirs = [d for d in raw_telemetry_dir.iterdir() if d.is_dir()]
    device_ids = set(eligible_devices_df["device_id"].astype(str).tolist())
    target_dirs = [d for d in device_dirs if d.name in device_ids]

    logger.info(f"Auditing telemetry for {len(target_dirs)} devices in {raw_telemetry_dir}...")

    # Key signal mapping from config
    signals_map = {
        "ac_voltage": config["signals"]["ac_voltage_keys"],
        "battery_voltage": config["signals"]["battery_voltage_keys"],
        "mains_status": config["signals"]["mains_keys"],
        "nvr_status": config["signals"]["nvr_keys"],
        "fas_status": config["signals"]["fas_keys"],
        "storage_health": config["signals"]["storage_keys"],
        "heartbeat": config["signals"]["heartbeat_keys"],
    }

    signal_stats: Dict[str, Any] = {}
    global_min_ts = float("inf")
    global_max_ts = float("-inf")
    total_raw_rows = 0

    for sig_name, keys in signals_map.items():
        all_vals: List[float] = []
        all_intervals_sec: List[float] = []
        all_gaps_hours: List[float] = []
        dup_ts_count = 0
        clock_drift_count = 0
        devices_with_signal = 0

        for ddir in target_dirs:
            # Look for matching parquet files for this signal's keys
            matched_dfs = []
            for k in keys:
                pfile = ddir / f"{k}.parquet"
                if pfile.exists():
                    try:
                        part = pd.read_parquet(pfile)
                        if not part.empty:
                            matched_dfs.append(part)
                    except Exception:
                        pass

            if not matched_dfs:
                continue

            devices_with_signal += 1
            df_sig = pd.concat(matched_dfs, ignore_index=True)
            ts_col = "ts" if "ts" in df_sig.columns else "timestamp"
            val_col = "value" if "value" in df_sig.columns else df_sig.columns[1]

            # Convert types safely
            df_sig = df_sig.sort_values(ts_col)
            ts_series = pd.to_numeric(df_sig[ts_col], errors="coerce").dropna()
            val_series = pd.to_numeric(df_sig[val_col], errors="coerce").dropna()

            if len(ts_series) == 0:
                continue

            min_ts = ts_series.min()
            max_ts = ts_series.max()
            if min_ts < global_min_ts and min_ts > 1_500_000_000_000:
                global_min_ts = min_ts
            if max_ts > global_max_ts:
                global_max_ts = max_ts

            total_raw_rows += len(df_sig)
            all_vals.extend(val_series.tolist()[:10000])  # sample if huge

            # Timestamp intervals and duplicates
            diffs_ms = ts_series.diff().dropna()
            dup_ts_count += int((diffs_ms == 0).sum())
            clock_drift_count += int((diffs_ms < 0).sum())

            # Intervals > 0 in seconds
            pos_diffs_sec = (diffs_ms[diffs_ms > 0] / 1000.0).tolist()
            all_intervals_sec.extend(pos_diffs_sec[:5000])

            # Gaps (> 15 minutes = 900 seconds)
            gaps = [d / 3600.0 for d in pos_diffs_sec if d >= 900]
            all_gaps_hours.extend(gaps)

        # Signal level aggregation
        if all_vals:
            arr_vals = np.array(all_vals)
            v_min, v_max = float(np.min(arr_vals)), float(np.max(arr_vals))
            v_mean, v_std = float(np.mean(arr_vals)), float(np.std(arr_vals))
            v_p25, v_p50, v_p75 = float(np.percentile(arr_vals, 25)), float(np.percentile(arr_vals, 50)), float(np.percentile(arr_vals, 75))
        else:
            v_min = v_max = v_mean = v_std = v_p25 = v_p50 = v_p75 = 0.0

        if all_intervals_sec:
            arr_int = np.array(all_intervals_sec)
            median_interval_min = float(np.median(arr_int) / 60.0)
            p90_interval_min = float(np.percentile(arr_int, 90) / 60.0)
        else:
            median_interval_min = p90_interval_min = 0.0

        if all_gaps_hours:
            arr_gaps = np.array(all_gaps_hours)
            med_gap_h = float(np.median(arr_gaps))
            p95_gap_h = float(np.percentile(arr_gaps, 95))
            max_gap_h = float(np.max(arr_gaps))
            total_gaps = len(arr_gaps)
        else:
            med_gap_h = p95_gap_h = max_gap_h = 0.0
            total_gaps = 0

        # Outlier counts
        outliers = 0
        if sig_name == "ac_voltage":
            outliers = int(np.sum((arr_vals < 0) | (arr_vals > 320))) if all_vals else 0
        elif sig_name == "battery_voltage":
            outliers = int(np.sum((arr_vals < 0) | (arr_vals > 25))) if all_vals else 0

        missing_device_pct = 100.0 * (1.0 - (devices_with_signal / max(1, len(target_dirs))))

        signal_stats[sig_name] = {
            "devices_active": devices_with_signal,
            "device_missing_pct": round(missing_device_pct, 1),
            "median_interval_minutes": round(median_interval_min, 2),
            "p90_interval_minutes": round(p90_interval_min, 2),
            "total_gaps_gt_15m": total_gaps,
            "median_gap_hours": round(med_gap_h, 2),
            "p95_gap_hours": round(p95_gap_h, 2),
            "max_gap_hours": round(max_gap_h, 2),
            "duplicate_timestamps": dup_ts_count,
            "clock_drift_events": clock_drift_count,
            "val_min": round(v_min, 2),
            "val_p25": round(v_p25, 2),
            "val_median": round(v_p50, 2),
            "val_p75": round(v_p75, 2),
            "val_max": round(v_max, 2),
            "val_mean": round(v_mean, 2),
            "val_std": round(v_std, 2),
            "outlier_count": outliers,
        }

    date_range_start = datetime.fromtimestamp(global_min_ts / 1000.0, tz=timezone.utc).isoformat() if global_min_ts != float("inf") else "N/A"
    date_range_end = datetime.fromtimestamp(global_max_ts / 1000.0, tz=timezone.utc).isoformat() if global_max_ts != float("-inf") else "N/A"
    duration_days = round((global_max_ts - global_min_ts) / (86400 * 1000.0), 1) if global_min_ts != float("inf") else 0

    return {
        "date_range_start": date_range_start,
        "date_range_end": date_range_end,
        "duration_days": duration_days,
        "total_raw_rows": total_raw_rows,
        "signals": signal_stats,
    }


def audit_outages_and_labels(
    raw_alarms_file: Path,
    raw_telemetry_dir: Path,
    eligible_devices_df: pd.DataFrame,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Define and evaluate outage events from alarms and telemetry."""
    label_cfg = config["outage_label"]
    batt_crit = label_cfg["battery_critical_volts"]
    sustained_min = label_cfg["sustained_minutes"]
    pred_window_h = label_cfg["prediction_window_hours"]

    # 1. Alarms-based evidence
    alarms_df = pd.read_parquet(raw_alarms_file)
    power_nvr_alarms = alarms_df[
        alarms_df["alarm_type"].astype(str).str.upper().str.contains("POWER OFF|BATTERY LOW|DVR/NVR OFF", regex=True)
    ].copy()

    # Normalize alarm timestamps (supports ISO strings and epoch ms)
    start_col = "start" if "start" in power_nvr_alarms.columns else "start_ts"
    clear_col = "clear" if "clear" in power_nvr_alarms.columns else "clear_ts"
    s_dt = pd.to_datetime(power_nvr_alarms[start_col], format="ISO8601", utc=True, errors="coerce")
    c_dt = pd.to_datetime(power_nvr_alarms[clear_col], format="ISO8601", utc=True, errors="coerce")
    c_dt = c_dt.fillna(s_dt + pd.Timedelta(minutes=sustained_min))

    # Convert to ms and cap alarm duration at 24 hours to prevent stale/un-cleared alarms spanning months
    power_nvr_alarms["start_ms"] = s_dt.astype("int64") // 1000
    power_nvr_alarms["clear_ms"] = np.minimum(
        c_dt.astype("int64") // 1000,
        power_nvr_alarms["start_ms"] + 24 * 3600 * 1000
    )
    power_nvr_alarms["duration_min"] = (power_nvr_alarms["clear_ms"] - power_nvr_alarms["start_ms"]) / (60.0 * 1000.0)

    # Filter sustained outages >= sustained_min
    sustained_alarms = power_nvr_alarms[power_nvr_alarms["duration_min"] >= sustained_min].copy()

    # 2. Extract telemetry-based outage episodes per device
    target_dids = eligible_devices_df["device_id"].astype(str).tolist()
    all_outage_events: List[Dict[str, Any]] = []

    # Map alarms by device
    alarm_events_by_device: Dict[str, List[Tuple[float, float, str]]] = {}
    for _, row in sustained_alarms.iterrows():
        did = str(row.get("device_id", ""))
        s = float(row["start_ms"])
        c = float(row["clear_ms"])
        atype = str(row.get("alarm_type", "OUTAGE"))
        if not np.isnan(s) and not np.isnan(c) and c >= s:
            alarm_events_by_device.setdefault(did, []).append((s, c, atype))

    device_event_counts: Dict[str, int] = {}
    total_grid_points = 0
    positive_grid_points = 0

    for did in target_dids:
        dev_dir = raw_telemetry_dir / did
        events: List[Tuple[float, float, str]] = list(alarm_events_by_device.get(did, []))

        # Check telemetry for mains lost + battery critical condition
        ac_file = dev_dir / "ac_voltage.parquet"
        batt_file = dev_dir / "battery_voltage.parquet"

        if ac_file.exists() and batt_file.exists():
            try:
                ac_df = pd.read_parquet(ac_file)
                batt_df = pd.read_parquet(batt_file)
                ac_ts_col = "ts" if "ts" in ac_df.columns else "timestamp"
                batt_ts_col = "ts" if "ts" in batt_df.columns else "timestamp"

                ac_df["val"] = pd.to_numeric(ac_df["value"], errors="coerce")
                batt_df["val"] = pd.to_numeric(batt_df["value"], errors="coerce")
                ac_df["time"] = pd.to_datetime(ac_df[ac_ts_col], unit="ms", utc=True)
                batt_df["time"] = pd.to_datetime(batt_df[batt_ts_col], unit="ms", utc=True)

                ac_15m = ac_df.set_index("time")["val"].resample("15min").mean()
                batt_15m = batt_df.set_index("time")["val"].resample("15min").mean()

                joined = pd.concat([ac_15m, batt_15m], axis=1, keys=["ac", "batt"]).dropna()
                if not joined.empty:
                    outage_mask = (joined["ac"] < 50.0) & (joined["batt"] < batt_crit)
                    for t_idx, is_out in outage_mask.items():
                        if is_out:
                            t_ms = t_idx.timestamp() * 1000.0
                            events.append((t_ms, t_ms + 15 * 60 * 1000, "TELEMETRY_MAINS_BATT_OUTAGE"))
            except Exception:
                pass

        if not events:
            continue

        # Sort and collapse overlapping or nearby events (< 2 hours = 7200s gap) into independent outage episodes
        events.sort(key=lambda x: x[0])
        collapsed: List[Dict[str, Any]] = []
        cur_start, cur_end, cur_reason = events[0]

        for s, e, r in events[1:]:
            if s <= cur_end + 7200 * 1000:  # merge if within 2 hours
                cur_end = max(cur_end, e)
                if r not in cur_reason:
                    cur_reason += f" + {r}"
            else:
                collapsed.append({
                    "device_id": did,
                    "start_ts": cur_start,
                    "end_ts": cur_end,
                    "duration_hours": round((cur_end - cur_start) / (3600.0 * 1000.0), 2),
                    "reason": cur_reason,
                })
                cur_start, cur_end, cur_reason = s, e, r

        collapsed.append({
            "device_id": did,
            "start_ts": cur_start,
            "end_ts": cur_end,
            "duration_hours": round((cur_end - cur_start) / (3600.0 * 1000.0), 2),
            "reason": cur_reason,
        })

        device_event_counts[did] = len(collapsed)
        all_outage_events.extend(collapsed)

        # Compute positive rate on 90-day 15-min grid:
        # A 15-min step t is positive if an outage starts in (t, t + 24h]
        n_steps = 90 * 24 * 4  # 8640 steps
        total_grid_points += n_steps
        pos_steps_for_dev = min(n_steps, len(collapsed) * 96)
        positive_grid_points += pos_steps_for_dev

    n_events = len(all_outage_events)
    events_per_device_list = list(device_event_counts.values()) or [0]
    mean_events = round(float(np.mean(events_per_device_list)), 2)
    max_events = int(np.max(events_per_device_list)) if events_per_device_list else 0
    devices_with_outage = len(device_event_counts)

    overall_positive_rate = round(100.0 * (positive_grid_points / max(1, total_grid_points)), 2)

    # Gate determination
    meets_ml_threshold = n_events >= 30
    recommendation = (
        "Supervised ML (LightGBM) + Rules + Anomaly Detection viable"
        if meets_ml_threshold
        else "Fewer than 30 independent outage events. RECOMMEND RULES + ANOMALY DETECTION ONLY."
    )

    # Persist all outage events for feature generation
    proc_dir = Path(config["paths"]["data_processed"])
    proc_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_outage_events).to_parquet(proc_dir / "outage_events.parquet", index=False)
    logger.info(f"Persisted {len(all_outage_events)} independent outage events to {proc_dir / 'outage_events.parquet'}")

    return {
        "outage_label_formula": label_cfg["definition"],
        "sustained_minutes": sustained_min,
        "prediction_window_hours": pred_window_h,
        "total_independent_outage_events": n_events,
        "devices_with_outage_events": devices_with_outage,
        "mean_events_per_device": mean_events,
        "max_events_per_device": max_events,
        "estimated_positive_rate_24h_pct": overall_positive_rate,
        "meets_ml_threshold_gte_30": meets_ml_threshold,
        "recommendation": recommendation,
        "sample_events": all_outage_events[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Telemetry and Outage Labels")
    parser.add_argument("--config", default="configs/config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    raw_telemetry = Path(config["paths"]["data_raw_telemetry"])
    raw_alarms = Path(config["paths"]["data_raw_alarms"]) / "alarms.parquet"
    raw_registry = Path(config["paths"]["data_raw_registry"]) / "devices.parquet"
    reports_dir = Path(config["paths"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Running Step 1: Device Audit...")
    device_audit = audit_devices(raw_registry, config)
    eligible_df = pd.read_parquet(raw_registry)
    eligible_df = eligible_df[eligible_df["is_target_bank"]].copy()

    logger.info("Running Step 2: Telemetry Audit...")
    telemetry_audit = audit_telemetry(raw_telemetry, eligible_df, config)

    logger.info("Running Step 3: Outage Label Audit...")
    outage_audit = audit_outages_and_labels(raw_alarms, raw_telemetry, eligible_df, config)

    audit_pack = {
        "device_audit": device_audit,
        "telemetry_audit": telemetry_audit,
        "outage_audit": outage_audit,
    }

    out_json = reports_dir / "phase_1_audit_tables.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(audit_pack, f, indent=2)

    logger.info(f"Audit complete! Results written to {out_json}")


if __name__ == "__main__":
    main()
