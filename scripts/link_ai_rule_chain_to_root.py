import os
import json
import logging
import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("link_ai_rule_chain")

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

# Verify if already linked
for idx, node in enumerate(meta.get("nodes", [])):
    if node.get("type") == "org.thingsboard.rule.engine.flow.TbRuleChainInputNode":
        if node.get("configuration", {}).get("ruleChainId") == AI_RC_ID:
            logger.info(f"Already linked at node index [{idx}] ({node.get('name')}). Exiting.")
            exit(0)

# Create input node pointing to AI Rule Chain
new_node_index = len(meta["nodes"])
input_node = {
    "type": "org.thingsboard.rule.engine.flow.TbRuleChainInputNode",
    "name": "AI Outage & Anomaly Operations Dispatch",
    "debugSettings": {"failuresEnabled": True, "allEnabled": False},
    "singletonMode": False,
    "configurationVersion": 1,
    "configuration": {
        "forwardMsgToDefaultRuleChain": False,
        "ruleChainId": AI_RC_ID
    },
    "additionalInfo": {
        "description": "Forward telemetry & attribute updates to the dedicated AI Alerting rule chain",
        "layoutX": 1120,
        "layoutY": 1450
    }
}

meta["nodes"].append(input_node)

# Connect from Node 1 (Save Attributes) and Node 0 (Save Timeseries) on 'Success'
meta["connections"].append({"fromIndex": 1, "toIndex": new_node_index, "type": "Success"})
meta["connections"].append({"fromIndex": 0, "toIndex": new_node_index, "type": "Success"})

logger.info(f"Linking Node 1 (Save Attributes) & Node 0 (Save Timeseries) -> New Node [{new_node_index}] (AI Outage & Anomaly Operations Dispatch)...")

resp = requests.post(f"{TB_HOST}/api/ruleChain/metadata", json=meta, headers=headers, timeout=20)
if resp.status_code != 200:
    raise RuntimeError(f"Failed to update root rule chain metadata: {resp.status_code} {resp.text}")

logger.info("Successfully linked AI Rule Chain into Root Rule Chain(Dexter)!")
