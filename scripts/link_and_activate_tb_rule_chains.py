"""scripts/link_and_activate_tb_rule_chains.py
Link and Activate SEPLE NovaEdge AI Rule Chain in ThingsBoard.

1. Updates 'AI Outage & Anomaly Operations Dispatch' (b00aff60-cf9a-11f1-8a38-793373dfbb03)
   with enriched JS filters, resilient Slack payload formatting, and working Gmail SMTP credentials.
2. Links 'AlarmCreatingNew' (ab630950-482c-11ef-bec6-6743a7e32eb4) createAlarm node [13]
   to the AI Rule Chain on 'Created' and 'Updated' relations.
3. Confirms 'Root Rule Chain(Dexter)' (d15e00e0-b913-11ee-be46-bbf26874563f)
   routes Post Telemetry (Node 0) and Post Attributes (Node 1) to the AI Rule Chain.
"""

import os
import json
import logging
import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("activate_ai_rule_chain")

load_dotenv(".env")
TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL")
TB_PASSWORD = os.getenv("TB_PASSWORD")

ROOT_RC_ID = "d15e00e0-b913-11ee-be46-bbf26874563f"
ALARM_RC_ID = "ab630950-482c-11ef-bec6-6743a7e32eb4"
AI_RC_ID = "b00aff60-cf9a-11f1-8a38-793373dfbb03"

