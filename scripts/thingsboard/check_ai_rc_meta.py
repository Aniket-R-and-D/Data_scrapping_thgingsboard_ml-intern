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

AI_RC_ID = "b00aff60-cf9a-11f1-8a38-793373dfbb03"
res = requests.get(f"{TB_HOST}/api/ruleChain/{AI_RC_ID}/metadata", headers=headers)
meta = res.json()

print(f"Rule Chain {AI_RC_ID} Metadata:")
print(f"First Node Index: {meta.get('firstNodeIndex')}")
print(f"Nodes ({len(meta.get('nodes', []))}):")
for idx, node in enumerate(meta.get("nodes", [])):
    print(f"  [{idx}] {node.get('name')} ({node.get('type')})")

print(f"Connections ({len(meta.get('connections', []))}):")
for conn in meta.get("connections", []):
    print(f"  Node {conn.get('fromIndex')} -> Node {conn.get('toIndex')} ({conn.get('type')})")
