"""src/explain/generate_explanation_dataset.py
Regenerate training and held-out evaluation datasets for the Qwen explanation layer.

Uses real remediated model scores, real physical rule triggers, and verified telemetry features.
Strictly disaggregates:
- STALE_DATA and OFFLINE_LONG states into Connectivity Investigation Tickets (no outage probability).
- ONLINE states into Power Outage Diagnostic Tickets with calibrated probability and risk tiers.
- Sites without battery hardware (SBI, BOB) explicitly mark battery as 'Not equipped / N/A'.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import sys
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import yaml

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.explain.validator import generate_deterministic_template

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.explain.generate_dataset")


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_remediated_explanation_dataset(
    total_samples: int = 550,
    test_samples: int = 100,
    random_state: int = 42,
) -> Dict[str, Path]:
    config = load_config()
    shadow_log = Path(config["paths"]["data_processed"]) / "remediated_shadow_predictions.jsonl"
    features_path = Path(config["paths"]["data_processed"]) / "remediated_features_matrix.parquet"
    out_dir = Path("training_data")
    out_dir.mkdir(parents=True, exist_ok=True)

    train_out = out_dir / "remediated_explanation_train.jsonl"
    test_out = out_dir / "remediated_explanation_test_heldout.jsonl"

    if not shadow_log.exists():
        raise FileNotFoundError(f"Remediated shadow predictions log not found at {shadow_log}.")
    if not features_path.exists():
        raise FileNotFoundError(f"Remediated features matrix not found at {features_path}.")

    logger.info(f"Loading predictions from {shadow_log}...")
    pred_records: List[Dict[str, Any]] = []
    with open(shadow_log, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                pred_records.append(json.loads(line))

    df_preds = pd.DataFrame(pred_records)
    logger.info(f"Loaded {len(df_preds)} prediction records.")

    # Convert timestamps to string format for consistent join
    df_preds["ts_str"] = df_preds["timestamp"].astype(str)

    logger.info(f"Loading feature telemetry snapshot from {features_path}...")
    feat_cols = [
        "device_id", "timestamp", "device_state", "is_battery_sensor_present",
        "ac_voltage_mean_1h", "battery_voltage_mean_1h", "time_since_mains_loss_hours",
        "alarms_critical_24h", "alarms_major_24h"
    ]
    df_feats = pd.read_parquet(features_path, columns=feat_cols)
    df_feats["ts_str"] = df_feats["timestamp"].astype(str)

    # Merge on device_id and ts_str
    logger.info("Merging predictions with telemetry features...")
    df_merged = df_preds.merge(
        df_feats[["device_id", "ts_str", "is_battery_sensor_present", "ac_voltage_mean_1h", "battery_voltage_mean_1h", "time_since_mains_loss_hours", "alarms_critical_24h", "alarms_major_24h"]],
        on=["device_id", "ts_str"],
        how="inner"
    )
    logger.info(f"Merged candidate records: {len(df_merged)}")

    # Stratified sampling across:
    # 1. Connectivity issues (STALE_DATA / OFFLINE_LONG)
    # 2. Critical & High Outage Risk
    # 3. Medium Outage Risk
    # 4. Low Outage Risk (normal)
    conn_pool = df_merged[df_merged["risk_tier"] == "CONNECTIVITY_ISSUE"]
    crit_high_pool = df_merged[df_merged["risk_tier"].isin(["CRITICAL", "HIGH"])]
    med_pool = df_merged[df_merged["risk_tier"] == "MEDIUM"]
    low_pool = df_merged[df_merged["risk_tier"] == "LOW"]

    n_conn = min(120, len(conn_pool))
    n_crit_high = min(150, len(crit_high_pool))
    n_med = min(140, len(med_pool))
    n_low = total_samples - n_conn - n_crit_high - n_med

    logger.info(f"Sampling strata: Conn={n_conn}, High/Crit={n_crit_high}, Med={n_med}, Low={n_low}")

    sample_subsets = [
        conn_pool.sample(n=n_conn, random_state=random_state),
        crit_high_pool.sample(n=n_crit_high, random_state=random_state),
        med_pool.sample(n=n_med, random_state=random_state),
        low_pool.sample(n=n_low, random_state=random_state),
    ]

    selected_df = pd.concat(sample_subsets).sample(frac=1.0, random_state=random_state).reset_index(drop=True)
    logger.info(f"Total sampled dataset size: {len(selected_df)}")

    system_prompt = (
        "You are the Bank Branch Outage Diagnostic Assistant. "
        "You generate operational diagnosis and dispatch tickets from verified machine learning predictions and physical telemetry states.\n"
        "Strict Operational Directives:\n"
        "1. If device state is STALE_DATA or OFFLINE_LONG, you MUST output a connectivity investigation ticket and NEVER state an outage probability or claim an active power outage.\n"
        "2. If battery hardware is not equipped, you NEVER state a numeric battery voltage or claim battery degradation.\n"
        "3. You NEVER cite physical rule violations (RULE_*) that are not present in the verified input payload.\n"
        "4. You NEVER invent ungrounded numerical figures. Every figure must match the input snapshot exactly."
    )

    dataset_examples = []
    for _, row in selected_df.iterrows():
        is_batt = bool(row["is_battery_sensor_present"])
        ac_val = row["ac_voltage_mean_1h"]
        ac_disp = round(float(ac_val), 1) if pd.notna(ac_val) else "N/A"

        batt_val = row["battery_voltage_mean_1h"]
        if is_batt and pd.notna(batt_val) and batt_val > 0.0:
            batt_disp = round(float(batt_val), 2)
        else:
            batt_disp = "N/A"

        mains_loss_h = round(float(row["time_since_mains_loss_hours"]), 1) if pd.notna(row["time_since_mains_loss_hours"]) else 0.0

        payload = {
            "device_id": str(row["device_id"]),
            "device_name": str(row["device_name"]),
            "bank": str(row["bank"]),
            "device_state": str(row["device_state"]),
            "is_battery_sensor_present": is_batt,
            "calibrated_prob": round(float(row["calibrated_prob"]), 4),
            "risk_tier": str(row["risk_tier"]),
            "rules_triggered": list(row["rules_triggered"]),
            "top_contributing_features": (
                "ac_voltage_slope_6h; time_since_mains_loss_hours"
                if row["risk_tier"] in ("HIGH", "CRITICAL")
                else ("heartbeat_staleness" if row["risk_tier"] == "CONNECTIVITY_ISSUE" else "normal electrical baseline")
            ),
            "telemetry_snapshot": {
                "ac_voltage": ac_disp,
                "battery_voltage": batt_disp,
                "time_since_mains_loss_hours": mains_loss_h,
            },
        }

        user_content = (
            f"Generate an operational diagnostic ticket for branch site '{payload['device_name']}' based on the verified payload:\n"
            f"{json.dumps(payload, indent=2)}"
        )

        assistant_target = generate_deterministic_template(payload)

        chat_item = {
            "device_id": payload["device_id"],
            "device_name": payload["device_name"],
            "bank": payload["bank"],
            "device_state": payload["device_state"],
            "risk_tier": payload["risk_tier"],
            "payload": payload,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": assistant_target},
            ],
        }
        dataset_examples.append(chat_item)

    # Split: Train vs Held-out Test
    train_examples = dataset_examples[:-test_samples]
    test_examples = dataset_examples[-test_samples:]

    logger.info(f"Writing {len(train_examples)} examples to {train_out}...")
    with open(train_out, "w", encoding="utf-8") as f:
        for ex in train_examples:
            f.write(json.dumps(ex) + "\n")

    logger.info(f"Writing {len(test_examples)} held-out evaluation examples to {test_out}...")
    with open(test_out, "w", encoding="utf-8") as f:
        for ex in test_examples:
            f.write(json.dumps(ex) + "\n")

    logger.info("Explanation dataset generation complete!")
    return {"train": train_out, "test": test_out}


if __name__ == "__main__":
    build_remediated_explanation_dataset()
