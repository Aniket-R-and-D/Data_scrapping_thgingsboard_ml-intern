"""scripts/thingsboard/full_branch_realtime_audit.py
Full 1-by-1 Device and Branch-Specific Real-Time vs Predicted Testing Suite.
Queries ThingsBoard live API for fresh telemetry and server attributes across all branches,
evaluates Isolation Forest anomaly score, 24h Outage Probability, Unified Risk Tier,
and verifies real-time telemetry concordance for every device.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.predict.send_daily_risk_digest import load_environment, compute_fleet_risk
from src.live.recovery_gate import probe_device_telemetry_live, fetch_fleet_liveness

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("full_branch_audit")


def run_full_realtime_audit() -> Dict[str, Any]:
    env = load_environment()
    tb_host = env["TB_HOST"].rstrip("/")
    tb_email = env["TB_EMAIL"]
    tb_password = env["TB_PASSWORD"]

    # 1. Login to ThingsBoard
    login_resp = requests.post(
        f"{tb_host}/api/auth/login",
        json={"username": tb_email, "password": tb_password},
        timeout=10
    )
    if login_resp.status_code != 200:
        raise RuntimeError(f"ThingsBoard login failed: {login_resp.status_code} {login_resp.text}")
    tb_token = login_resp.json()["token"]
    headers = {"X-Authorization": f"Bearer {tb_token}"}
    logger.info("Authenticated with ThingsBoard successfully.")

    # 2. Compute Fleet Risk using our newly calibrated pipeline
    fleet_df, summary = compute_fleet_risk(env)
    logger.info(f"Computed fleet risk for {len(fleet_df)} monitored branches.")

    # 3. Bulk Liveness
    liveness_map = fetch_fleet_liveness(tb_host, tb_token)
    logger.info(f"Loaded bulk liveness for {len(liveness_map)} total tenant devices.")

    # 4. Target Branch Cohorts
    canara_test_set = [
        "CANARA-BARUIPUR", "CANARA-KAMARHATI", "CANARA-CHETLA",
        "CANARA-BURNPUR", "CANARA-HALDI2"
    ]

    operational_negative_cohort = [
        "CANARA-BURNPUR", "DX7", "BOI-MALLAGURI", "BOI-CUTTACK", "BOI-SINGH-MORE",
        "BOI-PANASPADA", "BOI-LALPUR", "BOI-HARDA-ROAD", "BOI-HGBAD", "BOI-BHUBANESHWAR",
        "BOI-SONAHATU", "BOI-AIZWAL", "BOI-RAIGANJ", "BOI-PURI", "BOI-SITHIO",
        "BOI-KALYANI", "BOI-BAGULA", "BOI-COOCHBEHAR", "BOI-BERO", "BOI-MALDATOWN",
        "BOI-R-BAZAR", "SBI BHAWAL"
    ]

    active_recovered_cohort = [
        "BOI-BAGULA", "BOI-BABAI", "BOI-SALTLAKE", "DX2"
    ]

    # Combine into unique ordered list of priority branches
    priority_names = []
    for name in canara_test_set + operational_negative_cohort + active_recovered_cohort:
        if name not in priority_names:
            priority_names.append(name)

    now_ms = time.time() * 1000

    results = []

    # Map fleet_df by device_name and device_id
    fleet_by_name = fleet_df.set_index("device_name").to_dict(orient="index")
    fleet_by_id = fleet_df.set_index("device_id").to_dict(orient="index")

    # Map liveness by name
    liveness_by_name = {v.get("device_name"): v for v in liveness_map.values() if v.get("device_name")}

    print("\n" + "=" * 130)
    print("DETAILED 1-BY-1 DEVICE & BRANCH SPECIFIC REAL-TIME VS PREDICTED VERIFICATION AUDIT")
    print("=" * 130)

    for branch_name in priority_names:
        fleet_row = fleet_by_name.get(branch_name)
        live_info = liveness_by_name.get(branch_name)

        dev_id = None
        if fleet_row:
            dev_id = fleet_row.get("device_id")
        elif live_info:
            dev_id = live_info.get("device_id")

        if not dev_id:
            print(f"\n[BRANCH: {branch_name}] -> NOT FOUND IN THINGSBOARD OR FLEET DATASET")
            results.append({
                "branch_name": branch_name,
                "status": "NOT_FOUND",
                "notes": "Device not registered in ThingsBoard or historical parquet"
            })
            continue

        # Fetch deep live timeseries & server attributes
        deep_eval = probe_device_telemetry_live(dev_id, tb_host, tb_token)

        # Telemetry values
        ts_resp = requests.get(
            f"{tb_host}/api/plugins/telemetry/DEVICE/{dev_id}/values/timeseries",
            headers=headers,
            timeout=8
        ).json()

        tb_active = live_info.get("active") if live_info else None
        tb_age_hours = live_info.get("age_hours") if live_info else None

        # Check most recent physical packet timestamp across all keys
        latest_ts_packet = 0
        latest_packet_key = "none"
        for k, v_list in ts_resp.items():
            if isinstance(v_list, list) and len(v_list) > 0:
                t = v_list[0].get("ts", 0)
                # Ignore hourly synthetic zero injections to find actual physical activity
                if k not in ["alarmCount", "cctvLastHour", "errorCount", "uptimeTotal",
                             "accessControlLastHour", "basLastHour", "fasLastHour",
                             "gatewayLastHour", "iasLastHour", "timeLockLastHour",
                             "ai_health_tier", "ai_anomaly_score", "ai_outage_risk_prob",
                             "ai_primary_issue", "ai_recommended_action", "ai_last_evaluated"]:
                    if t > latest_ts_packet:
                        latest_ts_packet = t
                        latest_packet_key = k

        physical_age_hours = (now_ms - latest_ts_packet) / 3600000.0 if latest_ts_packet > 0 else None

        # Predicted values
        pred_tier = fleet_row.get("unified_tier") if fleet_row else "NOT_IN_FLEET"
        raw_tier = fleet_row.get("raw_anomaly_tier") if fleet_row else "N/A"
        anom_score = fleet_row.get("anomaly_score") if fleet_row else None
        outage_p = fleet_row.get("outage_prob_24h") if fleet_row else None
        primary_issue = fleet_row.get("primary_issue") if fleet_row else "N/A"
        recommended_action = fleet_row.get("recommended_action") if fleet_row else "N/A"

        # Vitals from recovery probe
        vitals = deep_eval.vitals

        audit_entry = {
            "branch_name": branch_name,
            "device_id": dev_id,
            "tb_active": tb_active,
            "tb_age_hours": round(tb_age_hours, 1) if tb_age_hours is not None else None,
            "physical_age_hours": round(physical_age_hours, 1) if physical_age_hours is not None else None,
            "last_physical_packet_key": latest_packet_key,
            "anomaly_score": anom_score,
            "raw_anomaly_tier": raw_tier,
            "outage_prob_24h": outage_p,
            "predicted_unified_tier": pred_tier,
            "primary_issue": primary_issue,
            "recommended_action": recommended_action,
            "is_recovered_live": deep_eval.is_recovered,
            "recovery_reason": deep_eval.summary_reason,
            "vitals": vitals,
        }
        results.append(audit_entry)

        # Print human-readable report block
        print(f"\nBranch: {branch_name} ({dev_id})")
        print(f"  Real-Time Connectivity : Active={tb_active} | Last Activity Age={round(tb_age_hours, 1) if tb_age_hours else 'N/A'}h | Physical Sensor Age={round(physical_age_hours, 1) if physical_age_hours else 'N/A'}h (via '{latest_packet_key}')")
        print(f"  Predicted ML Metrics   : Anomaly Score={anom_score} ({raw_tier}) | 24h Outage Prob={f'{outage_p*100:.1f}%' if outage_p is not None else 'N/A'}")
        print(f"  Unified AI Health Tier : {pred_tier}")
        print(f"  Diagnosed Primary Issue: {primary_issue}")
        print(f"  Recommended Action     : {recommended_action}")
        if deep_eval.is_recovered:
            print(f"  Live Recovery Confirmed: YES ({deep_eval.summary_reason}) | Vitals: {vitals}")
        elif vitals:
            print(f"  Live Sensor Vitals     : {vitals}")

    print("\n" + "=" * 130)
    print("AUDIT SUMMARY BY COHORT")
    print("=" * 130)

    # Breakdown by cohort
    print("\n1. CANARA TEST SET COHORT:")
    for r in results:
        if r["branch_name"] in canara_test_set:
            tier_s = str(r.get("predicted_unified_tier") or "N/A")
            iss_s = str(r.get("primary_issue") or "N/A")
            print(f"  {r['branch_name']:<18} | Live Active: {str(r.get('tb_active')):<5} | Silent For: {str(r.get('tb_age_hours')):<7}h | Anom Score: {str(r.get('anomaly_score')):<7} | Outage P: {str(r.get('outage_prob_24h')):<6} | Tier: {tier_s:<16} | Issue: {iss_s}")

    print("\n2. SBI DEAD INLIER COHORT:")
    for r in results:
        if "SBI" in r["branch_name"]:
            tier_s = str(r.get("predicted_unified_tier") or "N/A")
            iss_s = str(r.get("primary_issue") or "N/A")
            print(f"  {r['branch_name']:<18} | Live Active: {str(r.get('tb_active')):<5} | Silent For: {str(r.get('tb_age_hours')):<7}h | Anom Score: {str(r.get('anomaly_score')):<7} | Outage P: {str(r.get('outage_prob_24h')):<6} | Tier: {tier_s:<16} | Issue: {iss_s}")

    print("\n3. ACTIVE & SERVICED-RECOVERED COHORT:")
    for r in results:
        if r.get("is_recovered_live") or r["branch_name"] in active_recovered_cohort or r.get("predicted_unified_tier") == "SERVICED_RECOVERED":
            tier_s = str(r.get("predicted_unified_tier") or "N/A")
            iss_s = str(r.get("primary_issue") or "N/A")
            print(f"  {r['branch_name']:<18} | Live Active: {str(r.get('tb_active')):<5} | Silent For: {str(r.get('tb_age_hours')):<7}h | Anom Score: {str(r.get('anomaly_score')):<7} | Outage P: {str(r.get('outage_prob_24h')):<6} | Tier: {tier_s:<16} | Issue: {iss_s}")

    print("\n4. OPERATIONAL BRANCHES WITH NEGATIVE ANOMALY SCORES:")
    for r in results:
        if r["branch_name"] in operational_negative_cohort and r["branch_name"] not in canara_test_set and "SBI" not in r["branch_name"]:
            tier_s = str(r.get("predicted_unified_tier") or "N/A")
            iss_s = str(r.get("primary_issue") or "N/A")
            print(f"  {r['branch_name']:<18} | Live Active: {str(r.get('tb_active')):<5} | Silent For: {str(r.get('tb_age_hours')):<7}h | Anom Score: {str(r.get('anomaly_score')):<7} | Outage P: {str(r.get('outage_prob_24h')):<6} | Tier: {tier_s:<16} | Issue: {iss_s}")

    # Save to JSON audit file
    audit_path = ROOT / "reports/full_branch_realtime_audit.json"
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump({
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "total_branches_audited": len(results),
            "results": results,
            "fleet_summary": summary,
        }, f, indent=2)
    logger.info(f"Saved full audit results to {audit_path}")

    return {"results": results, "summary": summary}


if __name__ == "__main__":
    run_full_realtime_audit()
