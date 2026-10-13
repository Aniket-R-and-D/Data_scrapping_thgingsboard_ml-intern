"""src/shadow/shadow_job.py
Phase 5 Shadow Mode Job for Bank Branch Outage Prediction.

Operational Safety Guarantees:
1. READ-ONLY scoring: writes predictions to append-only log and SQLite database.
2. NEVER creates ThingsBoard alarms and NEVER triggers automated field dispatches.
3. Scores fleet telemetry at 15-minute intervals.
4. Logs calibrated outage probability, risk tier, deterministic rule hits, and top features.
"""

import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
import yaml

import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.rules.eval_rules import apply_rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.shadow.shadow_job")

FEATURE_COLS = [
    "ac_voltage",
    "ac_voltage_mean_1h",
    "ac_voltage_std_1h",
    "ac_voltage_min_1h",
    "ac_voltage_max_1h",
    "ac_voltage_mean_6h",
    "ac_voltage_std_6h",
    "ac_voltage_min_6h",
    "ac_voltage_max_6h",
    "ac_voltage_mean_24h",
    "ac_voltage_std_24h",
    "ac_voltage_slope_6h",
    "ac_zscore_7d",
    "battery_voltage",
    "battery_voltage_mean_1h",
    "battery_voltage_std_1h",
    "battery_voltage_min_1h",
    "battery_voltage_max_1h",
    "battery_voltage_mean_6h",
    "battery_voltage_std_6h",
    "battery_voltage_mean_24h",
    "battery_voltage_std_24h",
    "battery_discharge_rate_1h",
    "battery_discharge_rate_6h",
    "battery_zscore_7d",
    "heartbeat_gap_seconds",
    "telemetry_age_seconds",
    "missing_indicator",
    "time_since_mains_loss_hours",
    "mains_loss_count_7d",
    "fas_fault_flag",
    "storage_fault_flag",
    "nvr_fault_flag",
    "hour_of_day",
    "day_of_week",
    "sin_hour",
    "cos_hour",
]


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ShadowScorer:
    def __init__(self, config_path: str = "configs/config.yaml"):
        self.config = load_config(config_path)
        models_dir = Path(self.config["paths"]["models_dir"]) / "outage_predictor"
        self.model_path = models_dir / "lightgbm_outage_predictor.joblib"
        self.calibrator_path = models_dir / "platt_calibrator.joblib"

        if not self.model_path.exists() or not self.calibrator_path.exists():
            raise FileNotFoundError(f"Model or calibrator not found in {models_dir}. Run Phase 4 training first.")

        self.model = joblib.load(self.model_path)
        self.calibrator = joblib.load(self.calibrator_path)
        self.decision_threshold = 0.090  # tuned on validation in Phase 4

        # Paths
        self.log_path = Path(self.config["paths"]["data_processed"]) / "shadow_predictions.jsonl"
        self.db_path = Path(self.config["paths"]["data_processed"]) / "shadow_predictions.db"
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shadow_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    bank TEXT NOT NULL,
                    calibrated_prob REAL NOT NULL,
                    raw_prob REAL NOT NULL,
                    risk_tier TEXT NOT NULL,
                    rules_triggered TEXT NOT NULL,
                    action_taken TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dev_time ON shadow_log(device_id, timestamp)")
            conn.commit()

    @staticmethod
    def determine_risk_tier(prob: float, rules_hit: List[str]) -> str:
        """Deterministic operational tier assignment based on validated probability & physical rules."""
        if prob >= 0.65 or ("rule_mains_lost" in rules_hit and "rule_battery_critical" in rules_hit):
            return "CRITICAL"
        elif prob >= 0.35 or "rule_ac_zero" in rules_hit or "rule_mains_lost" in rules_hit:
            return "HIGH"
        elif prob >= 0.090 or len(rules_hit) > 0:
            return "MEDIUM"
        else:
            return "LOW"

    def score_slice(self, slice_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Score a synchronous fleet telemetry snapshot across all branches."""
        if slice_df.empty:
            return []

        # 1. Apply physical rules
        rules_df = apply_rules(slice_df, self.config)

        # 2. Predict ML probabilities
        X = slice_df[FEATURE_COLS]
        raw_probs = self.model.predict_proba(X)[:, 1]
        calib_probs = self.calibrator.predict_proba(raw_probs.reshape(-1, 1))[:, 1]

        records = []
        with open(self.log_path, "a", encoding="utf-8") as jsonl_f, sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for idx, (_, row) in enumerate(slice_df.iterrows()):
                prob = float(calib_probs[idx])
                raw_p = float(raw_probs[idx])

                # Identify triggered rules
                triggered_rules = [
                    col for col in rules_df.columns
                    if col.startswith("rule_") and col != "rule_combined" and rules_df.iloc[idx][col]
                ]

                tier = self.determine_risk_tier(prob, triggered_rules)

                rec = {
                    "timestamp": str(row["timestamp"]),
                    "device_id": str(row["device_id"]),
                    "device_name": str(row["device_name"]),
                    "bank": str(row.get("bank", "UNKNOWN")),
                    "calibrated_prob": round(prob, 4),
                    "raw_prob": round(raw_p, 4),
                    "risk_tier": tier,
                    "rules_triggered": triggered_rules,
                    "action_taken": "LOG_TELEMETRY_ONLY (NO_DISPATCH)",
                    "telemetry_snapshot": {
                        "ac_voltage": float(row["ac_voltage"]),
                        "battery_voltage": float(row["battery_voltage"]),
                        "heartbeat_gap_seconds": float(row["heartbeat_gap_seconds"]),
                        "time_since_mains_loss_hours": float(row["time_since_mains_loss_hours"]),
                    },
                }

                # Write append-only JSONL
                jsonl_f.write(json.dumps(rec) + "\n")

                # Insert SQLite audit row
                cursor.execute(
                    """
                    INSERT INTO shadow_log (timestamp, device_id, device_name, bank, calibrated_prob, raw_prob, risk_tier, rules_triggered, action_taken)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec["timestamp"],
                        rec["device_id"],
                        rec["device_name"],
                        rec["bank"],
                        rec["calibrated_prob"],
                        rec["raw_prob"],
                        rec["risk_tier"],
                        ",".join(triggered_rules),
                        rec["action_taken"],
                    ),
                )
                records.append(rec)
            conn.commit()

        return records


