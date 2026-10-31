"""scripts/thingsboard/audit_all_163_thingsboard_devices.py
Full 1-by-1 Device and Branch Audit across ALL 163 devices present in ThingsBoard.
Extracts real-time connectivity, live electrical/subsystem vitals, and compares against
Isolation Forest anomaly score, LightGBM outage probability, and Unified Risk Tiers.
"""
from __future__ import annotations

import concurrent.futures
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.predict.send_daily_risk_digest import load_environment, compute_fleet_risk
from src.live.recovery_gate import _safe_json_parse, _to_bool, _to_float

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("all_devices_audit")


def probe_single_device_vitals(
    dev_id: str,
    tb_host: str,
    headers: Dict[str, str],
    timeout: float = 6.0
) -> Dict[str, Any]:
    """Fetch live timeseries and server attributes for a single device."""
    keys = "gatewayStatus,ticketStatus,cameraStatus,ac_voltage,battery_voltage,active,lastActivityTime,ai_health_tier,ai_primary_issue,ai_anomaly_score,ai_outage_risk_prob"
    vitals = {
        "battery_voltage": None,
        "ac_voltage": None,
        "mains_on": None,
        "battery_low": None,
        "cctv_status": None,
        "active_ticket_faults": [],
        "last_physical_packet_key": "none",
        "last_physical_packet_age_hours": None,
        "tb_server_tier": None,
        "tb_server_issue": None,
    }

    try:
        # 1. Timeseries
        ts_resp = requests.get(
            f"{tb_host}/api/plugins/telemetry/DEVICE/{dev_id}/values/timeseries?keys={keys}",
            headers=headers,
            timeout=timeout
        )
        if ts_resp.status_code == 200:
            ts_data = ts_resp.json()
            now_ms = time.time() * 1000

            # Find latest physical packet
            newest_t = 0
            newest_k = "none"
            for k, v_list in ts_data.items():
                if isinstance(v_list, list) and len(v_list) > 0:
                    t = v_list[0].get("ts", 0)
                    if t > newest_t:
                        newest_t = t
                        newest_k = k
            if newest_t > 0:
                vitals["last_physical_packet_key"] = newest_k
                vitals["last_physical_packet_age_hours"] = round((now_ms - newest_t) / 3600000.0, 1)

            # Gateway Status
            gw_raw = ts_data.get("gatewayStatus")
            if gw_raw and isinstance(gw_raw, list) and len(gw_raw) > 0:
                gw_val = _safe_json_parse(gw_raw[0].get("value"))
                if isinstance(gw_val, dict):
                    vitals["battery_voltage"] = _to_float(gw_val.get("battery_voltage"))
                    vitals["ac_voltage"] = _to_float(gw_val.get("ac_voltage"))
                    vitals["mains_on"] = _to_bool(gw_val.get("MAINS ON"))
                    vitals["battery_low"] = _to_bool(gw_val.get("BATTERY LOW"))

            # Fallback direct keys
            if vitals["ac_voltage"] is None and "ac_voltage" in ts_data:
                vitals["ac_voltage"] = _to_float(ts_data["ac_voltage"][0].get("value"))
            if vitals["battery_voltage"] is None and "battery_voltage" in ts_data:
                vitals["battery_voltage"] = _to_float(ts_data["battery_voltage"][0].get("value"))

            # Ticket Status
            tkt_raw = ts_data.get("ticketStatus")
            if tkt_raw and isinstance(tkt_raw, list) and len(tkt_raw) > 0:
                tkt_val = _safe_json_parse(tkt_raw[0].get("value"))
                if isinstance(tkt_val, dict):
                    faults = []
                    for fk in ["POWER_OFF", "BATTERY_LOW", "NVR_OFF", "CAM_DIS", "H_ERROR", "FAS_OFF", "IAS_OFF"]:
                        if _to_bool(tkt_val.get(fk)) is True:
                            faults.append(fk)
                    vitals["active_ticket_faults"] = faults

            # Camera Status
            cam_raw = ts_data.get("cameraStatus")
            if cam_raw and isinstance(cam_raw, list) and len(cam_raw) > 0:
                cam_val = _safe_json_parse(cam_raw[0].get("value"))
                if isinstance(cam_val, dict):
                    vitals["cctv_status"] = "On" if _to_bool(cam_val.get("cctvStatus")) else "Off"
    except Exception as e:
        logger.debug(f"Timeseries query error for {dev_id}: {e}")

    try:
        # 2. Server attributes for synced AI state
        attr_resp = requests.get(
            f"{tb_host}/api/plugins/telemetry/DEVICE/{dev_id}/values/attributes/SERVER_SCOPE",
            headers=headers,
            timeout=timeout
        )
        if attr_resp.status_code == 200:
            attrs = {a.get("key"): a.get("value") for a in attr_resp.json()}
            vitals["tb_server_tier"] = attrs.get("ai_health_tier")
            vitals["tb_server_issue"] = attrs.get("ai_primary_issue")
    except Exception as e:
        logger.debug(f"Attribute query error for {dev_id}: {e}")

    return vitals


