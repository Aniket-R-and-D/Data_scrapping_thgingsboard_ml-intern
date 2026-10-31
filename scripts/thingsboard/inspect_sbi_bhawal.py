"""scripts/thingsboard/inspect_sbi_bhawal.py
Deep dive into SBI BHAWAL to answer the user's specific question.
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
    res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD}, timeout=10)
    token = res.json()["token"]
    headers = {"X-Authorization": f"Bearer {token}"}

    dev_id = "49626c80-3d15-11f0-9778-a73e030dc473"

    dev_res = requests.get(f"{TB_HOST}/api/device/{dev_id}", headers=headers).json()
    print("Device Name:", dev_res.get("name"))
    print("Device Type:", dev_res.get("type"))

    attr_res = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/values/attributes/SERVER_SCOPE", headers=headers).json()
    attrs = {a["key"]: a["value"] for a in attr_res}
    print("\nKey Server Attributes:")
    for k in ["active", "inactivityReason", "inactiveSince", "lastConnectTime", "lastDisconnectTime", "heartbeat", "ai_health_tier", "ai_anomaly_score", "ai_outage_risk_prob"]:
        print(f"  {k}: {attrs.get(k)}")

    ts_res = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/values/timeseries", headers=headers).json()
    print(f"\nTotal telemetry keys: {len(ts_res)}")
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    for k in sorted(ts_res.keys()):
        val = ts_res[k][0]
        dt = datetime.datetime.fromtimestamp(val["ts"] / 1000.0, datetime.timezone.utc)
        age_days = (now_utc - dt).total_seconds() / 86400.0
        v_str = str(val["value"])[:35]
        print(f"  {k:<25} | {v_str:<35} | {dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({age_days:5.1f} days ago)")

if __name__ == "__main__":
    main()