def run_shadow_simulation(days: int = 7) -> int:
    """Run simulated 15-minute scheduled shadow mode job over the most recent fleet data."""
    config = load_config()
    features_path = Path(config["paths"]["data_processed"]) / "features_matrix.parquet"
    registry_path = Path(config["paths"]["data_raw_registry"]) / "devices.parquet"

    logger.info(f"Loading features for shadow simulation from {features_path}...")
    df = pd.read_parquet(features_path)
    df["dt"] = pd.to_datetime(df["timestamp"], utc=True)

    devs = pd.read_parquet(registry_path)
    bank_map = devs.set_index("device_id")["bank"].to_dict()
    df["bank"] = df["device_id"].map(bank_map).fillna("UNKNOWN")

    # Select latest 'days' of data
    t_max = df["dt"].max()
    t_start = t_max - pd.Timedelta(days=days)
    sim_df = df[df["dt"] >= t_start].copy().sort_values("dt")

    logger.info(f"Simulating shadow scoring for {sim_df['device_id'].nunique()} devices from {t_start} to {t_max} ({len(sim_df)} rows)...")

    scorer = ShadowScorer()

    # Clear previous shadow log for clean replay
    if scorer.log_path.exists():
        with open(scorer.log_path, "w", encoding="utf-8") as f:
            pass
    with sqlite3.connect(scorer.db_path) as conn:
        conn.execute("DELETE FROM shadow_log")
        conn.commit()

    # Score in 15-minute timestamps
    unique_ts = sim_df["dt"].unique()
    total_scored = 0
    for ts in unique_ts:
        ts_slice = sim_df[sim_df["dt"] == ts]
        records = scorer.score_slice(ts_slice)
        total_scored += len(records)

    logger.info(f"Shadow simulation complete! Logged {total_scored} predictions across {len(unique_ts)} 15-min intervals.")
    return total_scored


if __name__ == "__main__":
    run_shadow_simulation(days=7)
