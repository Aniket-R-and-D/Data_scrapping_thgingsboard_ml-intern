"""scripts/thingsboard/check_other_branches_heartbeat.py
Check recent telemetry across other branches to confirm ThingsBoard telemetry ingestion is healthy.
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

res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD})
token = res.json()["token"]
headers = {"X-Authorization": f"Bearer {token}"}

devs = requests.get(f"{TB_HOST}/api/tenant/devices?pageSize=10&page=0", headers=headers).json().get("data", [])
now_utc = datetime.datetime.now(datetime.timezone.utc)
print(f"Current Time: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
print("Checking recent telemetry ingestion across other branches:")

for d in devs:
    d_id = d["id"]["id"]
    name = d["name"]
    ts_res = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{d_id}/values/timeseries", headers=headers).json()
    latest_ts = 0
    latest_k = None
    for k, v in ts_res.items():
        if k.startswith("ai_"):
            continue
        if v and v[0]["ts"] > latest_ts:
            latest_ts = v[0]["ts"]
            latest_k = k
    if latest_ts > 0:
        dt = datetime.datetime.fromtimestamp(latest_ts / 1000.0, datetime.timezone.utc)
        age = (now_utc - dt).total_seconds() / 60.0
        print(f"  Branch: {name:<25} | Latest Key: {latest_k:<18} | Time: {dt.strftime('%H:%M:%S UTC')} ({age:5.1f}m ago)")
    else:
        print(f"  Branch: {name:<25} | No recent telemetry")