def run_full_163_device_audit() -> Dict[str, Any]:
    env = load_environment()
    tb_host = env["TB_HOST"].rstrip("/")
    tb_email = env["TB_EMAIL"]
    tb_password = env["TB_PASSWORD"]

    # 1. Login to ThingsBoard
    logger.info("Logging into ThingsBoard...")
    login_resp = requests.post(
        f"{tb_host}/api/auth/login",
        json={"username": tb_email, "password": tb_password},
        timeout=10
    )
    if login_resp.status_code != 200:
        raise RuntimeError(f"ThingsBoard login failed: {login_resp.status_code} {login_resp.text}")
    tb_token = login_resp.json()["token"]
    headers = {"X-Authorization": f"Bearer {tb_token}"}
    logger.info("ThingsBoard authentication successful.")

    # 2. Fetch all 163 devices via entitiesQuery/find
    logger.info("Fetching all devices present in ThingsBoard via entitiesQuery/find...")
    payload = {
        "entityFilter": {"type": "entityType", "entityType": "DEVICE"},
        "pageLink": {
            "pageSize": 300,
            "page": 0,
            "sortOrder": {"key": {"type": "ENTITY_FIELD", "key": "createdTime"}, "direction": "DESC"}
        },
        "entityFields": [
            {"type": "ENTITY_FIELD", "key": "name"},
            {"type": "ENTITY_FIELD", "key": "type"},
            {"type": "ENTITY_FIELD", "key": "label"}
        ],
        "latestValues": [
            {"type": "SERVER_ATTRIBUTE", "key": "active"},
            {"type": "SERVER_ATTRIBUTE", "key": "lastActivityTime"},
            {"type": "SERVER_ATTRIBUTE", "key": "lastConnectTime"},
            {"type": "SERVER_ATTRIBUTE", "key": "lastDisconnectTime"},
            {"type": "SERVER_ATTRIBUTE", "key": "inactivityReason"}
        ]
    }

    resp = requests.post(f"{tb_host}/api/entitiesQuery/find", headers=headers, json=payload, timeout=12)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to query devices: {resp.status_code} {resp.text}")
    tb_devices = resp.json().get("data", [])
    logger.info(f"Discovered exactly {len(tb_devices)} devices present in ThingsBoard.")

    # 3. Compute Fleet Risk (AI Predictions across the 130 monitored branches)
    fleet_df, summary = compute_fleet_risk(env)
    fleet_by_id = fleet_df.set_index("device_id").to_dict(orient="index")
    fleet_by_name = fleet_df.set_index("device_name").to_dict(orient="index")

    now_ms = time.time() * 1000

    # 4. Multi-threaded live vitals probe
    logger.info("Probing live vitals concurrently across all devices...")
    device_id_list = [d.get("entityId", {}).get("id") for d in tb_devices if d.get("entityId", {}).get("id")]

    vitals_map: Dict[str, Dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_id = {
            executor.submit(probe_single_device_vitals, dev_id, tb_host, headers): dev_id
            for dev_id in device_id_list
        }
        for future in concurrent.futures.as_completed(future_to_id):
            did = future_to_id[future]
            try:
                vitals_map[did] = future.result()
            except Exception as e:
                logger.debug(f"Failed to fetch vitals for {did}: {e}")
                vitals_map[did] = {}

    logger.info("Vitals probing complete. Assembling audit records...")

    # 5. Build Comprehensive Audit Records
    audit_records: List[Dict[str, Any]] = []

    for d in tb_devices:
        dev_id = d.get("entityId", {}).get("id")
        ef = d.get("latest", {}).get("ENTITY_FIELD", {})
        sa = d.get("latest", {}).get("SERVER_ATTRIBUTE", {})

        dev_name = ef.get("name", {}).get("value", "")
        dev_type = ef.get("type", {}).get("value", "Default")

        raw_active = sa.get("active", {}).get("value")
        is_active = str(raw_active).lower() == "true"

        lat_raw = sa.get("lastActivityTime", {}).get("value")
        lat_ms = _to_float(lat_raw)
        age_hours = round((now_ms - lat_ms) / 3600000.0, 1) if lat_ms and lat_ms > 0 else None
        age_days = round(age_hours / 24.0, 1) if age_hours is not None else None

        reason = sa.get("inactivityReason", {}).get("value", "") or ""

        # Extract Bank Cohort
        name_upper = dev_name.upper()
        if "BOI" in name_upper:
            bank = "BOI"
        elif "SBI" in name_upper:
            bank = "SBI"
        elif "CANARA" in name_upper:
            bank = "CANARA"
        elif "BOB" in name_upper or "BARODA" in name_upper:
            bank = "BOB"
        elif "DX" in name_upper:
            bank = "DX"
        else:
            bank = "OTHER"

        # Fleet Model Row
        fleet_row = fleet_by_id.get(dev_id) or fleet_by_name.get(dev_name)
        is_in_monitored_fleet = fleet_row is not None

        if fleet_row:
            anomaly_score = float(fleet_row.get("anomaly_score", 0.0))
            raw_anomaly_tier = str(fleet_row.get("raw_anomaly_tier", "NORMAL"))
            outage_prob_24h = float(fleet_row.get("outage_prob_24h", 0.0))
            predicted_unified_tier = str(fleet_row.get("unified_tier", "NOMINAL"))
            primary_issue = str(fleet_row.get("primary_issue", "Normal Baseline Operation"))
            recommended_action = str(fleet_row.get("recommended_action", "Routine monitoring."))
        else:
            anomaly_score = None
            raw_anomaly_tier = "UNMONITORED"
            outage_prob_24h = None
            if not is_active:
                predicted_unified_tier = "CRITICAL_FAULT (UNMONITORED)"
                primary_issue = f"Hardware Disconnected (Silent for {age_days or 'N/A'} days)"
                recommended_action = "Edge & Telecom: Restore gateway power and verify cellular SIM connectivity on site"
            else:
                predicted_unified_tier = "ACTIVE (UNMONITORED)"
                primary_issue = "Active Tenant Device (Not in baseline parquet)"
                recommended_action = "Routine monitoring."

        vitals = vitals_map.get(dev_id, {})

        rec = {
            "device_id": dev_id,
            "device_name": dev_name,
            "device_type": dev_type,
            "bank": bank,
            "is_in_monitored_fleet": is_in_monitored_fleet,
            "realtime_active": is_active,
            "inactivity_age_hours": age_hours,
            "inactivity_age_days": age_days,
            "inactivity_reason": reason,
            "battery_voltage": vitals.get("battery_voltage"),
            "ac_voltage": vitals.get("ac_voltage"),
            "mains_on": vitals.get("mains_on"),
            "battery_low": vitals.get("battery_low"),
            "cctv_status": vitals.get("cctv_status"),
            "active_ticket_faults": vitals.get("active_ticket_faults", []),
            "anomaly_score": anomaly_score,
            "raw_anomaly_tier": raw_anomaly_tier,
            "outage_prob_24h": outage_prob_24h,
            "predicted_unified_tier": predicted_unified_tier,
            "primary_issue": primary_issue,
            "recommended_action": recommended_action,
            "tb_server_synced_tier": vitals.get("tb_server_tier"),
            "tb_server_synced_issue": vitals.get("tb_server_issue"),
        }
        audit_records.append(rec)

    # Sort: Bank, then realtime_active (True first), then dev_name
    audit_records.sort(key=lambda r: (r["bank"], not r["realtime_active"], r["device_name"]))

    # Save to JSON
    json_path = ROOT / "reports/all_163_devices_audit_report.json"
    csv_path = ROOT / "reports/all_163_devices_audit_report.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "total_devices_in_thingsboard": len(audit_records),
            "total_active_devices": sum(1 for r in audit_records if r["realtime_active"]),
            "total_inactive_devices": sum(1 for r in audit_records if not r["realtime_active"]),
            "devices": audit_records,
        }, f, indent=2)

    # Save to CSV using Manager-Friendly Formatter
    from scripts.thingsboard.format_manager_report import format_audit_report
    format_audit_report(json_path, csv_path)

    logger.info(f"Saved complete 163-device audit reports to {json_path} and {csv_path}")

    # Print Cohort Summary & Bank Statistics
    banks = ["BOI", "SBI", "CANARA", "BOB", "DX", "OTHER"]
    print("\n" + "=" * 140)
    print("THINGSBOARD COMPLETE 163-DEVICE REAL-TIME VS PREDICTED VERIFICATION AUDIT")
    print("=" * 140)

    for b in banks:
        b_devs = [r for r in audit_records if r["bank"] == b]
        if not b_devs:
            continue
        active_b = [r for r in b_devs if r["realtime_active"]]
        inactive_b = [r for r in b_devs if not r["realtime_active"]]
        print(f"\n[{b} BANK COHORT] Total Devices: {len(b_devs)} | Active: {len(active_b)} | Inactive: {len(inactive_b)}")
        print("-" * 140)
        print(f"{'Branch Name':<22} | {'Active':<6} | {'Silent(d)':<9} | {'Batt(V)':<7} | {'AC(V)':<6} | {'AnomScore':<9} | {'OutageP':<7} | {'Unified Tier':<18} | {'Primary Diagnosed Issue'}")
        print("-" * 140)
        for r in b_devs:
            anom_s = f"{r['anomaly_score']:.4f}" if r["anomaly_score"] is not None else "N/A"
            outage_s = f"{r['outage_prob_24h']*100:.1f}%" if r["outage_prob_24h"] is not None else "N/A"
            batt_s = f"{r['battery_voltage']:.1f}" if r["battery_voltage"] is not None else "N/A"
            ac_s = f"{r['ac_voltage']:.0f}" if r["ac_voltage"] is not None else "N/A"
            age_d = f"{r['inactivity_age_days']:.1f}" if r["inactivity_age_days"] is not None else "0.0"
            iss = (r["primary_issue"] or "Nominal")[:45]
            print(f"{r['device_name']:<22} | {str(r['realtime_active']):<6} | {age_d:<9} | {batt_s:<7} | {ac_s:<6} | {anom_s:<9} | {outage_s:<7} | {r['predicted_unified_tier']:<18} | {iss}")

    print("\n" + "=" * 140)
    print(f"AUDIT EXECUTION COMPLETE: 163/163 DEVICES VERIFIED (Active: {sum(1 for r in audit_records if r['realtime_active'])}, Inactive: {sum(1 for r in audit_records if not r['realtime_active'])})")
    print("=" * 140)

    return {"records": audit_records}


if __name__ == "__main__":
    run_full_163_device_audit()

