"""src/rules/eval_rules.py
Deterministic rule evaluation engine for ThingsBoard bank branch telemetry.

Rules implemented:
1. AC Zero (Instantaneous AC == 0 V during active telemetry)
2. AC Surge (AC > 250 V or max_1h > 250 V)
3. Battery Critical (Battery voltage < 10.8 V)
4. Mains Disconnected (Mains lost / power off)
5. Heartbeat Gap (> 1800s inactivity)
6. Storage Fault (disk error flag >= 1)
7. Combined Rules (Union of operational rules)

Evaluates:
- Sample-level: Precision, Recall, F1, Alert Volume
- Event-level: 24h Event Detection Recall, Mean Lead Time, Median Lead Time
- Operational: False Alarms per Device per Week (both raw steps and clustered episodes)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.rules.eval_rules")


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def apply_rules(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Vectorized calculation of deterministic physical alarm rules."""
    rules_cfg = config.get("rules", {})
    ac_zero_v = float(rules_cfg.get("ac_zero_volts", 0.0))
    ac_surge_v = float(rules_cfg.get("ac_surge_volts", 250.0))
    batt_crit_v = float(rules_cfg.get("battery_critical_volts", 10.8))
    hb_timeout_s = float(rules_cfg.get("heartbeat_timeout_seconds", 1800.0))
    storage_thresh = float(rules_cfg.get("storage_error_threshold", 1.0))

    df_rules = pd.DataFrame(index=df.index)

    # 1. AC Zero: physical zero line voltage during active communication
    # (distinguishing from unmonitored devices via missing_indicator)
    df_rules["rule_ac_zero"] = (df["missing_indicator"] == 0) & (df["ac_voltage"] <= ac_zero_v)

    # 2. AC Surge: over-voltage dangerous to bank electronics
    df_rules["rule_ac_surge"] = (df["ac_voltage"] > ac_surge_v) | (df["ac_voltage_max_1h"] > ac_surge_v)

    # 3. Battery Critical: inverter battery collapsed below 10.8V
    # Only triggered when battery voltage is actively monitored (> 0 and < 10.8)
    df_rules["rule_battery_critical"] = (
        (df["missing_indicator"] == 0) & (df["battery_voltage"] > 0.0) & (df["battery_voltage"] < batt_crit_v)
    )

    # 4. Mains Disconnected: active grid outage
    df_rules["rule_mains_lost"] = df["time_since_mains_loss_hours"] == 0.0

    # 5. Heartbeat Gap: communication loss exceeding timeout
    df_rules["rule_heartbeat_gap"] = df["heartbeat_gap_seconds"] > hb_timeout_s

    # 6. Storage Fault: NVR/DVR HDD failure
    df_rules["rule_storage_fault"] = df["storage_fault_flag"] >= storage_thresh

    # 7. Combined Operational Rules (Any critical fault)
    df_rules["rule_combined"] = (
        df_rules["rule_ac_zero"]
        | df_rules["rule_ac_surge"]
        | df_rules["rule_battery_critical"]
        | df_rules["rule_mains_lost"]
        | df_rules["rule_heartbeat_gap"]
        | df_rules["rule_storage_fault"]
    )

    return df_rules


