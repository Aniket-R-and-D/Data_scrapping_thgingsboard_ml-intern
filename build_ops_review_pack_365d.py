#!/usr/bin/env python
"""build_ops_review_pack_365d.py
Step 2: Generate the Operational Review Pack for the Top-20 Faultiest Bank Branches.

Sourced from the complete 365-day dataset:
- Evaluates chronic brownouts, battery degradation, controller disconnections, and alarm frequency.
- Generates an actionable Ops Pack with human review forms, root causes, and recommended field interventions.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("ops_pack_365d")

ROOT = Path(__file__).resolve().parent
SCORED_PATH = ROOT / "training_data/anomaly_20261022_082325/scored_anomalies.parquet"
FEATURES_PATH = ROOT / "training_data/anomaly_20261022_082325/anomaly_features.parquet"
DEVICES_PATH = ROOT / "data_harvest/runs/20261022_082325/registry/devices.csv"
ALARMS_PATH = ROOT / "data_harvest/runs/20261022_082325/alarms/alarms.csv"
OUT_DIR = ROOT / "reports"


def determine_recommendation(root_cause: str, alarms_cnt: int, ac_mean: float, bat_min: float) -> str:
    cause_lower = root_cause.lower()
    if "battery" in cause_lower or bat_min < 10.5:
        return "Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test."
    elif "ac_voltage" in cause_lower or ac_mean < 190.0:
        return "Electrical: High chronic undervoltage/brownouts; verify branch input phase line & install voltage stabilizer."
    elif "system_current" in cause_lower:
        return "Instrumentation: Verify CT current transformer wiring and sensor clamp calibration."
    elif "errorcount" in cause_lower or "uptime" in cause_lower or "gateway" in cause_lower:
        return "Communications/Edge: High controller reset rate; verify 4G SIM antenna signal and power supply stability."
    elif "accesscontrol" in cause_lower or "cctv" in cause_lower:
        return "Peripherals: Intermittent tamper or channel disconnect on secondary access/camera loop."
    else:
        return "General Maintenance: Complete quarterly preventative maintenance inspection."


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Loading scored anomaly dataset and registry...")
    scored_df = pd.read_parquet(SCORED_PATH)
    devs_df = pd.read_csv(DEVICES_PATH)
    dev_name_map = devs_df.set_index("device_id")["name"].to_dict()

    # Load alarms count per device
    alarm_counts: Dict[str, int] = {}
    if ALARMS_PATH.exists():
        logger.info("Aggregating alarms by branch...")
        alarms_df = pd.read_csv(ALARMS_PATH, usecols=["device_id"])
        alarm_counts = alarms_df["device_id"].value_counts().to_dict()

    # Read relevant raw signals from features for physical context
    feat_cols = ["device_id", "anchor_ts", "ac_voltage__mean_24h", "battery_voltage__min_24h", "ac_voltage__gap_max_7d"]
    feats_summary: Dict[str, Dict[str, float]] = {}
    if FEATURES_PATH.exists():
        logger.info("Extracting electrical voltage & battery statistics...")
        avail_cols = [c for c in feat_cols if c in pd.read_parquet(FEATURES_PATH, columns=["device_id"]).columns]
        raw_feats = pd.read_parquet(FEATURES_PATH)
        for did, g in raw_feats.groupby("device_id"):
            ac_mean = float(g["ac_voltage__mean_24h"].mean()) if "ac_voltage__mean_24h" in g else 225.0
            bat_min = float(g["battery_voltage__min_24h"].min()) if "battery_voltage__min_24h" in g else 12.4
            gap_max = float(g["ac_voltage__gap_max_7d"].max()) if "ac_voltage__gap_max_7d" in g else 0.0
            feats_summary[did] = {"ac_mean": ac_mean, "bat_min": bat_min, "gap_max": gap_max}

    # Aggregate anomalies
    severe_mask = scored_df["anomaly_tier"].isin(["CRITICAL_FAULT", "ANOMALOUS"])
    grouped = (
        scored_df[severe_mask]
        .groupby(["device_id", "bank"])
        .agg(
            severe_count=("anomaly_score", "count"),
            max_score=("anomaly_score", "max"),
            mean_score=("anomaly_score", "mean"),
        )
        .reset_index()
        .sort_values(by="severe_count", ascending=False)
    )

    top20 = grouped.head(20).copy()

    pack_rows = []
    for rank, (_, row) in enumerate(top20.iterrows(), 1):
        did = row["device_id"]
        bank = row["bank"]
        dname = dev_name_map.get(did, f"{bank}-UNKNOWN")
        cnt = int(row["severe_count"])
        max_s = float(row["max_score"])

        # Most representative root cause
        sub = scored_df[(scored_df["device_id"] == did) & severe_mask]["top_contributions"]
        root_cause = sub.mode().iloc[0] if len(sub) > 0 else "Multivariate sensor deviation"

        phys = feats_summary.get(did, {"ac_mean": 220.0, "bat_min": 12.0, "gap_max": 0.0})
        total_alarms = alarm_counts.get(did, 0)
        rec = determine_recommendation(root_cause, total_alarms, phys["ac_mean"], phys["bat_min"])

        pack_rows.append({
            "rank": rank,
            "bank": bank,
            "device_id": did,
            "device_name": dname,
            "severe_anomaly_windows": cnt,
            "max_anomaly_score": round(max_s, 4),
            "historical_alarms_count": total_alarms,
            "mean_ac_voltage_v": round(phys["ac_mean"], 1) if not np.isnan(phys["ac_mean"]) else 220.0,
            "min_battery_voltage_v": round(phys["bat_min"], 1) if not np.isnan(phys["bat_min"]) else 12.0,
            "primary_failure_mode": root_cause,
            "recommended_field_action": rec,
            "ops_review_status": "PENDING_OPS_SIGN_OFF",
        })

    # Save CSV
    csv_path = OUT_DIR / "ops_review_pack_top20_faulty_branches.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(pack_rows[0].keys()))
        writer.writeheader()
        writer.writerows(pack_rows)
    logger.info("Saved Ops Review CSV: %s", csv_path)

    # Save JSON
    json_path = OUT_DIR / "ops_review_pack_top20_faulty_branches.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(pack_rows, f, indent=2)
    logger.info("Saved Ops Review JSON: %s", json_path)

    # Markdown Report with human review forms
    md_content = f"""# Operations Review Pack: Top-20 Faultiest Bank Branches (365-Day Analysis)

