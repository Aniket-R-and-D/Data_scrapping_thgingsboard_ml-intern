#!/usr/bin/env python
"""Integrate fresh ThingsBoard MCP fleet anchors into production training dataset.

Reads:
  - training_data/mcp_fleet_dataset.jsonl (161 fresh fleet devices, 174 keys, alarms)
  - training_data/anomaly_20261005_041300_v2/anomaly_features.parquet
  - training_data/supervised_outage_oct2026.parquet
  - models/gbm_outage_oct2026/feature_names.json

Outputs:
  - training_data/anomaly_features_augmented_oct2026.parquet
  - training_data/supervised_outage_augmented_oct2026.parquet
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
JSONL_PATH = ROOT / "training_data/mcp_fleet_dataset.jsonl"
FEATS_ORIG = ROOT / "training_data/anomaly_20261005_041300_v2/anomaly_features.parquet"
LABELS_ORIG = ROOT / "training_data/supervised_outage_oct2026.parquet"
FEATURE_NAMES_PATH = ROOT / "models/gbm_outage_oct2026/feature_names.json"

FEATS_AUG = ROOT / "training_data/anomaly_features_augmented_oct2026.parquet"
LABELS_AUG = ROOT / "training_data/supervised_outage_augmented_oct2026.parquet"


def safe_float(val):
    if val is None:
        return np.nan
    try:
        return float(val)
    except (ValueError, TypeError):
        return np.nan


def parse_mcp_anchors(jsonl_path: Path, expected_cols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor_ts = pd.Timestamp("2026-10-12 14:30:00", tz="UTC")
    feat_rows = []
    label_rows = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            did = rec.get("device_id")
            lt = rec.get("latest_telemetry", {})
            a_meta = rec.get("alarms_summary", {})

            # 1. Determine Label
            active_unack = a_meta.get("by_status", {}).get("ACTIVE_UNACK", 0)
            t_stat = lt.get("ticketStatus", {})
            if isinstance(t_stat, str):
                try:
                    t_stat = json.loads(t_stat)
                except Exception:
                    t_stat = {}

            nvr_off = t_stat.get("NVR_OFF") in (True, "true", "1")
            fas_off = t_stat.get("FAS_OFF") in (True, "true", "1")
            pwr_off = t_stat.get("POWER_OFF") in (True, "true", "1")
            bat_low = t_stat.get("BATTERY_LOW") in (True, "true", "1")
            hdd_err = t_stat.get("H_ERROR") in (True, "true", "1")

            is_outage = int(active_unack > 0 or nvr_off or fas_off or pwr_off or bat_low or hdd_err)

            label_rows.append({
                "device_id": did,
                "anchor_ts": anchor_ts,
                "y_outage_24h": is_outage,
                "label_status": "measured",
                "label_horizon_end": anchor_ts + pd.Timedelta(hours=24),
                "censor_reason": "",
            })

            # 2. Extract Flat Feature Map from Telemetry
            flat_map = {}
            for k, v in lt.items():
                if isinstance(v, (int, float)):
                    flat_map[k] = float(v)
                elif isinstance(v, str):
                    fval = safe_float(v)
                    if not np.isnan(fval):
                        flat_map[k] = fval
                    else:
                        # Try parsing JSON string inside value
                        if v.startswith("{") and v.endswith("}"):
                            try:
                                sub = json.loads(v)
                                for sub_k, sub_v in sub.items():
                                    sub_fval = safe_float(sub_v)
                                    if not np.isnan(sub_fval):
                                        flat_map[f"{k}_{sub_k}"] = sub_fval
                            except Exception:
                                pass

            # 3. Populate expected feature row
            row_dict = {"device_id": did, "anchor_ts": anchor_ts}
            for col in expected_cols:
                if col in ("device_id", "anchor_ts"):
                    continue
                # Match col patterns like <key>__last or <key>__mean_1h
                val = np.nan
                for k, v in flat_map.items():
                    if col.startswith(f"{k}__"):
                        val = v
                        break
                row_dict[col] = val

            feat_rows.append(row_dict)

    df_feats = pd.DataFrame(feat_rows)
    df_labels = pd.DataFrame(label_rows)
    return df_feats, df_labels


def main():
    logger.info("Loading existing production features and labels...")
    orig_feats = pd.read_parquet(FEATS_ORIG)
    orig_labels = pd.read_parquet(LABELS_ORIG)

    logger.info(f"Original features shape: {orig_feats.shape}, labels shape: {orig_labels.shape}")

    cols = orig_feats.columns.tolist()
    logger.info(f"Extracting fresh anchors from {JSONL_PATH}...")
    fresh_feats, fresh_labels = parse_mcp_anchors(JSONL_PATH, cols)
    logger.info(f"Fresh anchors parsed: {len(fresh_feats)} devices. Outage positive rate: {fresh_labels['y_outage_24h'].mean():.2%}")

    # Ensure consistent dtypes
    fresh_labels["y_outage_24h"] = fresh_labels["y_outage_24h"].astype("Int64")
    orig_labels["anchor_ts"] = pd.to_datetime(orig_labels["anchor_ts"], utc=True)
    orig_feats["anchor_ts"] = pd.to_datetime(orig_feats["anchor_ts"], utc=True)

    aug_feats = pd.concat([orig_feats, fresh_feats], ignore_index=True)
    aug_labels = pd.concat([orig_labels, fresh_labels], ignore_index=True)

    logger.info(f"Augmented features shape: {aug_feats.shape}")
    logger.info(f"Augmented labels shape: {aug_labels.shape}")
    measured = aug_labels[aug_labels["label_status"] == "measured"]
    logger.info(f"Total measured training samples: {len(measured)} (positive: {(measured['y_outage_24h'] == 1).sum()})")

    logger.info(f"Saving augmented features to {FEATS_AUG}...")
    aug_feats.to_parquet(FEATS_AUG, index=False)

    logger.info(f"Saving augmented labels to {LABELS_AUG}...")
    aug_labels.to_parquet(LABELS_AUG, index=False)

    logger.info("Fresh telemetry anchors integration complete!")


if __name__ == "__main__":
    main()
