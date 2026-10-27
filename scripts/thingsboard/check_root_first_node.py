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

res = requests.get(f"{TB_HOST}/api/ruleChain/d15e00e0-b913-11ee-be46-bbf26874563f/metadata", headers=headers)
meta = res.json()

def print_conns(from_idx):
    conns = [c for c in meta.get("connections", []) if c.get("fromIndex") == from_idx]
    node_name = meta["nodes"][from_idx]["name"]
    print(f"\nConnections from [{from_idx}] {node_name} ({len(conns)}):")
    for c in conns:
        target_name = meta["nodes"][c["toIndex"]]["name"]
        print(f"  --[{c.get('type')}]--> [{c.get('toIndex')}]: {target_name}")

print_conns(6)
print_conns(0)
print_conns(1)
