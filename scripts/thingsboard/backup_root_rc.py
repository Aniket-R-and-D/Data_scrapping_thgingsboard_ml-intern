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

ROOT_RC_ID = "d15e00e0-b913-11ee-be46-bbf26874563f"
res = requests.get(f"{TB_HOST}/api/ruleChain/{ROOT_RC_ID}/metadata", headers=headers)
meta = res.json()

os.makedirs("backup", exist_ok=True)
with open("backup/root_rule_chain_backup.json", "w") as f:
    json.dump(meta, f, indent=2)

print(f"Backed up Root Rule Chain ({len(meta['nodes'])} nodes, {len(meta['connections'])} connections) to backup/root_rule_chain_backup.json")
