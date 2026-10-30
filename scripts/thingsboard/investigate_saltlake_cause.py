"""scripts/thingsboard/investigate_saltlake_cause.py
Comprehensive investigation of Saltlake branch heartbeat and the AI rule chain.
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

    # 1. Inspect AI Rule Chain (b00aff60-cf9a-11f1-8a38-793373dfbb03)
    ai_rc_id = "b00aff60-cf9a-11f1-8a38-793373dfbb03"
    ai_meta = requests.get(f"{TB_HOST}/api/ruleChain/{ai_rc_id}/metadata", headers=headers).json()
    print("=== AI Rule Chain Structure ===")
    print(f"Total nodes in AI Rule Chain: {len(ai_meta.get('nodes', []))}")
    for i, n in enumerate(ai_meta.get("nodes", [])):
        print(f"  Node [{i}] {n.get('name')} ({n.get('type')})")
    print("\nConnections in AI Rule Chain:")
    for c in ai_meta.get("connections", []):
        f = ai_meta["nodes"][c["fromIndex"]]["name"]
        t = ai_meta["nodes"][c["toIndex"]]["name"]
        print(f"  [{f}] --({c['type']})--> [{t}]")

    # 2. Check if AI Rule Chain outputs back to anything or deletes anything
    # Rule chains that only dispatch alerts (Slack, Email) cannot modify or delete incoming device telemetry.
    
    # 3. Check BOI-SALTLAKE device telemetry history for today
    salt_id = "6287e400-a823-11f0-b150-2710a8915e1d"
    
    # Fetch latest values of all timeseries keys
    ts_latest = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{salt_id}/values/timeseries", headers=headers).json()
    print("\n=== BOI-SALTLAKE Telemetry Timestamps ===")
    
    latest_times = []
    for k, v in ts_latest.items():
        if v and len(v) > 0:
            latest_times.append((k, v[0]["ts"], v[0]["value"]))
    
    latest_times.sort(key=lambda x: x[1], reverse=True)
    
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    print(f"Current Time: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("\nTop 15 Most Recent Telemetry Keys for BOI-SALTLAKE:")
    for k, ts, val in latest_times[:15]:
        dt = datetime.datetime.fromtimestamp(ts / 1000.0, datetime.timezone.utc)
        age = (now_utc - dt).total_seconds() / 60.0
        val_str = str(val)[:50]
        print(f"  {dt.strftime('%H:%M:%S UTC')} ({age:5.1f}m ago) | {k} = {val_str}")

    # Check oldest/most lagging keys
    print("\nKey Hardware & Power Telemetry:")
    for target in ["ac_voltage", "battery_voltage", "ups_status", "heartbeat", "active", "gwStatus", "gwHealth", "DahuaNVR_Heartbeat", "Hikvision_NVR_Heartbeat"]:
        if target in ts_latest:
            v = ts_latest[target][0]
            dt = datetime.datetime.fromtimestamp(v["ts"] / 1000.0, datetime.timezone.utc)
            age = (now_utc - dt).total_seconds() / 60.0
            print(f"  {target}: {v['value']} at {dt.strftime('%H:%M:%S UTC')} ({age:.1f}m ago)")

    # 4. Check device attributes for inactivity/lastConnect
    attrs_server = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{salt_id}/values/attributes/SERVER_SCOPE", headers=headers).json()
    attr_map = {a["key"]: a["value"] for a in attrs_server}
    print("\n=== Device Activity & Connection Attributes ===")
    print(f"  active: {attr_map.get('active')}")
    print(f"  inactivityAlarmTime: {attr_map.get('inactivityAlarmTime')}")
    if attr_map.get("inactivityAlarmTime"):
        dt_inact = datetime.datetime.fromtimestamp(attr_map["inactivityAlarmTime"] / 1000.0, datetime.timezone.utc)
        print(f"    Inactivity Alarm Time: {dt_inact.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  lastConnectTime: {attr_map.get('lastConnectTime')}")
    if attr_map.get("lastConnectTime"):
        dt_conn = datetime.datetime.fromtimestamp(attr_map["lastConnectTime"] / 1000.0, datetime.timezone.utc)
        print(f"    Last Connect Time: {dt_conn.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  lastDisconnectTime: {attr_map.get('lastDisconnectTime')}")
    if attr_map.get("lastDisconnectTime"):
        dt_disc = datetime.datetime.fromtimestamp(attr_map["lastDisconnectTime"] / 1000.0, datetime.timezone.utc)
        print(f"    Last Disconnect Time: {dt_disc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  inactiveSince: {attr_map.get('inactiveSince')}")
    print(f"  inactiveReason: {attr_map.get('inactiveReason')}")

if __name__ == "__main__":
    main()