def evaluate_rule_metrics(
    df: pd.DataFrame,
    df_rules: pd.DataFrame,
    events_df: pd.DataFrame,
) -> Dict[str, Dict[str, Any]]:
    """Compute comprehensive sample-level, event-level, and operational metrics."""
    y = df["y_outage_24h"].values
    num_devices = df["device_id"].nunique()

    # Time span in weeks
    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    t_min = timestamps.min()
    t_max = timestamps.max()
    duration_weeks = max(1.0, (t_max - t_min).total_seconds() / (7.0 * 86400.0))

    # Pre-index device timeline for lead time evaluation
    df_with_meta = df[["device_id"]].copy()
    df_with_meta["dt"] = timestamps
    dev_timelines: Dict[str, pd.DataFrame] = {}
    for rule_name in df_rules.columns:
        df_with_meta[rule_name] = df_rules[rule_name].values

    for did, group in df_with_meta.groupby("device_id"):
        dev_timelines[str(did)] = group.set_index("dt").sort_index()

    # Filter events to those starting within evaluation range (+24h buffer for lead-up)
    events_clean = events_df.copy()
    events_clean["start_dt"] = pd.to_datetime(events_clean["start_ts"], unit="ms", utc=True)
    valid_events = events_clean[
        (events_clean["start_dt"] >= t_min + pd.Timedelta(hours=24))
        & (events_clean["start_dt"] <= t_max)
    ].copy()
    n_valid_events = len(valid_events)

    metrics_by_rule: Dict[str, Dict[str, Any]] = {}

    for rule_name in df_rules.columns:
        pred = df_rules[rule_name].values.astype(bool)

        # 1. Sample-level metrics
        tp = int(np.sum(pred & (y == 1)))
        fp = int(np.sum(pred & (y == 0)))
        fn = int(np.sum((~pred) & (y == 1)))
        tn = int(np.sum((~pred) & (y == 0)))

        prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = float(2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        fire_rate = float(np.mean(pred))

        # 2. Operational false alarms
        # False alarm steps per device per week
        fp_steps_per_dev_week = float(fp / (num_devices * duration_weeks))

        # False alarm episodes (clustered contiguous blocks)
        is_fp_series = pd.Series((pred & (y == 0)).astype(int), index=df.index)
        # Cluster within device
        dev_series = df["device_id"]
        fp_starts = (is_fp_series == 1) & (
            (is_fp_series.shift(1, fill_value=0) == 0) | (dev_series != dev_series.shift(1))
        )
        total_fp_episodes = int(fp_starts.sum())
        fp_episodes_per_dev_week = float(total_fp_episodes / (num_devices * duration_weeks))

        # 3. Incident-level lead time & recall
        detected_events = 0
        lead_times_hours: List[float] = []

        for _, ev in valid_events.iterrows():
            did = str(ev["device_id"])
            ev_start = ev["start_dt"]
            if did in dev_timelines:
                dev_tl = dev_timelines[did]
                # Look at 24h prior window: [ev_start - 24h, ev_start]
                win_start = ev_start - pd.Timedelta(hours=24)
                sub_win = dev_tl.loc[win_start:ev_start]
                rule_fires = sub_win[sub_win[rule_name] == True]
                if not rule_fires.empty:
                    detected_events += 1
                    earliest_alert_dt = rule_fires.index[0]
                    lead_h = float((ev_start - earliest_alert_dt).total_seconds() / 3600.0)
                    lead_times_hours.append(lead_h)

        event_recall = float(detected_events / n_valid_events) if n_valid_events > 0 else 0.0
        mean_lead_time_h = float(np.mean(lead_times_hours)) if lead_times_hours else 0.0
        median_lead_time_h = float(np.median(lead_times_hours)) if lead_times_hours else 0.0

        metrics_by_rule[rule_name] = {
            "sample_metrics": {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "fire_rate": round(fire_rate, 4),
                "total_fires": int(tp + fp),
            },
            "operational_metrics": {
                "fp_steps_per_dev_week": round(fp_steps_per_dev_week, 2),
                "total_fp_episodes": total_fp_episodes,
                "fp_episodes_per_dev_week": round(fp_episodes_per_dev_week, 2),
            },
            "incident_metrics": {
                "total_events_in_range": n_valid_events,
                "detected_events": detected_events,
                "event_recall": round(event_recall, 4),
                "mean_lead_time_hours": round(mean_lead_time_h, 2),
                "median_lead_time_hours": round(median_lead_time_h, 2),
            },
        }

    return metrics_by_rule


def run_rule_evaluation() -> Dict[str, Any]:
    """Execute complete deterministic rules pipeline."""
    config = load_config()
    features_path = Path(config["paths"]["data_processed"]) / "features_matrix.parquet"
    events_path = Path(config["paths"]["data_processed"]) / "outage_events.parquet"
    reports_dir = Path(config["paths"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading feature matrix from {features_path}...")
    df = pd.read_parquet(features_path)
    logger.info(f"Loaded {len(df)} rows across {df['device_id'].nunique()} devices.")

    logger.info(f"Loading labeled outage events from {events_path}...")
    events_df = pd.read_parquet(events_path)
    logger.info(f"Loaded {len(events_df)} independent events.")

    logger.info("Computing deterministic rules...")
    df_rules = apply_rules(df, config)

    logger.info("Evaluating rules against ground truth...")
    results = evaluate_rule_metrics(df, df_rules, events_df)

    out_file = reports_dir / "phase_3_rules_metrics.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Rules evaluation saved to {out_file}.")

    # Print summary table
    print("\n" + "=" * 90)
    print(f"{'Rule Name':<24} | {'Precision':<9} | {'Recall':<8} | {'F1':<6} | {'Ev Recall':<10} | {'Mean LT':<8} | {'FP Ep/dev/wk':<12}")
    print("-" * 90)
    for rname, rdata in results.items():
        sm = rdata["sample_metrics"]
        om = rdata["operational_metrics"]
        im = rdata["incident_metrics"]
        print(
            f"{rname:<24} | "
            f"{sm['precision']*100:8.2f}% | "
            f"{sm['recall']*100:7.2f}% | "
            f"{sm['f1']:6.4f} | "
            f"{im['event_recall']*100:9.1f}% | "
            f"{im['mean_lead_time_hours']:6.1f}h | "
            f"{om['fp_episodes_per_dev_week']:12.2f}"
        )
    print("=" * 90 + "\n")

    return results


if __name__ == "__main__":
    run_rule_evaluation()
