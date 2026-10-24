import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(".env")
TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL")
TB_PASSWORD = os.getenv("TB_PASSWORD")

res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD})
token = res.json()["token"]
headers = {"X-Authorization": f"Bearer {token}"}

devices = [
    ("BOB-AIRPORT", "e0622090-7694-11f0-91d2-cd9d6fd53b07"),
    ("BOI-ADITYAPUR", "ad7453e0-a8be-11f0-91df-7ffa16af2ee9"),
    ("BOI-ALIPURDUAR", "b60f6ba0-ab2c-11f0-91df-7ffa16af2ee9"),
    ("BOI-MALDATOWN", "01c61bb0-ab4c-11f0-91df-7ffa16af2ee9")
]

for name, dev_id in devices:
    keys = "ticketStatus,alarm,gatewayStatus,cameraStatus,active,lastActivityTime,branch_id,cctvLastHour,fasLastHour,basLastHour"
    r = requests.get(f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/values/timeseries?keys={keys}", headers=headers)
    print(f"\n=======================================================")
    print(f"Device: {name} ({dev_id})")
    print(f"=======================================================")
    if r.status_code == 200:
        data = r.json()
        for k, v in data.items():
            if v and len(v) > 0:
                print(f"  [{k}] (ts: {v[0].get('ts')}): {v[0].get('value')}")
            else:
                print(f"  [{k}]: <empty>")
    else:
        print(f"  Failed: {r.status_code}")
