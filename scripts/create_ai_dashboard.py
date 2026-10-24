#!/usr/bin/env python
"""scripts/create_ai_dashboard.py
Step 1: Programmatically create the Dedicated 'AI Fleet Operations & Outage Prediction' Dashboard
directly in ThingsBoard (https://seple.iot-private.cloud) via the REST API.

Updates dashboard ID 4a25a020-62f7-11f1-9463-fde182b082f4 with full system-level
typeFullFqn schemas for ThingsBoard PE.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("create_ai_dashboard")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL", "info@seple.in")
TB_PASSWORD = os.getenv("TB_PASSWORD", "")

LATEST_DIGEST_JSON = ROOT / "reports/latest_risk_digest.json"
TARGET_DASHBOARD_ID = "4a25a020-62f7-11f1-9463-fde182b082f4"


def get_auth_token() -> str:
    """Authenticate and obtain JWT token from ThingsBoard."""
    logger.info(f"Authenticating with ThingsBoard at {TB_HOST}...")
    resp = requests.post(
        f"{TB_HOST}/api/auth/login",
        json={"username": TB_EMAIL, "password": TB_PASSWORD},
        timeout=15
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ThingsBoard authentication failed: {resp.status_code} {resp.text}")
    return resp.json()["token"]


def build_dashboard_config(summary: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Construct valid ThingsBoard Dashboard Configuration JSON with full typeFullFqn."""
    alias_id = str(uuid.uuid4())
    banner_widget_id = str(uuid.uuid4())
    table_widget_id = str(uuid.uuid4())
    alarms_widget_id = str(uuid.uuid4())

    total_branches = summary.get("total_monitored_branches", 130)
    health_score = summary.get("fleet_health_score_pct", 55.8)
    critical_count = summary.get("critical_fault_count", 28)
    high_risk_count = summary.get("high_risk_count", 20)
    recovered_count = summary.get("serviced_recovered_count", 5)
    elevated_count = summary.get("elevated_count", 19)
    nominal_count = summary.get("nominal_count", 58)

    # 1. Entity Alias for all Devices in tenant
    entity_aliases = {
        alias_id: {
            "id": alias_id,
            "alias": "All Monitored Bank Devices",
            "filter": {
                "type": "entityType",
                "resolveMultiple": True,
                "entityType": "DEVICE"
            }
        }
    }

    # 2. Executive Banner HTML
    banner_html = f"""<div style="display: flex; gap: 12px; width: 100%; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <div style="flex: 1.1; background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color: white; padding: 16px 18px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.08);">
    <div style="font-size: 11px; opacity: 0.85; text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em;">Fleet Health Index</div>
    <div style="font-size: 28px; font-weight: 800; margin-top: 2px; color: #38bdf8;">{health_score}%</div>
    <div style="font-size: 10px; opacity: 0.8; margin-top: 2px;">{nominal_count + recovered_count} of {total_branches} Branches Healthy</div>
  </div>
  <div style="flex: 1; background: #ffffff; border: 1px solid #fee2e2; border-left: 5px solid #dc2626; padding: 16px 16px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.03);">
    <div style="font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 700;">Critical Faults</div>
    <div style="font-size: 28px; font-weight: 800; color: #dc2626; margin-top: 2px;">{critical_count}</div>
    <div style="font-size: 10px; color: #ef4444; margin-top: 2px;">Ongoing physical/power outages</div>
  </div>
  <div style="flex: 1; background: #ffffff; border: 1px solid #fef3c7; border-left: 5px solid #d97706; padding: 16px 16px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.03);">
    <div style="font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 700;">24h Risk &ge; 50%</div>
    <div style="font-size: 28px; font-weight: 800; color: #d97706; margin-top: 2px;">{high_risk_count}</div>
    <div style="font-size: 10px; color: #b45309; margin-top: 2px;">Predictive collapse within 24h</div>
  </div>
  <div style="flex: 1; background: #ffffff; border: 1px solid #d1fae5; border-left: 5px solid #059669; padding: 16px 16px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.03);">
    <div style="font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 700;">Serviced & Restored</div>
    <div style="font-size: 28px; font-weight: 800; color: #059669; margin-top: 2px;">{recovered_count}</div>
    <div style="font-size: 10px; color: #047857; margin-top: 2px;">Verified healthy post-fix</div>
  </div>
  <div style="flex: 1; background: #ffffff; border: 1px solid #e0f2fe; border-left: 5px solid #0284c7; padding: 16px 16px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.03);">
    <div style="font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 700;">Elevated Drift</div>
    <div style="font-size: 28px; font-weight: 800; color: #0284c7; margin-top: 2px;">{elevated_count}</div>
    <div style="font-size: 10px; color: #0369a1; margin-top: 2px;">Warning tier (service in 7d)</div>
  </div>
</div>"""

    banner_widget = {
        "id": banner_widget_id,
        "typeFullFqn": "system.cards.html_card",
        "type": "static",
        "title": "Fleet Health KPI Overview",
        "sizeX": 24,
        "sizeY": 3,
        "row": 0,
        "col": 0,
        "config": {
            "showTitle": False,
            "backgroundColor": "transparent",
            "padding": "0px",
            "dropShadow": False,
            "datasources": [
                {
                    "type": "static",
                    "name": "function",
                    "dataKeys": [
                        {
                            "name": "f(x)",
                            "type": "function",
                            "label": "kpi",
                            "color": "#2196f3",
                            "settings": {},
                            "funcBody": "return true;"
                        }
                    ]
                }
            ],
            "settings": {
                "cardHtml": banner_html,
                "cardCss": ""
            }
        }
    }

    # 3. Entities Table for AI Fleet Attributes
    table_widget = {
        "id": table_widget_id,
        "typeFullFqn": "system.cards.entities_table",
        "type": "latest",
        "title": "AI Predictive Fleet Risk & Operations Matrix (130 Branches)",
        "sizeX": 24,
        "sizeY": 10,
        "row": 3,
        "col": 0,
        "config": {
            "showTitle": True,
            "backgroundColor": "#ffffff",
            "color": "rgba(0, 0, 0, 0.87)",
            "padding": "8px",
            "dropShadow": True,
            "enableFullscreen": True,
            "titleStyle": {
                "fontSize": "16px",
                "fontWeight": "bold",
                "padding": "8px 12px"
            },
            "settings": {
                "entitiesTitle": "Bank Branches",
                "enableSearch": True,
                "enableSelectColumnDisplay": True,
                "enableStickyHeader": True,
                "displayEntityName": False,
                "displayEntityLabel": False,
                "displayEntityType": False,
                "displayPagination": True,
                "defaultPageSize": 15,
                "defaultSortOrder": "ai_outage_risk_prob:desc"
            },
            "datasources": [
                {
                    "type": "entity",
                    "name": None,
                    "entityAliasId": alias_id,
                    "dataKeys": [
                        {
                            "name": "name",
                            "type": "entityField",
                            "label": "Branch Name",
                            "color": "#1e293b",
                            "settings": {
                                "columnWidth": "180px"
                            }
                        },
                        {
                            "name": "ai_health_tier",
                            "type": "attribute",
                            "label": "AI Health Tier",
                            "color": "#dc2626",
                            "settings": {
                                "columnWidth": "140px",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "if (value === 'CRITICAL_FAULT') return {color: '#991b1b', backgroundColor: '#fee2e2', fontWeight: 'bold', padding: '4px 8px', borderRadius: '4px'}; if (value === 'HIGH_RISK') return {color: '#92400e', backgroundColor: '#fef3c7', fontWeight: 'bold', padding: '4px 8px', borderRadius: '4px'}; if (value === 'SERVICED_RECOVERED') return {color: '#065f46', backgroundColor: '#d1fae5', fontWeight: 'bold', padding: '4px 8px', borderRadius: '4px'}; if (value === 'ELEVATED') return {color: '#0369a1', backgroundColor: '#e0f2fe', fontWeight: 'bold', padding: '4px 8px', borderRadius: '4px'}; return {color: '#166534', backgroundColor: '#dcfce7', fontWeight: 'bold', padding: '4px 8px', borderRadius: '4px'};"
                            }
                        },
                        {
                            "name": "ai_outage_risk_prob",
                            "type": "attribute",
                            "label": "24h Outage Risk",
                            "color": "#ea580c",
                            "settings": {
                                "columnWidth": "130px",
                                "useCellContentFunction": True,
                                "cellContentFunction": "if (value !== undefined && value !== null && value !== '') { return (parseFloat(value) * 100).toFixed(1) + '%'; } return '-';",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "var p = parseFloat(value); if (p >= 0.8) return {color: '#b91c1c', fontWeight: 'bold'}; if (p >= 0.5) return {color: '#d97706', fontWeight: 'bold'}; return {color: '#15803d'};"
                            }
                        },
                        {
                            "name": "ai_anomaly_score",
                            "type": "attribute",
                            "label": "Anomaly Score",
                            "color": "#6366f1",
                            "settings": {
                                "columnWidth": "130px",
                                "useCellContentFunction": True,
                                "cellContentFunction": "if (value !== undefined && value !== null && value !== '') { return parseFloat(value).toFixed(4); } return '-';"
                            }
                        },
                        {
                            "name": "ai_primary_issue",
                            "type": "attribute",
                            "label": "Primary Failure Mode / Root Cause",
                            "color": "#334155",
                            "settings": {
                                "columnWidth": "280px"
                            }
                        },
                        {
                            "name": "ai_recommended_action",
                            "type": "attribute",
                            "label": "Recommended Technician Action",
                            "color": "#0f766e",
                            "settings": {
                                "columnWidth": "300px"
                            }
                        },
                        {
                            "name": "ai_last_evaluated",
                            "type": "attribute",
                            "label": "Last Evaluated",
                            "color": "#64748b",
                            "settings": {
                                "columnWidth": "160px"
                            }
                        }
                    ]
                }
            ]
        }
    }

    # 4. Alarms Table for Active AI Alarms
    alarms_widget = {
        "id": alarms_widget_id,
        "typeFullFqn": "system.alarm_widgets.alarms_table",
        "type": "alarm",
        "title": "Active AI Alarms (Critical Anomalies & Outage Risk Alerts)",
        "sizeX": 24,
        "sizeY": 8,
        "row": 13,
        "col": 0,
        "config": {
            "timewindow": {
                "selectedTab": 0,
                "realtime": {
                    "realtimeType": 0,
                    "timewindowMs": 2592000000
                }
            },
            "showTitle": True,
            "backgroundColor": "#ffffff",
            "color": "rgba(0, 0, 0, 0.87)",
            "padding": "8px",
            "dropShadow": True,
            "enableFullscreen": True,
            "titleStyle": {
                "fontSize": "16px",
                "fontWeight": "bold",
                "color": "#991b1b",
                "padding": "8px 12px"
            },
            "alarmSearchStatus": "ACTIVE",
            "alarmTypeList": ["AI_CRITICAL_ANOMALY", "AI_PREDICTIVE_OUTAGE_RISK"],
            "alarmSeverityList": ["CRITICAL", "MAJOR", "MINOR", "WARNING"],
            "settings": {
                "enableSelection": True,
                "enableSearch": True,
                "displayDetails": True,
                "allowAcknowledgment": True,
                "allowClear": True,
                "allowAssign": True,
                "displayActivity": True,
                "displayPagination": True,
                "defaultPageSize": 10,
                "defaultSortOrder": "-createdTime",
                "enableSelectColumnDisplay": True,
                "enableFilter": True,
                "alarmsTitle": "Active AI Alarms"
            },
            "alarmSource": {
                "type": "entity",
                "entityAliasId": alias_id,
                "dataKeys": [
                    {
                        "name": "createdTime",
                        "type": "alarm",
                        "label": "Created time",
                        "color": "#2196f3",
                        "settings": {}
                    },
                    {
                        "name": "originatorDisplayName",
                        "type": "alarm",
                        "label": "Branch / Device",
                        "color": "#4caf50",
                        "settings": {}
                    },
                    {
                        "name": "type",
                        "type": "alarm",
                        "label": "Alarm Type",
                        "color": "#f44336",
                        "settings": {}
                    },
                    {
                        "name": "severity",
                        "type": "alarm",
                        "label": "Severity",
                        "color": "#ffc107",
                        "settings": {}
                    },
                    {
                        "name": "status",
                        "type": "alarm",
                        "label": "Status",
                        "color": "#607d8b",
                        "settings": {}
                    }
                ]
            }
        }
    }

    widgets = {
        banner_widget_id: banner_widget,
        table_widget_id: table_widget,
        alarms_widget_id: alarms_widget
    }

    # Layout Grid Placement
    layouts = {
        "main": {
            "widgets": {
                banner_widget_id: {"sizeX": 24, "sizeY": 3, "col": 0, "row": 0},
                table_widget_id: {"sizeX": 24, "sizeY": 10, "col": 0, "row": 3},
                alarms_widget_id: {"sizeX": 24, "sizeY": 8, "col": 0, "row": 13}
            },
            "gridSettings": {
                "layoutType": "default",
                "columns": 24,
                "margin": 10,
                "outerMargin": True,
                "backgroundColor": "#f8fafc",
                "autoFillHeight": False,
                "backgroundSizeMode": "100%"
            }
        }
    }

    configuration = {
        "description": "SEPLE NovaEdge AI Operations Dashboard: 365-day fleet anomaly scoring & 24h electrical collapse predictions across 130 bank branches.",
        "widgets": widgets,
        "states": {
            "default": {
                "name": "default",
                "root": True,
                "layouts": layouts
            }
        },
        "entityAliases": entity_aliases,
        "filters": {},
        "timewindow": {
            "realtime": {
                "timewindowMs": 86400000
            }
        },
        "settings": {
            "stateControllerId": "entity",
            "showTitle": True,
            "showDashboardsSelect": True,
            "showEntitiesSelect": False,
            "showDashboardTimewindow": True,
            "showDashboardExport": True
        }
    }

    return configuration, alias_id


