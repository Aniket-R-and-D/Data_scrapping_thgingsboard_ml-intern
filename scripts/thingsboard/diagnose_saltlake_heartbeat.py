"""scripts/thingsboard/diagnose_saltlake_heartbeat.py
Inspect Saltlake device telemetry, heartbeat, alarms, and rule chain interactions.
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
    print(f"Connecting to ThingsBoard at {TB_HOST} as {TB_EMAIL}...")
    res = requests.post(
        f"{TB_HOST}/api/auth/login",
        json={"username": TB_EMAIL, "password": TB_PASSWORD},
        timeout=10,
    )
    if res.status_code != 200:
        print("Login failed:", res.status_code, res.text)
        return
    token = res.json()["token"]
    headers = {"X-Authorization": f"Bearer {token}"}

    # 1. Search for devices with SALTLAKE or salt lake in their name
    dev_res = requests.get(
        f"{TB_HOST}/api/tenant/devices?pageSize=100&page=0&textSearch=SALT",
        headers=headers,
        timeout=15,
    )
    devices = dev_res.json().get("data", [])
    print(f"Found {len(devices)} device(s) matching 'SALT':")

    for d in devices:
        d_id = d["id"]["id"]
        name = d["name"]
        d_type = d.get("type")
        label = d.get("label")
        print(f"\n==========================================")
        print(f"Device: {name} (ID: {d_id}, Type: {d_type}, Label: {label})")
        print(f"==========================================")

        # 2. Latest Timeseries
        ts_res = requests.get(
            f"{TB_HOST}/api/plugins/telemetry/DEVICE/{d_id}/values/timeseries",
            headers=headers,
            timeout=15,
        )
        if ts_res.status_code == 200:
            ts_data = ts_res.json()
            print(f"Total timeseries telemetry keys: {len(ts_data)}")
            
            # Check specifically for heartbeat or status or timestamp keys
            hb_keys = [k for k in ts_data.keys() if any(sub in k.lower() for sub in ["heart", "beat", "status", "active", "online", "ping", "last", "voltage", "current", "temp"])]
            print("Relevant Telemetry Keys:")
            for k in sorted(hb_keys)[:20]:
                val = ts_data[k][0]
                ts_ms = val["ts"]
                dt = datetime.datetime.fromtimestamp(ts_ms / 1000.0, datetime.timezone.utc)
                age_min = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 60.0
                print(f"  {k}: {val['value']} (Timestamp: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')} -> {age_min:.1f} min ago)")
            
            # If no hb_keys, show top 10 general keys
            if not hb_keys:
                for k in sorted(ts_data.keys())[:10]:
                    val = ts_data[k][0]
                    ts_ms = val["ts"]
                    dt = datetime.datetime.fromtimestamp(ts_ms / 1000.0, datetime.timezone.utc)
                    age_min = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 60.0
                    print(f"  {k}: {val['value']} (Timestamp: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')} -> {age_min:.1f} min ago)")
        else:
            print("Failed to fetch timeseries:", ts_res.status_code)

        # 3. Server / Client / Shared Attributes
        for scope in ["SERVER_SCOPE", "SHARED_SCOPE", "CLIENT_SCOPE"]:
            attr_res = requests.get(
                f"{TB_HOST}/api/plugins/telemetry/DEVICE/{d_id}/values/attributes/{scope}",
                headers=headers,
                timeout=15,
            )
            if attr_res.status_code == 200:
                attrs = attr_res.json()
                print(f"\nAttributes ({scope}) - {len(attrs)} items:")
                for a in attrs:
                    k = a.get("key")
                    v = a.get("value")
                    ts = a.get("lastUpdateTs")
                    dt_str = datetime.datetime.fromtimestamp(ts / 1000.0, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if ts else "N/A"
                    print(f"  {k}: {v} (Updated: {dt_str})")

        # 4. Active Alarms
        alarm_res = requests.get(
            f"{TB_HOST}/api/alarms/DEVICE/{d_id}?pageSize=10&page=0&searchStatus=ANY",
            headers=headers,
            timeout=15,
        )
        if alarm_res.status_code == 200:
            alarms = alarm_res.json().get("data", [])
            print(f"\nAlarms ({len(alarms)} recent):")
            for al in alarms:
                al_type = al.get("type")
                al_sev = al.get("severity")
                al_status = al.get("status")
                start_ts = al.get("startTs")
                start_dt = datetime.datetime.fromtimestamp(start_ts / 1000.0, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if start_ts else "N/A"
                print(f"  [{al_sev}] {al_type} - {al_status} (Started: {start_dt})")

if __name__ == "__main__":
    main()

