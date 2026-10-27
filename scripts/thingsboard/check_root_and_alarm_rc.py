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

def check_rc(rc_id, name):
    res = requests.get(f"{TB_HOST}/api/ruleChain/{rc_id}/metadata", headers=headers)
    meta = res.json()
    print(f"\n=== {name} ({rc_id}) ===")
    print("Rule Chain Connections:")
    for rcc in meta.get("ruleChainConnections", []) or []:
        target = rcc.get("targetRuleChainId", {}).get("id")
        from_idx = rcc.get("fromIndex")
        node_name = meta["nodes"][from_idx]["name"] if from_idx < len(meta["nodes"]) else "?"
        print(f"  From Node [{from_idx}: {node_name}] -> Target Rule Chain [{target}] (type: {rcc.get('type')})")
    
    # Also check if any node is a Rule Chain Output or Rule Chain Input node
    print("Nodes summary:")
    for idx, node in enumerate(meta.get("nodes", [])):
        ntype = node.get("type", "")
        if "RuleChain" in ntype or "Alarm" in ntype or "alarm" in node.get("name", "").lower():
            print(f"  Node [{idx}]: {node.get('name')} ({ntype})")

check_rc("d15e00e0-b913-11ee-be46-bbf26874563f", "Root Rule Chain(Dexter)")
check_rc("ab630950-482c-11ef-bec6-6743a7e32eb4", "AlarmCreatingNew")
