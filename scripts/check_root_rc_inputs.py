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

res = requests.get(f"{TB_HOST}/api/ruleChain/d15e00e0-b913-11ee-be46-bbf26874563f/metadata", headers=headers)
meta = res.json()

rc_nodes = [i for i, n in enumerate(meta["nodes"]) if "RuleChainInputNode" in n.get("type", "")]
print(f"Total RuleChainInputNodes in Root: {len(rc_nodes)}")
for idx in rc_nodes:
    name = meta["nodes"][idx]["name"]
    target_rc = meta["nodes"][idx].get("configuration", {}).get("ruleChainId")
    incoming = [c for c in meta.get("connections", []) if c.get("toIndex") == idx]
    inc_info = [f"{meta['nodes'][c['fromIndex']]['name']} ({c['type']})" for c in incoming]
    print(f"Node [{idx:2d}] {name} (targets {target_rc}): fed from {inc_info}")
