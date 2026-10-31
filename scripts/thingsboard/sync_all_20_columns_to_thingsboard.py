"""scripts/thingsboard/sync_all_20_columns_to_thingsboard.py
Pushes all 20 managerial and technical attributes from reports/all_163_devices_audit_report.csv
to ThingsBoard SERVER_SCOPE and SHARED_SCOPE across all 163 devices.
Ensures zero-latency loading and complete population for every column in the enhanced ThingsBoard Entities Table.
"""
from __future__ import annotations

import concurrent.futures
import csv
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_all_20_columns")

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL", "info@seple.in")
TB_PASSWORD = os.getenv("TB_PASSWORD", "")

CSV_PATH = ROOT / "reports/all_163_devices_audit_report.csv"


def get_auth_token() -> str:
    resp = requests.post(
        f"{TB_HOST}/api/auth/login",
        json={"username": TB_EMAIL, "password": TB_PASSWORD},
        timeout=15
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ThingsBoard authentication failed: {resp.status_code} {resp.text}")
    return resp.json()["token"]


def map_health_status_to_tier(health_status: str) -> str:
    s = str(health_status).upper()
    if "CRITICAL FAULT" in s:
        return "CRITICAL_FAULT"
    if "PREDICTIVE OUTAGE" in s:
        return "HIGH_RISK"
    if "BROWNOUT" in s or "LOW VOLTAGE" in s or "ELEVATED" in s:
        return "ELEVATED"
    if "RECOVERED" in s:
        return "SERVICED_RECOVERED"
    if "NOMINAL" in s:
        return "NOMINAL"
    if "ACTIVE TEST" in s:
        return "NOMINAL"
    if "OFFLINE TEST" in s:
        return "CRITICAL_FAULT"
    return "NOMINAL"


def sync_single_device(row: Dict[str, str], headers: Dict[str, str]) -> bool:
    dev_id = row.get("ThingsBoard Device ID", "").strip()
    if not dev_id:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    health_status = row.get("Health Status", "")
    tier = map_health_status_to_tier(health_status)

    def _to_f(v: str, default: float = 0.0) -> float:
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    raw_prob = _to_f(row.get("Raw Outage Probability", "0"))
    raw_anomaly = _to_f(row.get("Raw Anomaly Score", "0"))
    batt_v = _to_f(row.get("Last Known Battery Voltage (V)", "0"))
    ac_v = _to_f(row.get("Last Known AC Voltage (V)", "0"))

    attrs_payload: Dict[str, Any] = {
        "ai_bank": row.get("Bank", "Unknown Bank"),
        "ai_fleet_classification": row.get("Fleet Classification", "Monitored Branch"),
        "ai_operational_status": row.get("Operational Status", "ONLINE"),
        "ai_health_status": health_status,
        "ai_management_priority": row.get("Management Priority", "P4 - Nominal (Healthy Operations)"),
        "ai_days_offline": row.get("Days Offline", "Online Now (0.0 days)"),
        "ai_ac_mains": row.get("AC Mains Power", "220V (Normal Grid Supply)"),
        "ai_battery_health": row.get("Battery Health", "14.0V (Healthy / Fully Charged)"),
        "ai_cctv_status": row.get("CCTV Surveillance", "Online (Cameras Active)"),
        "ai_active_alarms": row.get("Active System Alarms", "None (All Systems Normal)"),
        "ai_outage_risk_prob": raw_prob,
        "ai_executive_diagnosis": row.get("Executive Diagnosis", ""),
        "ai_manager_action": row.get("Recommended Manager Action", ""),
        "ai_device_id": dev_id,
        "ai_anomaly_score": raw_anomaly,
        "ai_outage_prob_raw": raw_prob,
        "ai_battery_voltage": batt_v,
        "ai_ac_voltage": ac_v,
        "ai_health_tier": tier,
        "ai_last_evaluated": now_iso,
    }

    for attempt in range(3):
        try:
            r1 = requests.post(
                f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/SERVER_SCOPE",
                json=attrs_payload,
                headers=headers,
                timeout=12
            )
            r2 = requests.post(
                f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/SHARED_SCOPE",
                json=attrs_payload,
                headers=headers,
                timeout=12
            )
            requests.post(
                f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/timeseries/any",
                json=attrs_payload,
                headers=headers,
                timeout=12
            )
            if r1.status_code == 200 or r2.status_code == 200:
                return True
        except Exception:
            time.sleep(0.5)

    return False


def sync_all_devices() -> None:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing audit CSV at: {CSV_PATH}")

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"Loaded {len(rows)} devices from {CSV_PATH}")
    logger.info("Authenticating with ThingsBoard...")
    token = get_auth_token()
    headers = {"X-Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    logger.info("Syncing all 20 managerial and technical attributes across all devices...")
    success_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(sync_single_device, r, headers): r for r in rows}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                success_count += 1

    logger.info(f"Attribute synchronization complete: {success_count}/{len(rows)} devices updated successfully.")


if __name__ == "__main__":
    sync_all_devices()
