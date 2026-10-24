#!/usr/bin/env python
"""scripts/deploy_ai_rule_chain.py
Configures metadata for the dedicated Rule Chain:
'AI Outage & Anomaly Operations Dispatch' (ID: b00aff60-cf9a-11f1-8a38-793373dfbb03)

Pipeline:
1. Filter: Matches AI_CRITICAL_ANOMALY or AI_PREDICTIVE_OUTAGE_RISK alarms.
2. Slack Branch:
   - Transform: Formats Slack Block Kit message with branch name, severity, outage probability,
     root cause, and direct dashboard button.
   - REST API Call: Posts to Slack incoming webhook (https://hooks.slack.com/services/...).
3. Email Branch:
   - MsgToEmail: Constructs alert email addressed to animesh.choudhury@seple.in.
   - SendEmail: Dispatches using system SMTP.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("deploy_rule_chain")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL", "info@seple.in")
TB_PASSWORD = os.getenv("TB_PASSWORD", "")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "animesh.choudhury@seple.in").strip()
RULE_CHAIN_ID = "b00aff60-cf9a-11f1-8a38-793373dfbb03"


def get_auth_token() -> str:
    resp = requests.post(
        f"{TB_HOST}/api/auth/login",
        json={"username": TB_EMAIL, "password": TB_PASSWORD},
        timeout=15
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Authentication failed: {resp.status_code} {resp.text}")
    return resp.json()["token"]


def deploy_metadata() -> None:
    token = get_auth_token()
    headers = {"X-Authorization": f"Bearer {token}"}

    logger.info(f"Building metadata for Rule Chain {RULE_CHAIN_ID}...")

    # Node 0: Filter AI Alarms & Risk Telemetry
    filter_js = """
var isAiAlarm = msg.type === 'AI_CRITICAL_ANOMALY' || 
                msg.type === 'AI_PREDICTIVE_OUTAGE_RISK' || 
                (metadata.alarmType && (metadata.alarmType === 'AI_CRITICAL_ANOMALY' || metadata.alarmType === 'AI_PREDICTIVE_OUTAGE_RISK'));

var isAiRisk = (msg.risk_tier && (msg.risk_tier === 'CRITICAL_FAULT' || msg.risk_tier === 'HIGH_RISK_OUTAGE')) ||
               (msg.predicted_outage_risk_24h && parseFloat(msg.predicted_outage_risk_24h) >= 0.50);

return isAiAlarm || isAiRisk;
"""

    node_filter = {
        "type": "org.thingsboard.rule.engine.filter.TbJsFilterNode",
        "name": "Filter AI Outage & Anomaly Alarms",
        "debugSettings": {"failuresEnabled": True, "allEnabled": False},
        "singletonMode": False,
        "configurationVersion": 0,
        "configuration": {
            "jsScript": filter_js
        },
        "additionalInfo": {"layoutX": 150, "layoutY": 250}
    }

    # Node 1: Format Slack Message
    slack_js = """
var alertType = msg.type || metadata.alarmType || (msg.risk_tier ? 'AI Risk: ' + msg.risk_tier : 'AI Operational Risk Alert');
var severity = msg.severity || metadata.alarmSeverity || (msg.risk_tier === 'CRITICAL_FAULT' ? 'CRITICAL' : 'MAJOR');
var devName = metadata.originatorName || metadata.entityName || metadata.deviceName || metadata.device_name || 'Bank Branch';
var outageRisk = 'Elevated';
var rootCause = 'Acute baseline deviation';
var action = 'Inspect electrical & edge equipment';