def create_or_update_dashboard() -> str:
    token = get_auth_token()
    headers = {"X-Authorization": f"Bearer {token}"}

    summary: Dict[str, Any] = {}
    if LATEST_DIGEST_JSON.exists():
        with open(LATEST_DIGEST_JSON, "r", encoding="utf-8") as f:
            summary = json.load(f).get("summary", {})

    target_title = "AI Fleet Operations & Outage Prediction"
    existing_id = TARGET_DASHBOARD_ID

    logger.info(f"Deploying AI dashboard configuration into ThingsBoard dashboard ID: {existing_id}...")
    configuration, _ = build_dashboard_config(summary)

    payload: Dict[str, Any] = {
        "id": {"entityType": "DASHBOARD", "id": existing_id},
        "title": target_title,
        "configuration": configuration
    }

    resp = requests.post(f"{TB_HOST}/api/dashboard", json=payload, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to save dashboard: {resp.status_code} - {resp.text}")

    dash_data = resp.json()
    dash_id = dash_data.get("id", {}).get("id")
    dashboard_url = f"{TB_HOST}/dashboards/{dash_id}"
    logger.info("=" * 65)
    logger.info(f"SUCCESS! ThingsBoard AI Dashboard updated with full typeFullFqn schemas:")
    logger.info(f"URL: {dashboard_url}")
    logger.info("=" * 65)
    return dashboard_url


if __name__ == "__main__":
    url = create_or_update_dashboard()
    print(f"\nDashboard URL: {url}")
