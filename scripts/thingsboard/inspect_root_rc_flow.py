"""scripts/thingsboard/inspect_root_rc_flow.py
Examine the Root Rule Chain to see how telemetry is saved and routed.
"""
import os, requests
from dotenv import load_dotenv

load_dotenv(".env")
TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL")
TB_PASSWORD = os.getenv("TB_PASSWORD")

res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD})
token = res.json()["token"]
headers = {"X-Authorization": f"Bearer {token}"}

ROOT_RC_ID = "d15e00e0-b913-11ee-be46-bbf26874563f"
rc_meta = requests.get(f"{TB_HOST}/api/ruleChain/{ROOT_RC_ID}/metadata", headers=headers).json()

print(f"Root Rule Chain has {len(rc_meta['nodes'])} nodes.")
for i, n in enumerate(rc_meta["nodes"]):
    print(f"Node [{i}] {n['name']} ({n['type']})")

print("\nConnections from Post Telemetry (Node 0):")
for c in rc_meta["connections"]:
    if c["fromIndex"] == 0:
        to_node = rc_meta["nodes"][c["toIndex"]]
        print(f"  --( {c['type']} )--> Node [{c['toIndex']}] {to_node['name']} ({to_node['type']})")

print("\nFirst rule node:")
first_node_id = rc_meta.get("firstRuleNodeId", {}).get("id")
for i, n in enumerate(rc_meta["nodes"]):
    if n["id"]["id"] == first_node_id:
        print(f"Node [{i}] {n['name']} ({n['type']})")

for i, n in enumerate(rc_meta["nodes"]):
    if "timeseries" in n["name"].lower() or "telemetry" in n["type"].lower():
        inbound = [c for c in rc_meta["connections"] if c["toIndex"] == i]
        print(f"Node [{i}] {n['name']} ({n['type']}) has {len(inbound)} inbound connections")
        for c in inbound:
            print(f"  <--( {c['type']} )-- Node [{c['fromIndex']}] {rc_meta['nodes'][c['fromIndex']]['name']}")