if (msg.details) {
    if (msg.details.predicted_outage_risk_24h !== undefined) {
        outageRisk = (parseFloat(msg.details.predicted_outage_risk_24h) * 100).toFixed(1) + '%';
    }
    if (msg.details.primary_issue) {
        rootCause = msg.details.primary_issue;
    }
    if (msg.details.recommended_action) {
        action = msg.details.recommended_action;
    }
} else {
    if (msg.predicted_outage_risk_24h !== undefined) {
        outageRisk = (parseFloat(msg.predicted_outage_risk_24h) * 100).toFixed(1) + '%';
    }
    if (msg.primary_issue) {
        rootCause = msg.primary_issue;
    }
    if (msg.recommended_action) {
        action = msg.recommended_action;
    }
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

    node_slack_format = {
        "type": "org.thingsboard.rule.engine.transform.TbTransformMsgNode",
        "name": "Format Slack Block Kit Payload",
        "debugSettings": {"failuresEnabled": True, "allEnabled": False},
        "singletonMode": False,
        "configurationVersion": 0,
        "configuration": {
            "jsScript": slack_js
        },
        "additionalInfo": {"layoutX": 420, "layoutY": 180}
    }

    # Node 2: REST API Call to Slack
    node_slack_call = {
        "type": "org.thingsboard.rule.engine.rest.TbRestApiCallNode",
        "name": "Real-time Slack Webhook",
        "debugSettings": {"failuresEnabled": True, "allEnabled": False},
        "singletonMode": False,
        "configurationVersion": 3,
        "configuration": {
            "restEndpointUrlPattern": SLACK_WEBHOOK,
            "requestMethod": "POST",
            "useSimpleClientHttpFactory": True,
            "parseToPlainText": False,
            "ignoreRequestBody": False,
            "enableProxy": False,
            "useSystemProxyProperties": False,
            "proxyScheme": None,
            "proxyHost": None,
            "proxyPort": 0,
            "proxyUser": None,
            "proxyPassword": None,
            "readTimeoutMs": 10000,
            "maxParallelRequestsCount": 0,
            "headers": {
                "Content-Type": "application/json"
            },
            "credentials": {
                "type": "anonymous"
            },
            "maxInMemoryBufferSizeInKb": 256
        },
        "additionalInfo": {"layoutX": 720, "layoutY": 180}
    }

    # Node 3: Prepare Email
    email_body = """Hi Operations Team,

An automated AI risk alert has been triggered for branch: $[originatorName].

Originator: $[originatorName]
Evaluated Risk: ${msg.details.predicted_outage_risk_24h}
Primary Root Cause: ${msg.details.primary_issue}
Recommended Field Action: ${msg.details.recommended_action}

Please inspect telemetry and active status on the ThingsBoard AI Dashboard:
https://seple.iot-private.cloud/dashboards/4a25a020-62f7-11f1-9463-fde182b082f4

Regards,
SEPLE NovaEdge AI Engine
"""

    node_email_format = {
        "type": "org.thingsboard.rule.engine.mail.TbMsgToEmailNode",
        "name": "Format Email Message",
        "debugSettings": {"failuresEnabled": True, "allEnabled": False},
        "singletonMode": False,
        "configurationVersion": 0,
        "configuration": {
            "fromTemplate": "alerts@dexterhms.com",
            "toTemplate": EMAIL_TO,
            "ccTemplate": None,
            "bccTemplate": None,
            "subjectTemplate": "[ThingsBoard AI Alert] Risk Alert on $[originatorName]",
            "mailBodyType": "false",
            "bodyTemplate": email_body
        },
        "additionalInfo": {"layoutX": 420, "layoutY": 340}
    }

    # Node 4: Send Email
    node_send_email = {
        "type": "org.thingsboard.rule.engine.mail.TbSendEmailNode",
        "name": "Send Alert Email",
        "debugSettings": {"failuresEnabled": True, "allEnabled": False},
        "singletonMode": False,
        "configurationVersion": 0,
        "configuration": {
            "useSystemSmtpSettings": True,
            "smtpHost": "localhost",
            "smtpPort": 25,
            "username": None,
            "password": None,
            "smtpProtocol": "smtp",
            "timeout": 10000,
            "enableTls": False,
            "tlsVersion": "TLSv1.2",
            "enableProxy": False,
            "proxyHost": None,
            "proxyPort": None,
            "proxyUser": None,
            "proxyPassword": None
        },
        "additionalInfo": {"layoutX": 720, "layoutY": 340}
    }

    nodes = [node_filter, node_slack_format, node_slack_call, node_email_format, node_send_email]

    connections = [
        # Filter True -> Slack Format & Email Format
        {"fromIndex": 0, "toIndex": 1, "type": "True"},
        {"fromIndex": 0, "toIndex": 3, "type": "True"},
        # Slack Format Success -> Slack Call
        {"fromIndex": 1, "toIndex": 2, "type": "Success"},
        # Email Format Success -> Send Email
        {"fromIndex": 3, "toIndex": 4, "type": "Success"}
    ]

    metadata_payload = {
        "ruleChainId": {"entityType": "RULE_CHAIN", "id": RULE_CHAIN_ID},
        "firstNodeIndex": 0,
        "nodes": nodes,
        "connections": connections,
        "ruleChainConnections": None
    }

    logger.info(f"Uploading metadata to {TB_HOST}/api/ruleChain/metadata...")
    resp = requests.post(f"{TB_HOST}/api/ruleChain/metadata", json=metadata_payload, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to deploy rule chain metadata: {resp.status_code} {resp.text}")

    logger.info("=" * 65)
    logger.info("SUCCESS! AI Rule Chain metadata deployed:")
    logger.info(f"Rule Chain URL: {TB_HOST}/ruleChains/{RULE_CHAIN_ID}")
    logger.info("Nodes deployed:")
    logger.info("  1. Filter AI Outage & Anomaly Alarms")
    logger.info("  2. Format Slack Block Kit Payload")
    logger.info("  3. Real-time Slack Webhook Node (REST API Call)")
    logger.info("  4. Format Email Message")
    logger.info("  5. Send Alert Email")
    logger.info("=" * 65)


if __name__ == "__main__":
    deploy_metadata()
