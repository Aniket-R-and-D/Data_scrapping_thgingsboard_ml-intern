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

res = requests.get(f"{TB_HOST}/api/ruleChain/ab630950-482c-11ef-bec6-6743a7e32eb4/metadata", headers=headers)
meta = res.json()

print("AlarmCreatingNew First Node Index:", meta.get("firstNodeIndex"))
print("Total Nodes:", len(meta.get("nodes", [])))
print("Total Connections:", len(meta.get("connections", [])))

# Let's inspect createAlarm node(s) and their outgoing connections
for idx, node in enumerate(meta.get("nodes", [])):
    if node.get("type") == "org.thingsboard.rule.engine.action.TbCreateAlarmNode":
        print(f"\nCreate Alarm Node [{idx}]: {node.get('name')}")
        # Outgoing connections from this node:
        out_conns = [c for c in meta.get("connections", []) if c.get("fromIndex") == idx]
        for c in out_conns:
            target_node = meta["nodes"][c["toIndex"]]["name"] if c["toIndex"] < len(meta["nodes"]) else "?"
            print(f"  -> ({c.get('type')}) -> Node [{c.get('toIndex')}]: {target_node}")
