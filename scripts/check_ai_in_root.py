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
AI_RC_ID = "b00aff60-cf9a-11f1-8a38-793373dfbb03"

res = requests.get(f"{TB_HOST}/api/ruleChain/{ROOT_RC_ID}/metadata", headers=headers)
meta = res.json()

# Check if already linked
existing_idx = None
for idx, node in enumerate(meta.get("nodes", [])):
    if node.get("type") == "org.thingsboard.rule.engine.flow.TbRuleChainInputNode":
        if node.get("configuration", {}).get("ruleChainId") == AI_RC_ID:
            existing_idx = idx
            break

if existing_idx is not None:
    print(f"AI Rule Chain is ALREADY linked at node index [{existing_idx}]")
else:
    print("AI Rule Chain is not yet linked to Root Rule Chain.")
