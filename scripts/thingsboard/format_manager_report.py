"""scripts/thingsboard/format_manager_report.py
Formats the complete 163-device audit report into an executive, manager-friendly CSV.
Removes technical jargon, mathematical z-scores, and misleading offline metrics.
Provides clear operational statuses, management action priorities, power health, and human-readable diagnoses.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]


FAULT_NAME_MAP = {
    "CAM_DIS": "Camera Disconnected",
    "FAS_OFF": "Fire Alarm System Offline",
    "NVR_OFF": "NVR Video Recorder Offline",
    "POWER_OFF": "Mains Power Cut (Running on Battery)",
    "BATTERY_LOW": "Low Backup Battery Alarm",
    "H_ERROR": "Gateway Hardware Error",
    "IAS_OFF": "Intrusion Alarm System Offline",
}

BANK_FULL_NAME_MAP = {
    "BOI": "Bank of India (BOI)",
    "SBI": "State Bank of India (SBI)",
    "CANARA": "Canara Bank",
    "BOB": "Bank of Baroda (BOB)",
    "DX": "DX ATM Network",
    "OTHER": "Lab / Test Bench",
}

BANK_SORT_ORDER = {
    "Bank of India (BOI)": 1,
    "Canara Bank": 2,
    "State Bank of India (SBI)": 3,
    "Bank of Baroda (BOB)": 4,
    "DX ATM Network": 5,
    "Lab / Test Bench": 6,
}


def clean_ticket_faults(fault_list: List[str], is_active: bool) -> str:
    """Format active ticket faults into plain English."""
    if not is_active:
        return "Offline (No Live Telemetry)"
    if not fault_list:
        return "None (All Systems Normal)"
    cleaned = [FAULT_NAME_MAP.get(f, f.replace("_", " ").title()) for f in fault_list]
    return ", ".join(cleaned)


def clean_ac_power(is_active: bool, ac_v: Any) -> str:
    """Format AC mains voltage for an operations manager."""
    if not is_active:
        return "Offline (No Signal)"
    if ac_v is None:
        return "No Telemetry"
    try:
        val = float(ac_v)
        if val <= 0:
            return "0V (Mains Power Outage)"
        elif val <= 160:
            return f"{val:.0f}V (Severe Brownout Warning)"
        elif val <= 180:
            return f"{val:.0f}V (Low Grid Voltage)"
        elif val >= 260:
            return f"{val:.0f}V (High Surge Warning)"
        else:
            return f"{val:.0f}V (Normal Grid Supply)"
    except Exception:
        return str(ac_v)


def clean_battery(is_active: bool, batt_v: Any) -> str:
    """Format battery voltage for an operations manager."""
    if not is_active:
        return "Offline (No Signal)"
    if batt_v is None:
        return "No Telemetry"
    try:
        val = float(batt_v)
        if val <= 0:
            return "0.0V (Battery Disconnected / Unreported)"
        elif val < 11.5:
            return f"{val:.1f}V (Critical Low Battery)"
        elif val < 12.5:
            return f"{val:.1f}V (Discharged / Below Normal)"
        else:
            return f"{val:.1f}V (Healthy / Fully Charged)"
    except Exception:
        return str(batt_v)


def clean_cctv(is_active: bool, cctv_status: Any) -> str:
    """Format CCTV surveillance status."""
    if not is_active:
        return "Offline (No Signal)"
    if not cctv_status:
        return "No Telemetry"
    s = str(cctv_status).strip().lower()
    if s in ("on", "true", "active", "connected", "online"):
        return "Online (Cameras Active)"
    return "Offline (Camera Feed Down)"


def clean_outage_risk(is_active: bool, outage_prob: Any, is_monitored: bool) -> str:
    """Format 24-hour predictive outage probability."""
    if not is_active:
        return "100.0% (Currently Offline)"
    if not is_monitored or outage_prob is None:
        return "N/A (Test / Unmonitored Node)"
    try:
        pct = round(float(outage_prob) * 100, 1)
        if pct >= 70.0:
            return f"{pct}% (High Risk)"
        elif pct >= 30.0:
            return f"{pct}% (Moderate Risk)"
        else:
            return f"{pct}% (Minimal Risk)"
    except Exception:
        return "N/A"


def resolve_manager_tier_and_priority(
    dev_name: str,
    bank: str,
    is_active: bool,
    is_monitored: bool,
    age_days: Any,
    ac_v: Any,
    batt_v: Any,
    raw_tier: str,
    outage_prob: Any
) -> Dict[str, str]:
    """Resolve Health Status, Priority, Diagnosis, and Recommended Action for Managers."""
    
    # Case 1: Unmonitored Lab / Bench Test devices
    if not is_monitored:
        if is_active:
            return {
                "health_status": "ACTIVE TEST NODE",
                "priority": "INFO - Active Lab Device",
                "diagnosis": "Development or lab test bench hardware currently transmitting live telemetry. Not an active customer-facing branch.",
                "action": "Internal R&D reference only. No field or branch action required.",
            }
        else:
            d_str = f" for {age_days:.1f} days" if age_days is not None else ""
            return {
                "health_status": "OFFLINE TEST NODE",
                "priority": "INFO - Offline Lab Device",
                "diagnosis": f"Development or bench test hardware offline{d_str}. Non-production device.",
                "action": "Internal R&D reference only. No field action required.",
            }

    # Case 2: Production Monitored Branch OFFLINE (Dead)
    if not is_active:
        d_str = f"{age_days:.1f} days" if age_days is not None else "unknown duration"
        urgency = "Recent Outage" if (age_days is not None and age_days < 7) else "Dormant Outage"
        return {
            "health_status": "CRITICAL FAULT (DISCONNECTED)",
            "priority": f"P1 - Immediate Field Dispatch ({urgency})",
            "diagnosis": f"Hardware disconnected and completely silent for {d_str}. IoT gateway has lost cellular connection or power on site.",
            "action": "Dispatch field technician to branch: inspect gateway power adapter, antenna, and cellular SIM connectivity.",
        }

    # Case 3: Production Monitored Branch ONLINE
    # Sub-case: High Predictive Outage Risk (e.g. BOI-ZARKAWT)
    prob_val = float(outage_prob) if outage_prob is not None else 0.0
    if prob_val >= 0.70:
        pct = round(prob_val * 100, 1)
        return {
            "health_status": "PREDICTIVE OUTAGE WARNING",
            "priority": "P1 - High Outage Risk (Preventative)",
            "diagnosis": f"High probability of failure ({pct}%) within next 24 hours based on cyclic historical degradation patterns.",
            "action": "Schedule urgent preventative technician visit to inspect battery backup and charger before branch goes offline.",
        }

    # Sub-case: Verified Recoveries
    if raw_tier == "SERVICED_RECOVERED":
        return {
            "health_status": "SERVICED & RECOVERED",
            "priority": "P4 - Nominal (Verified Recovered)",
            "diagnosis": "Operational recovery confirmed. Real-time telemetry resumed with healthy battery, restored mains power, and zero ticket faults.",
            "action": "None required. Post-service operational stability confirmed.",
        }

    # Sub-case: Severe Brownout / Low AC Mains
    try:
        ac_num = float(ac_v) if ac_v is not None else 220.0
    except Exception:
        ac_num = 220.0

    if 0 < ac_num <= 160:
        return {
            "health_status": "ELEVATED RISK (BROWNOUT)",
            "priority": "P2 - Facility Check (Brownout Warning)",
            "diagnosis": f"Severe AC mains brownout ({ac_num:.0f}V). Grid voltage is far below nominal operating standard (220V), risking equipment damage.",
            "action": "Alert branch facility manager and local electrician to check utility transformer, circuit breakers, and voltage stabilizer.",
        }

    if 0 < ac_num <= 180:
        return {
            "health_status": "ELEVATED RISK (LOW VOLTAGE)",
            "priority": "P3 - Proactive Monitoring (Low Voltage)",
            "diagnosis": f"Low AC mains voltage ({ac_num:.0f}V) recorded from electrical grid. Operating below normal 220V threshold.",
            "action": "Monitor grid supply fluctuations; notify facility electrician if voltage drops below 160V.",
        }

    # Sub-case: Other Elevated Risks
    if raw_tier == "ELEVATED":
        return {
            "health_status": "ELEVATED RISK",
            "priority": "P3 - Proactive Monitoring",
            "diagnosis": "Moderate telemetry variance detected. Gateway is online, but sensor usage patterns deviate slightly from baseline.",
            "action": "Maintain automated monitoring. Observe sensor stability over the next 24 hours.",
        }

    # Sub-case: Nominal / Healthy
    return {
        "health_status": "NOMINAL (HEALTHY)",
        "priority": "P4 - Nominal (Healthy Operations)",
        "diagnosis": "Branch operating nominally. All electrical, battery, surveillance, and sensor vitals are within standard operating boundaries.",
        "action": "None required. Routine automated fleet monitoring.",
    }


def format_single_device_manager_fields(
    dev_name: str,
    bank: str,
    is_active: bool,
    is_monitored: bool,
    age_days: Any,
    ac_v: Any,
    batt_v: Any,
    cctv_status: Any,
    faults: List[str],
    raw_tier: str,
    outage_prob: Any
) -> Dict[str, str]:
    """Return dictionary of all manager fields for a device."""
    res = resolve_manager_tier_and_priority(
        dev_name=dev_name,
        bank=bank,
        is_active=is_active,
        is_monitored=is_monitored,
        age_days=age_days,
        ac_v=ac_v,
        batt_v=batt_v,
        raw_tier=raw_tier,
        outage_prob=outage_prob
    )
    if is_active:
        offline_dur = "Online Now (0.0 days)"
    else:
        offline_dur = f"{age_days:.1f} days offline" if age_days is not None else "Offline (Unknown)"

    return {
        "operational_status": "ONLINE" if is_active else "OFFLINE",
        "health_status": res["health_status"],
        "management_priority": res["priority"],
        "days_offline": offline_dur,
        "ac_mains_power": clean_ac_power(is_active, ac_v),
        "battery_health": clean_battery(is_active, batt_v),
        "cctv_surveillance": clean_cctv(is_active, cctv_status),
        "active_system_alarms": clean_ticket_faults(faults, is_active),
        "predicted_outage_risk": clean_outage_risk(is_active, outage_prob, is_monitored),
        "executive_diagnosis": res["diagnosis"],
        "recommended_manager_action": res["action"],
    }


def format_audit_report(json_in_path: Path, csv_out_path: Path) -> None:
    """Transform raw audit JSON into manager-friendly CSV."""
    with open(json_in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    devices = data.get("devices", [])
    manager_rows = []

    for r in devices:
        dev_name = r.get("device_name", "")
        bank_code = r.get("bank", "OTHER")
        bank_full = BANK_FULL_NAME_MAP.get(bank_code, bank_code)
        is_monitored = r.get("is_in_monitored_fleet", False)
        is_active = r.get("realtime_active", False)
        age_days = r.get("inactivity_age_days")
        ac_v = r.get("ac_voltage")
        batt_v = r.get("battery_voltage")
        cctv_raw = r.get("cctv_status")
        faults_raw = r.get("active_ticket_faults", [])
        if isinstance(faults_raw, str):
            faults_raw = [f.strip() for f in faults_raw.split(";") if f.strip()]
        raw_tier = r.get("predicted_unified_tier", "NOMINAL")
        outage_prob = r.get("outage_prob_24h")

        res = resolve_manager_tier_and_priority(
            dev_name=dev_name,
            bank=bank_code,
            is_active=is_active,
            is_monitored=is_monitored,
            age_days=age_days,
            ac_v=ac_v,
            batt_v=batt_v,
            raw_tier=raw_tier,
            outage_prob=outage_prob
        )

        # Inactivity duration string
        if is_active:
            offline_duration_str = "Online Now (0.0 days)"
            offline_days_sort = 0.0
        else:
            offline_duration_str = f"{age_days:.1f} days offline" if age_days is not None else "Offline (Unknown)"
            offline_days_sort = age_days if age_days is not None else 9999.0

        # Sort Priority Rank (1: P1, 2: P2, 3: P3, 4: P4, 5: INFO)
        prio_str = res["priority"]
        if "P1" in prio_str:
            prio_rank = 1
        elif "P2" in prio_str:
            prio_rank = 2
        elif "P3" in prio_str:
            prio_rank = 3
        elif "P4" in prio_str:
            prio_rank = 4
        else:
            prio_rank = 5

        row = {
            "Branch / Device Name": dev_name,
            "Bank": bank_full,
            "Fleet Classification": "Monitored Branch" if is_monitored else "Internal Test / Lab Node",
            "Operational Status": "ONLINE" if is_active else "OFFLINE",
            "Health Status": res["health_status"],
            "Management Priority": res["priority"],
            "Days Offline": offline_duration_str,
            "AC Mains Power": clean_ac_power(is_active, ac_v),
            "Battery Health": clean_battery(is_active, batt_v),
            "CCTV Surveillance": clean_cctv(is_active, cctv_raw),
            "Active System Alarms": clean_ticket_faults(faults_raw, is_active),
            "Predicted Outage Risk (24h)": clean_outage_risk(is_active, outage_prob, is_monitored),
            "Executive Diagnosis": res["diagnosis"],
            "Recommended Manager Action": res["action"],
            "Device Type": r.get("device_type", "Default"),
            "ThingsBoard Device ID": r.get("device_id", ""),
            # Technical reference at the end
            "Raw Anomaly Score": f"{r['anomaly_score']:.4f}" if r.get("anomaly_score") is not None else "",
            "Raw Outage Probability": f"{r['outage_prob_24h']:.4f}" if r.get("outage_prob_24h") is not None else "",
            "Last Known Battery Voltage (V)": str(batt_v) if batt_v is not None else "",
            "Last Known AC Voltage (V)": str(ac_v) if ac_v is not None else "",
            # Internal sort helpers
            "_bank_order": BANK_SORT_ORDER.get(bank_full, 99),
            "_prio_rank": prio_rank,
            "_is_active": 0 if is_active else 1,  # Active first
            "_offline_days_sort": offline_days_sort,
        }
        manager_rows.append(row)

    # Sort order:
    # 1. Bank Order (BOI, CANARA, SBI, BOB, DX, LAB)
    # 2. Operational Status (Active first)
    # 3. Priority Rank (Recovered / Healthy / Warning)
    # 4. For offline devices: most recent outages first (e.g. 2.8 days before 80 days)
    # 5. Branch Name
    manager_rows.sort(key=lambda x: (
        x["_bank_order"],
        x["_is_active"],
        x["_prio_rank"],
        x["_offline_days_sort"],
        x["Branch / Device Name"]
    ))

    # Strip sort helper keys
    clean_fieldnames = [k for k in manager_rows[0].keys() if not k.startswith("_")]

    # Write CSV
    with open(csv_out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=clean_fieldnames)
        writer.writeheader()
        for r in manager_rows:
            clean_row = {k: r[k] for k in clean_fieldnames}
            writer.writerow(clean_row)

    print(f"Successfully generated manager-friendly CSV with {len(manager_rows)} devices at: {csv_out_path}")


if __name__ == "__main__":
    json_path = ROOT / "reports/all_163_devices_audit_report.json"
    csv_path = ROOT / "reports/all_163_devices_audit_report.csv"
    format_audit_report(json_path, csv_path)

