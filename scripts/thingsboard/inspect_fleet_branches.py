"""scripts/thingsboard/inspect_fleet_branches.py
Inspect the status of specific bank branches across ThingsBoard and local models.
"""
import os
import requests
import time
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env")
TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
login = requests.post(
    f"{TB_HOST}/api/auth/login",
    json={"username": os.getenv("TB_EMAIL"), "password": os.getenv("TB_PASSWORD")},
    timeout=8
)
token = login.json()["token"]
headers = {"X-Authorization": f"Bearer {token}"}

payload = {
    "entityFilter": {"type": "entityType", "entityType": "DEVICE"},
    "pageLink": {
        "pageSize": 250,
        "page": 0,
        "sortOrder": {"key": {"type": "ENTITY_FIELD", "key": "createdTime"}, "direction": "DESC"}
    },
    "entityFields": [{"type": "ENTITY_FIELD", "key": "name"}],
    "latestValues": [
        {"type": "SERVER_ATTRIBUTE", "key": "active"},
        {"type": "SERVER_ATTRIBUTE", "key": "lastActivityTime"}
    ]
}

res = requests.post(f"{TB_HOST}/api/entitiesQuery/find", headers=headers, json=payload, timeout=10)
data = res.json().get("data", [])
now_ms = time.time() * 1000

tb_map = {}
for d in data:
    name = d.get("latest", {}).get("ENTITY_FIELD", {}).get("name", {}).get("value")
    sa = d.get("latest", {}).get("SERVER_ATTRIBUTE", {})
    act = sa.get("active", {}).get("value") == "true"
    lat = sa.get("lastActivityTime", {}).get("value")
    age_h = (now_ms - float(lat)) / 3600000.0 if lat else 999999
    tb_map[name] = (act, age_h)

scored = pd.read_parquet("models/latest_fleet_scored.parquet").set_index("device_name")

branches_to_check = [
    "CANARA-BURNPUR", "DX7", "BOI-MALLAGURI", "BOI-CUTTACK", "BOI-SINGH-MORE",
    "BOI-PANASPADA", "BOI-LALPUR", "BOI-HARDA-ROAD", "BOI-HGBAD", "BOI-BHUBANESHWAR",
    "BOI-SONAHATU", "BOI-AIZWAL", "BOI-RAIGANJ", "BOI-PURI", "BOI-SITHIO",
    "BOI-KALYANI", "BOI-BAGULA", "BOI-COOCHBEHAR", "BOI-BERO", "BOI-MALDATOWN",
    "BOI-R-BAZAR", "SBI BHAWAL", "CANARA-BARUIPUR", "CANARA-KAMARHATI", "CANARA-CHETLA", "CANARA-HALDI2"
]

print(f"{'Branch Name':<20} | {'TB Active':<9} | {'TB Age(h)':<10} | {'Anom Score':<10} | {'Anom Tier':<15}")
print("-" * 75)
for b in branches_to_check:
    tb_info = tb_map.get(b, ("NOT_IN_TB", 999999))
    sc = scored.loc[b] if b in scored.index else None
    score_str = f"{sc['anomaly_score']:.4f}" if sc is not None else "N/A"
    tier_str = str(sc["anomaly_tier"]) if sc is not None else "N/A"
    print(f"{b:<20} | {str(tb_info[0]):<9} | {tb_info[1]:10.1f} | {score_str:<10} | {tier_str:<15}")

