"""scripts/thingsboard/mcp_fleet_heartbeat_audit.py
Perform in-depth fleet-wide audit of all devices to analyze heartbeat status and diagnose non-reporting devices.
"""
from __future__ import annotations

import datetime
import os
import requests
from dotenv import load_dotenv

load_dotenv(".env")
TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL")
TB_PASSWORD = os.getenv("TB_PASSWORD")

def main():
    res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD})
    token = res.json()["token"]
    headers = {"X-Authorization": f"Bearer {token}"}

    # Fetch all devices (163 total)
    devices = []
    page = 0
    while True:
        resp = requests.get(f"{TB_HOST}/api/tenant/devices?pageSize=50&page={page}", headers=headers).json()
        data = resp.get("data", [])
        devices.extend(data)
        if not resp.get("hasNext", False):
            break
        page += 1

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    print(f"Total devices retrieved: {len(devices)}")
    print(f"Audit Timestamp: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    results = []
    
    for idx, d in enumerate(devices):
        dev_id = d["id"]["id"]
        dev_name = d["name"]
        dev_type = d.get("type", "Unknown")

        # Get latest timeseries
        ts_res = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/values/timeseries", headers=headers)
        latest_ts = 0
        latest_key = None
        latest_val = None
        has_ac_zero = False
        has_batt_zero = False
        
        if ts_res.status_code == 200:
            ts_data = ts_res.json()
            for k, vals in ts_data.items():
                if k.startswith("ai_"):
                    continue
                if vals and vals[0]["ts"] > latest_ts:
                    latest_ts = vals[0]["ts"]
                    latest_key = k
                    latest_val = vals[0]["value"]
                if k == "ac_voltage" and vals:
                    try:
                        if float(vals[0]["value"]) == 0.0:
                            has_ac_zero = True
                    except (ValueError, TypeError):
                        pass
                if k == "battery_voltage" and vals:
                    try:
                        if float(vals[0]["value"]) == 0.0:
                            has_batt_zero = True
                    except (ValueError, TypeError):
                        pass

        # Attributes
        attr_res = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/values/attributes/SERVER_SCOPE", headers=headers)
        active_attr = None
        inactivity_reason = None
        last_disconnect = None
        if attr_res.status_code == 200:
            attrs = {a["key"]: a["value"] for a in attr_res.json()}
            active_attr = attrs.get("active")
            inactivity_reason = attrs.get("inactiveReason")
            last_disconnect = attrs.get("lastDisconnectTime")

        age_hours = None
        if latest_ts > 0:
            dt = datetime.datetime.fromtimestamp(latest_ts / 1000.0, datetime.timezone.utc)
            age_hours = (now_utc - dt).total_seconds() / 3600.0
        
        results.append({
            "name": dev_name,
            "id": dev_id,
            "type": dev_type,
            "latest_ts": latest_ts,
            "latest_key": latest_key,
            "latest_val": str(latest_val)[:30],
            "age_hours": age_hours,
            "active": active_attr,
            "reason": inactivity_reason,
            "ac_zero": has_ac_zero,
            "batt_zero": has_batt_zero,
        })

    # Categorize results
    # 1. Real-time active (< 1 hour)
    active_recent = [r for r in results if r["age_hours"] is not None and r["age_hours"] <= 1.0]
    # 2. Delayed (1 to 24 hours)
    delayed_today = [r for r in results if r["age_hours"] is not None and 1.0 < r["age_hours"] <= 24.0]
    # 3. Offline / Silent (> 24 hours or never)
    offline_stale = [r for r in results if r["age_hours"] is None or r["age_hours"] > 24.0]

    print("\n=======================================================")
    print("FLEET HEARTBEAT SUMMARY")
    print("=======================================================")
    print(f"Active & Streaming Today (< 1h lag)      : {len(active_recent)}")
    print(f"Recent Outages Today (1h - 24h lag)       : {len(delayed_today)}")
    print(f"Long-term Offline / Dormant (> 24h lag)  : {len(offline_stale)}")
    print("=======================================================")

    print("\n[CATEGORY 1] ACTIVE & STREAMING TELEMETRY RIGHT NOW (< 1 hr):")
    for r in sorted(active_recent, key=lambda x: x["age_hours"]):
        print(f"  {r['name']:<25} | Lag: {r['age_hours']*60:5.1f} min | Key: {r['latest_key']:<15} | Active: {r['active']}")

    print("\n[CATEGORY 2] DEVICES WHOSE HEARTBEAT STOPPED TODAY (1h to 24h):")
    for r in sorted(delayed_today, key=lambda x: x["age_hours"]):
        diag = []
        if r["ac_zero"]: diag.append("AC_MAINS_0V")
        if r["batt_zero"]: diag.append("BATTERY_0V")
        if r["reason"]: diag.append(r["reason"])
        diag_str = ", ".join(diag) if diag else "No power/status error captured"
        print(f"  {r['name']:<25} | Stopped: {r['age_hours']:4.1f}h ago | Key: {r['latest_key']:<15} | Diag: {diag_str}")

    print("\n[CATEGORY 3] SAMPLE OF LONG-TERM DORMANT / HISTORICALLY OFFLINE DEVICES (> 24h):")
    for r in sorted(offline_stale, key=lambda x: (x["age_hours"] if x["age_hours"] is not None else 999999))[:15]:
        age_str = f"{r['age_hours']/24.0:5.1f} days" if r["age_hours"] else "NEVER"
        diag = r["reason"] or "Offline"
        print(f"  {r['name']:<25} | Last seen: {age_str:<10} | Type: {r['type']:<20} | Status: {diag}")

if __name__ == "__main__":
    main()