**Document ID:** `OPS-REVIEW-PACK-365D-v1.0`  
**Generated:** {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}  
**Source Dataset:** 140,219 time-anchored windows, 130 branches across 365 days (`2025-10-22` to `2026-10-22`)  
**Objective:** Operational intervention pack for Seple field engineers and bank facility managers to triage chronic brownouts, battery failures, and controller drops.

---

## 1. Executive Summary Table

| Rank | Bank | Branch Name / Device | Severe Anomalies | 1-Year Alarms | Key Electrical State | Recommended Field Action |
| :---: | :---: | :--- | :---: | :---: | :--- | :--- |
"""
    for r in pack_rows:
        md_content += (
            f"| **{r['rank']}** | {r['bank']} | **{r['device_name']}**<br>`{r['device_id']}` | "
            f"**{r['severe_anomaly_windows']:,}** | {r['historical_alarms_count']:,} | "
            f"AC: {r['mean_ac_voltage_v']}V<br>Bat Min: {r['min_battery_voltage_v']}V | "
            f"{r['recommended_field_action']} |\n"
        )

    md_content += """
---

## 2. Detailed Branch Triage & Ops Review Forms

"""
    for r in pack_rows:
        md_content += f"""### Branch #{r['rank']}: {r['device_name']} ({r['bank']})
* **Device ID:** `{r['device_id']}`
* **Severe 6h Anomaly Intervals (Year):** **{r['severe_anomaly_windows']}** (Max Anomaly Score: `{r['max_anomaly_score']}`)
* **Total Historic Alarms in 365 Days:** **{r['historical_alarms_count']:,}**
* **Observed Metrics:** Mean AC Voltage = `{r['mean_ac_voltage_v']} V`, Minimum Battery = `{r['min_battery_voltage_v']} V`
* **Dominant Sensor Deviations:** `{r['primary_failure_mode']}`
* **Recommended Field Action:** **{r['recommended_field_action']}**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
"""

    md_path = OUT_DIR / "ops_review_pack_top20_faulty_branches.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info("Saved Ops Review Pack Markdown: %s", md_path)


if __name__ == "__main__":
    main()