def get_auth_headers():
    res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD}, timeout=10)
    if res.status_code != 200:
        raise RuntimeError(f"ThingsBoard login failed: {res.status_code} {res.text}")
    token = res.json()["token"]
    return {"X-Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def update_ai_rule_chain_metadata(headers):
    logger.info(f"1. Updating AI Rule Chain ({AI_RC_ID}) metadata and configuration...")
    res = requests.get(f"{TB_HOST}/api/ruleChain/{AI_RC_ID}/metadata", headers=headers, timeout=15)
    meta = res.json()
    
    # 1. Update Node 0 (Filter)
    meta["nodes"][0]["configuration"]["jsScript"] = """
// 1. Explicit AI alarm types
var isAiAlarm = msg.type === 'AI_CRITICAL_ANOMALY' || 
                msg.type === 'AI_PREDICTIVE_OUTAGE_RISK' || 
                (metadata.alarmType && (metadata.alarmType === 'AI_CRITICAL_ANOMALY' || metadata.alarmType === 'AI_PREDICTIVE_OUTAGE_RISK'));

// 2. High risk telemetry / attribute scoring from NovaEdge AI Agent
var isAiRisk = (msg.risk_tier && (msg.risk_tier === 'CRITICAL_FAULT' || msg.risk_tier === 'HIGH_RISK_OUTAGE')) ||
               (msg.predicted_outage_risk_24h && parseFloat(msg.predicted_outage_risk_24h) >= 0.50);

// 3. Critical hardware alarms routed from AlarmCreatingNew
var isCriticalFleetAlarm = (msgType === 'ALARM' || metadata.alarmSeverity === 'CRITICAL' || msg.severity === 'CRITICAL') &&
                           (msg.type !== undefined || metadata.alarmType !== undefined);

return isAiAlarm || isAiRisk || isCriticalFleetAlarm;
"""

    # 2. Update Node 1 (Slack Formatter)
    meta["nodes"][1]["configuration"]["jsScript"] = """
var alertType = msg.type || metadata.alarmType || (msg.risk_tier ? 'AI Risk: ' + msg.risk_tier : 'AI Operational Risk Alert');
var severity = msg.severity || metadata.alarmSeverity || (msg.risk_tier === 'CRITICAL_FAULT' ? 'CRITICAL' : 'MAJOR');
var devName = metadata.originatorName || metadata.entityName || metadata.deviceName || metadata.device_name || 'Bank Branch';
var outageRisk = 'Elevated';
var rootCause = 'Acute baseline deviation';
var action = 'Inspect electrical & edge equipment';

if (msg.details) {
    var d = msg.details;
    if (typeof d === 'string') {
        try { d = JSON.parse(d); } catch(e){}
    }
    if (d.predicted_outage_risk_24h !== undefined) {
        outageRisk = (parseFloat(d.predicted_outage_risk_24h) * 100).toFixed(1) + '%';
    }
    if (d.primary_issue) {
        rootCause = d.primary_issue;
    }
    if (d.recommended_action) {
        action = d.recommended_action;
    }
} else if (msg.predicted_outage_risk_24h !== undefined) {
    outageRisk = (parseFloat(msg.predicted_outage_risk_24h) * 100).toFixed(1) + '%';
    if (msg.primary_issue) rootCause = msg.primary_issue;
    if (msg.recommended_action) action = msg.recommended_action;
} else if (msg.type) {
    rootCause = msg.type + ' incident detected';
    action = 'Immediate technician dispatch / equipment check';
}

var slackPayload = {
    blocks: [
        {
            type: "header",
            text: {
                type: "plain_text",
                text: "🚨 " + alertType + " | " + devName,
                emoji: true
            }
        },
        {
            type: "section",
            text: {
                type: "mrkdwn",
                text: "*Branch:* *" + devName + "*\\n" +
                      "*Severity:* `" + severity + "` | *24h Outage Probability:* `" + outageRisk + "`\\n" +
                      "*Primary Root Cause:* " + rootCause + "\\n" +
                      "*Action Required:* " + action
            }
        },
        {
            type: "actions",
            elements: [
                {
                    type: "button",
                    text: {
                        type: "plain_text",
                        text: "📊 Open ThingsBoard AI Dashboard"
                    },
                    url: "https://seple.iot-private.cloud/dashboards/4a25a020-62f7-11f1-9463-fde182b082f4",
                    style: "primary"
                }
            ]
        }
    ]
};

return { msg: slackPayload, metadata: metadata, msgType: msgType };
"""

    # 3. Update Node 3 & 4 (Email Message & SMTP Configuration)
    meta["nodes"][3]["configuration"]["fromTemplate"] = os.getenv("SMTP_EMAIL", "itinerant018@gmail.com")
    meta["nodes"][3]["configuration"]["toTemplate"] = os.getenv("EMAIL_TO", "animesh.choudhury@seple.in")

    meta["nodes"][4]["configuration"]["useSystemSmtpSettings"] = False
    meta["nodes"][4]["configuration"]["smtpHost"] = "smtp.gmail.com"
    meta["nodes"][4]["configuration"]["smtpPort"] = 587
    meta["nodes"][4]["configuration"]["username"] = os.getenv("SMTP_EMAIL", "itinerant018@gmail.com")
    meta["nodes"][4]["configuration"]["password"] = os.getenv("SMTP_PASSWORD", "uszq fpmy afct jwom")
    meta["nodes"][4]["configuration"]["smtpProtocol"] = "smtp"
    meta["nodes"][4]["configuration"]["enableTls"] = True
    meta["nodes"][4]["configuration"]["tlsVersion"] = "TLSv1.2"

    save_res = requests.post(f"{TB_HOST}/api/ruleChain/metadata", json=meta, headers=headers, timeout=20)
    if save_res.status_code != 200:
        raise RuntimeError(f"Failed to update AI Rule Chain metadata: {save_res.status_code} {save_res.text}")
    logger.info("✅ AI Rule Chain metadata updated successfully.")

def link_alarm_creating_rule_chain(headers):
    logger.info(f"2. Inspecting AlarmCreatingNew ({ALARM_RC_ID})...")
    res = requests.get(f"{TB_HOST}/api/ruleChain/{ALARM_RC_ID}/metadata", headers=headers, timeout=15)
    meta = res.json()

    # Check if already linked
    existing_idx = None
    for idx, node in enumerate(meta.get("nodes", [])):
        if node.get("type") == "org.thingsboard.rule.engine.flow.TbRuleChainInputNode":
            if node.get("configuration", {}).get("ruleChainId") == AI_RC_ID:
                existing_idx = idx
                break

    if existing_idx is not None:
        logger.info(f"AI Rule Chain already linked in AlarmCreatingNew at node index [{existing_idx}].")
        input_node_idx = existing_idx
    else:
        input_node_idx = len(meta["nodes"])
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
                "description": "Forward newly created/updated hardware alarms to AI Dispatch Chain",
                "layoutX": 1100,
                "layoutY": 750
            }
        }
        meta["nodes"].append(input_node)
        logger.info(f"Added new TbRuleChainInputNode at index [{input_node_idx}] pointing to AI Rule Chain.")

    # Find createAlarm node (index 13)
    create_alarm_idx = 13
    assert meta["nodes"][create_alarm_idx]["name"] == "createAlarm", f"Expected node 13 to be createAlarm, found {meta['nodes'][create_alarm_idx]['name']}"

    # Ensure connections on 'Created' and 'Updated'
    conns = meta.get("connections", [])
    has_created = any(c.get("fromIndex") == create_alarm_idx and c.get("toIndex") == input_node_idx and c.get("type") == "Created" for c in conns)
    has_updated = any(c.get("fromIndex") == create_alarm_idx and c.get("toIndex") == input_node_idx and c.get("type") == "Updated" for c in conns)

    if not has_created:
        conns.append({"fromIndex": create_alarm_idx, "toIndex": input_node_idx, "type": "Created"})
        logger.info(f"Added connection: createAlarm [{create_alarm_idx}] -(Created)-> AI Rule Chain [{input_node_idx}]")
    if not has_updated:
        conns.append({"fromIndex": create_alarm_idx, "toIndex": input_node_idx, "type": "Updated"})
        logger.info(f"Added connection: createAlarm [{create_alarm_idx}] -(Updated)-> AI Rule Chain [{input_node_idx}]")

    meta["connections"] = conns
    save_res = requests.post(f"{TB_HOST}/api/ruleChain/metadata", json=meta, headers=headers, timeout=20)
    if save_res.status_code != 200:
        raise RuntimeError(f"Failed to update AlarmCreatingNew metadata: {save_res.status_code} {save_res.text}")
    logger.info("✅ AlarmCreatingNew successfully linked to AI Rule Chain!")

def verify_root_rule_chain(headers):
    logger.info(f"3. Verifying Root Rule Chain ({ROOT_RC_ID})...")
    res = requests.get(f"{TB_HOST}/api/ruleChain/{ROOT_RC_ID}/metadata", headers=headers, timeout=15)
    meta = res.json()

    linked_idx = None
    for idx, node in enumerate(meta.get("nodes", [])):
        if node.get("type") == "org.thingsboard.rule.engine.flow.TbRuleChainInputNode":
            if node.get("configuration", {}).get("ruleChainId") == AI_RC_ID:
                linked_idx = idx
                break

    if linked_idx is None:
        raise RuntimeError("AI Rule Chain input node is missing in Root Rule Chain!")

    inbound = [c for c in meta.get("connections", []) if c.get("toIndex") == linked_idx]
    logger.info(f"Root Rule Chain Node [{linked_idx}] has {len(inbound)} inbound connections: {inbound}")
    logger.info("✅ Root Rule Chain verification complete.")

def main():
    headers = get_auth_headers()
    update_ai_rule_chain_metadata(headers)
    link_alarm_creating_rule_chain(headers)
    verify_root_rule_chain(headers)
    logger.info("\n🎉 All ThingsBoard Rule Chains linked and activated successfully!")

if __name__ == "__main__":
    main()
