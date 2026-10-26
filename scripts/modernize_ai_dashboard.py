"""scripts/modernize_ai_dashboard.py
Modernize SEPLE NovaEdge AI Dashboard into Cloudflare Observability Style.

Applies:
- Solid dark slate surfaces (#111827 canvas, #1f2937 cards, #374151 borders).
- Zero gradients, zero decorative blur — razor-sharp industrial utility.
- 5 sharp rectangular KPI tiles with 3px electric accent lines and monospace numbers.
- AI Predictive Risk Matrix with electric status pill badges and sorted outage probability.
- Active Alarms Table with high-contrast incident display.
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("modernize_ai_dashboard")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL", "info@seple.in")
TB_PASSWORD = os.getenv("TB_PASSWORD", "")

LATEST_DIGEST_JSON = ROOT / "reports/latest_risk_digest.json"
TARGET_DASHBOARD_ID = "4a25a020-62f7-11f1-9463-fde182b082f4"

CLOUDFLARE_DASHBOARD_CSS = """
/* ========================================================
   CLOUDFLARE OBSERVABILITY INDUSTRIAL DARK STYLING
   Solid dark surfaces, crisp 1px borders, zero gradients.
   ======================================================== */

.tb-dashboard-page, body {
    background-color: #0e121b !important;
    color: #f3f4f6 !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
}

/* Base Widget Cards */
.tb-card, mat-card.tb-card, .card-container {
    background-color: #161b26 !important;
    border: 1px solid #283042 !important;
    border-radius: 6px !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4) !important;
    margin: 0px !important;
}

/* Header & Typography */
.tb-widget-title, .tb-primary-text {
    color: #f9fafb !important;
    font-weight: 700 !important;
    letter-spacing: 0.03em !important;
}

.tb-secondary-text {
    color: #94a3b8 !important;
}

/* Tables & Grids */
mat-table, .mat-table {
    background-color: transparent !important;
}

mat-header-row, .mat-header-row {
    background-color: #11151f !important;
    border-bottom: 1px solid #283042 !important;
}

.mat-header-cell {
    color: #94a3b8 !important;
    font-size: 11px !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    border-bottom-color: #283042 !important;
}

mat-row, .mat-row {
    background-color: #161b26 !important;
    border-bottom: 1px solid #202636 !important;
    color: #f1f5f9 !important;
    transition: background-color 0.12s ease !important;
}

mat-row:hover, .mat-row:hover {
    background-color: #212838 !important;
}

.mat-cell {
    border-bottom-color: #202636 !important;
    color: #f3f4f6 !important;
}

/* Search bar, inputs, & paginator */
.mat-form-field-appearance-fill .mat-form-field-flex {
    background-color: #11151f !important;
    border: 1px solid #283042 !important;
    border-radius: 4px !important;
}

mat-paginator, .mat-paginator {
    background-color: #11151f !important;
    color: #94a3b8 !important;
    border-top: 1px solid #283042 !important;
}

/* Monospace Numbers & Code elements */
.mono-metric {
    font-family: "JetBrains Mono", "Roboto Mono", "SF Mono", Consolas, monospace !important;
}

/* Custom Clean Scrollbars */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}
::-webkit-scrollbar-track {
    background: #0e121b;
}
::-webkit-scrollbar-thumb {
    background: #334155;
    border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
    background: #475569;
}
"""


def get_auth_token() -> str:
    """Authenticate and obtain JWT token from ThingsBoard."""
    resp = requests.post(
        f"{TB_HOST}/api/auth/login",
        json={"username": TB_EMAIL, "password": TB_PASSWORD},
        timeout=15
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ThingsBoard authentication failed: {resp.status_code} {resp.text}")
    return resp.json()["token"]


def build_modern_kpi_html(summary: Dict[str, Any]) -> str:
    """Construct solid, sharp rectangular KPI Command Deck."""
    total_branches = summary.get("total_monitored_branches", 130)
    health_score = summary.get("fleet_health_score_pct", 55.0)
    critical_count = summary.get("critical_fault_count", 28)
    high_risk_count = summary.get("high_risk_count", 21)
    recovered_count = summary.get("serviced_recovered_count", 4)
    elevated_count = summary.get("elevated_count", 19)
    nominal_count = summary.get("nominal_count", 58)

    return f"""<div style="display: flex; gap: 10px; width: 100%; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <!-- 1. Fleet Health Index -->
  <div style="flex: 1.1; background-color: #161b26; border: 1px solid #283042; border-top: 3px solid #0ea5e9; padding: 14px 16px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.3);">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <span style="font-size: 11px; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">Fleet Health Index</span>
      <span style="font-size: 10px; color: #0ea5e9; font-family: monospace; background: #082f49; padding: 1px 6px; border-radius: 2px;">STABLE</span>
    </div>
    <div style="font-size: 28px; font-weight: 700; font-family: 'JetBrains Mono', 'Roboto Mono', monospace; color: #38bdf8; margin-top: 4px;">{health_score:.1f}%</div>
    <div style="font-size: 11px; color: #64748b; margin-top: 4px; font-weight: 500;">{nominal_count + recovered_count} of {total_branches} Branches Nominal</div>
  </div>

  <!-- 2. Critical Faults -->
  <div style="flex: 1; background-color: #161b26; border: 1px solid #283042; border-top: 3px solid #f43f5e; padding: 14px 16px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.3);">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <span style="font-size: 11px; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">Critical Faults</span>
      <span style="font-size: 10px; color: #fda4af; font-family: monospace; background: #4c0519; padding: 1px 6px; border-radius: 2px;">IMPACT</span>
    </div>
    <div style="font-size: 28px; font-weight: 700; font-family: 'JetBrains Mono', 'Roboto Mono', monospace; color: #f43f5e; margin-top: 4px;">{critical_count}</div>
    <div style="font-size: 11px; color: #64748b; margin-top: 4px; font-weight: 500;">Active Outages Requiring Tech</div>
  </div>

  <!-- 3. High Predictive Risk -->
  <div style="flex: 1; background-color: #161b26; border: 1px solid #283042; border-top: 3px solid #f59e0b; padding: 14px 16px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.3);">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <span style="font-size: 11px; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">24h Risk &ge; 50%</span>
      <span style="font-size: 10px; color: #fde68a; font-family: monospace; background: #451a03; padding: 1px 6px; border-radius: 2px;">ALERT</span>
    </div>
    <div style="font-size: 28px; font-weight: 700; font-family: 'JetBrains Mono', 'Roboto Mono', monospace; color: #f59e0b; margin-top: 4px;">{high_risk_count}</div>
    <div style="font-size: 11px; color: #64748b; margin-top: 4px; font-weight: 500;">Predictive Collapse Warning</div>
  </div>

  <!-- 4. Serviced & Restored -->
  <div style="flex: 1; background-color: #161b26; border: 1px solid #283042; border-top: 3px solid #10b981; padding: 14px 16px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.3);">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <span style="font-size: 11px; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">Serviced & Restored</span>
      <span style="font-size: 10px; color: #6ee7b7; font-family: monospace; background: #022c22; padding: 1px 6px; border-radius: 2px;">VERIFIED</span>
    </div>
    <div style="font-size: 28px; font-weight: 700; font-family: 'JetBrains Mono', 'Roboto Mono', monospace; color: #10b981; margin-top: 4px;">{recovered_count}</div>
    <div style="font-size: 11px; color: #64748b; margin-top: 4px; font-weight: 500;">Healthy Post-Technician Fix</div>
  </div>

  <!-- 5. Elevated Drift -->
  <div style="flex: 1; background-color: #161b26; border: 1px solid #283042; border-top: 3px solid #06b6d4; padding: 14px 16px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.3);">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <span style="font-size: 11px; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;">Elevated Drift</span>
      <span style="font-size: 10px; color: #bae6fd; font-family: monospace; background: #082f49; padding: 1px 6px; border-radius: 2px;">MONITOR</span>
    </div>
    <div style="font-size: 28px; font-weight: 700; font-family: 'JetBrains Mono', 'Roboto Mono', monospace; color: #06b6d4; margin-top: 4px;">{elevated_count}</div>
    <div style="font-size: 11px; color: #64748b; margin-top: 4px; font-weight: 500;">Service Within 7 Days</div>
  </div>
</div>"""


def build_modern_dashboard_payload(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Construct full ThingsBoard Dashboard Configuration with Cloudflare styling."""
    alias_id = "256ad357-53ba-458f-a3aa-2ec9b040f001"
    banner_widget_id = "50263388-7e29-4d37-8cbd-267014732b82"
    table_widget_id = "3ec22488-34fa-4f2f-838a-ead69b1f3d2a"
    alarms_widget_id = "9ecdb8fd-ed07-476c-8d8b-da14aea0add8"

    banner_html = build_modern_kpi_html(summary)

    # 1. KPI Banner Widget
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
                            "color": "#0ea5e9",
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

    # 2. High-Density Predictive Risk Matrix Table
    table_widget = {
        "id": table_widget_id,
        "typeFullFqn": "system.cards.entities_table",
        "type": "latest",
        "title": "AI Predictive Fleet Risk & Operations Matrix (130 Branches)",
        "sizeX": 24,
        "sizeY": 11,
        "row": 3,
        "col": 0,
        "config": {
            "showTitle": True,
            "backgroundColor": "#161b26",
            "color": "#f9fafb",
            "padding": "12px",
            "dropShadow": False,
            "enableFullscreen": True,
            "titleStyle": {
                "fontSize": "14px",
                "fontWeight": "700",
                "color": "#f1f5f9",
                "letterSpacing": "0.05em",
                "textTransform": "uppercase",
                "padding": "4px 8px"
            },
            "settings": {
                "entitiesTitle": "Monitored Branches",
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
                            "label": "Branch Identifier",
                            "color": "#f8fafc",
                            "settings": {
                                "columnWidth": "190px",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "return {fontWeight: '700', color: '#f8fafc'};"
                            }
                        },
                        {
                            "name": "ai_health_tier",
                            "type": "attribute",
                            "label": "Health Status",
                            "color": "#f43f5e",
                            "settings": {
                                "columnWidth": "145px",
                                "useCellContentFunction": True,
                                "cellContentFunction": "if (value === 'CRITICAL_FAULT') return '● CRITICAL'; if (value === 'HIGH_RISK') return '▲ HIGH_RISK'; if (value === 'SERVICED_RECOVERED') return '✔ RECOVERED'; if (value === 'ELEVATED') return '◆ ELEVATED'; return '○ NOMINAL';",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "if (value === 'CRITICAL_FAULT') return {color: '#fda4af', backgroundColor: '#4c0519', border: '1px solid #f43f5e', fontWeight: '700', padding: '3px 8px', borderRadius: '3px', fontFamily: 'monospace', fontSize: '11px'}; if (value === 'HIGH_RISK') return {color: '#fde68a', backgroundColor: '#451a03', border: '1px solid #f59e0b', fontWeight: '700', padding: '3px 8px', borderRadius: '3px', fontFamily: 'monospace', fontSize: '11px'}; if (value === 'SERVICED_RECOVERED') return {color: '#6ee7b7', backgroundColor: '#022c22', border: '1px solid #10b981', fontWeight: '700', padding: '3px 8px', borderRadius: '3px', fontFamily: 'monospace', fontSize: '11px'}; if (value === 'ELEVATED') return {color: '#bae6fd', backgroundColor: '#082f49', border: '1px solid #0ea5e9', fontWeight: '700', padding: '3px 8px', borderRadius: '3px', fontFamily: 'monospace', fontSize: '11px'}; return {color: '#9ca3af', backgroundColor: '#11151f', border: '1px solid #374151', fontWeight: '600', padding: '3px 8px', borderRadius: '3px', fontFamily: 'monospace', fontSize: '11px'};"
                            }
                        },
                        {
                            "name": "ai_outage_risk_prob",
                            "type": "attribute",
                            "label": "24h Outage Risk",
                            "color": "#f59e0b",
                            "settings": {
                                "columnWidth": "135px",
                                "useCellContentFunction": True,
                                "cellContentFunction": "if (value !== undefined && value !== null && value !== '') { return (parseFloat(value) * 100).toFixed(1) + '%'; } return '-';",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "var p = parseFloat(value); if (p >= 0.80) return {color: '#f43f5e', fontWeight: '700', fontFamily: 'monospace', fontSize: '13px'}; if (p >= 0.50) return {color: '#f59e0b', fontWeight: '700', fontFamily: 'monospace', fontSize: '13px'}; return {color: '#10b981', fontFamily: 'monospace', fontSize: '12px'};"
                            }
                        },
                        {
                            "name": "ai_anomaly_score",
                            "type": "attribute",
                            "label": "Anomaly Deviation",
                            "color": "#38bdf8",
                            "settings": {
                                "columnWidth": "135px",
                                "useCellContentFunction": True,
                                "cellContentFunction": "if (value !== undefined && value !== null && value !== '') { return parseFloat(value).toFixed(4); } return '-';",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "return {fontFamily: 'monospace', color: '#94a3b8', fontSize: '12px'};"
                            }
                        },
                        {
                            "name": "ai_primary_issue",
                            "type": "attribute",
                            "label": "Primary Root Cause / Failure Mode",
                            "color": "#cbd5e1",
                            "settings": {
                                "columnWidth": "280px",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "return {color: '#cbd5e1', fontSize: '12px'};"
                            }
                        },
                        {
                            "name": "ai_recommended_action",
                            "type": "attribute",
                            "label": "Recommended Technician Action",
                            "color": "#2dd4bf",
                            "settings": {
                                "columnWidth": "300px",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "return {color: '#2dd4bf', fontSize: '12px'};"
                            }
                        },
                        {
                            "name": "ai_last_evaluated",
                            "type": "attribute",
                            "label": "Last Evaluated",
                            "color": "#64748b",
                            "settings": {
                                "columnWidth": "160px",
                                "useCellStyleFunction": True,
                                "cellStyleFunction": "return {color: '#64748b', fontFamily: 'monospace', fontSize: '11px'};"
                            }
                        }
                    ]
                }
            ]
        }
    }

    # 3. Active AI Alarms Console
    alarms_widget = {
        "id": alarms_widget_id,
        "typeFullFqn": "system.alarm_widgets.alarms_table",
        "type": "alarm",
        "title": "Active Incident Console (Critical Faults & Outage Risk Alarms)",
        "sizeX": 24,
        "sizeY": 8,
        "row": 14,
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
            "backgroundColor": "#161b26",
            "color": "#f9fafb",
            "padding": "12px",
            "dropShadow": False,
            "enableFullscreen": True,
            "titleStyle": {
                "fontSize": "14px",
                "fontWeight": "700",
                "color": "#f43f5e",
                "letterSpacing": "0.05em",
                "textTransform": "uppercase",
                "padding": "4px 8px"
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
                "displayPagination": True,
                "defaultPageSize": 10,
                "defaultSortOrder": "createdTime:desc"
            },
            "datasources": [
                {
                    "type": "entity",
                    "name": None,
                    "entityAliasId": alias_id,
                    "dataKeys": [
                        {"name": "createdTime", "type": "alarm", "label": "Incident Time", "color": "#64748b", "settings": {"columnWidth": "160px"}},
                        {"name": "originator", "type": "alarm", "label": "Branch", "color": "#f8fafc", "settings": {"columnWidth": "190px"}},
                        {"name": "type", "type": "alarm", "label": "Alarm Event", "color": "#f43f5e", "settings": {"columnWidth": "200px"}},
                        {"name": "severity", "type": "alarm", "label": "Severity", "color": "#fb7185", "settings": {"columnWidth": "120px"}},
                        {"name": "status", "type": "alarm", "label": "Status", "color": "#f59e0b", "settings": {"columnWidth": "140px"}},
                        {"name": "details", "type": "alarm", "label": "Root Cause Details", "color": "#cbd5e1", "settings": {"columnWidth": "350px"}}
                    ]
                }
            ]
        }
    }

    # Entity Alias
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

    dashboard_config = {
        "description": "Cloudflare Observability style AI Fleet Outage & Anomaly Operations Command Center",
        "widgets": {
            banner_widget_id: banner_widget,
            table_widget_id: table_widget,
            alarms_widget_id: alarms_widget
        },
        "states": {
            "default": {
                "name": "AI Fleet Operations Command Center",
                "root": True,
                "layouts": {
                    "main": {
                        "widgets": {
                            banner_widget_id: {"sizeX": 24, "sizeY": 3, "row": 0, "col": 0},
                            table_widget_id: {"sizeX": 24, "sizeY": 11, "row": 3, "col": 0},
                            alarms_widget_id: {"sizeX": 24, "sizeY": 8, "row": 14, "col": 0}
                        },
                        "gridSettings": {
                            "backgroundColor": "#0e121b",
                            "columns": 24,
                            "margin": 10,
                            "outerMargin": True,
                            "backgroundSizeMode": "100%"
                        }
                    }
                }
            }
        },
        "entityAliases": entity_aliases,
        "filters": {},
        "timewindow": {
            "selectedTab": 0,
            "realtime": {
                "realtimeType": 0,
                "timewindowMs": 86400000
            }
        },
        "settings": {
            "stateControllerId": "entity",
            "showTitle": True,
            "showDashboardsSelect": True,
            "showEntitiesSelect": False,
            "showDashboardTimewindow": True,
            "showDashboardExport": True,
            "dashboardCss": CLOUDFLARE_DASHBOARD_CSS
        }
    }

    return dashboard_config


def update_live_kpi_card(summary: Dict[str, Any], host: str = TB_HOST, token: Optional[str] = None) -> bool:
    """Update just the KPI card banner on the target ThingsBoard dashboard dynamically."""
    try:
        if not token:
            token = get_auth_token()
        headers = {"X-Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.get(f"{host}/api/dashboard/{TARGET_DASHBOARD_ID}", headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Failed to fetch dashboard {TARGET_DASHBOARD_ID}: {resp.status_code}")
            return False
        dash = resp.json()
        cfg = dash.get("configuration", {})
        widgets = cfg.get("widgets", {})
        banner_widget_id = "50263388-7e29-4d37-8cbd-267014732b82"
        if banner_widget_id in widgets:
            new_html = build_modern_kpi_html(summary)
            widgets[banner_widget_id]["config"]["settings"]["cardHtml"] = new_html
            post_resp = requests.post(f"{host}/api/dashboard", json=dash, headers=headers, timeout=10)
            return post_resp.status_code == 200
        return False
    except Exception as e:
        logger.warning(f"Could not update live KPI card: {e}")
        return False


def modernize_dashboard():
    """Main deployment routine to push Cloudflare styling to ThingsBoard."""
    logger.info("Authenticating with ThingsBoard...")
    token = get_auth_token()
    headers = {"X-Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1. Load latest fleet metrics
    if LATEST_DIGEST_JSON.exists():
        logger.info(f"Loading latest fleet risk summary from {LATEST_DIGEST_JSON}...")
        with open(LATEST_DIGEST_JSON, "r", encoding="utf-8") as f:
            digest = json.load(f)
        summary = digest.get("summary", {})
    else:
        logger.warning("No latest_risk_digest.json found; using standard defaults.")
        summary = {
            "total_monitored_branches": 130,
            "fleet_health_score_pct": 55.0,
            "critical_fault_count": 28,
            "high_risk_count": 21,
            "serviced_recovered_count": 4,
            "elevated_count": 19,
            "nominal_count": 58
        }

    # 2. Fetch existing dashboard to preserve metadata
    logger.info(f"Fetching target dashboard {TARGET_DASHBOARD_ID}...")
    resp = requests.get(f"{TB_HOST}/api/dashboard/{TARGET_DASHBOARD_ID}", headers=headers, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not fetch target dashboard: {resp.status_code} {resp.text}")
    dash_obj = resp.json()

    # 3. Apply modern Cloudflare Observability configuration
    new_cfg = build_modern_dashboard_payload(summary)
    dash_obj["title"] = "AI Fleet Operations & Outage Prediction"
    dash_obj["configuration"] = new_cfg

    # 4. Save to ThingsBoard
    logger.info(f"Deploying modernized Cloudflare Observability configuration to {TB_HOST}...")
    save_resp = requests.post(f"{TB_HOST}/api/dashboard", json=dash_obj, headers=headers, timeout=20)
    if save_resp.status_code != 200:
        raise RuntimeError(f"Failed to update dashboard: {save_resp.status_code} {save_resp.text}")

    logger.info("=" * 65)
    logger.info("ThingsBoard Anomaly Detection Dashboard Modernization Complete!")
    logger.info(f"URL: {TB_HOST}/dashboards/{TARGET_DASHBOARD_ID}")
    logger.info("=" * 65)


if __name__ == "__main__":
    modernize_dashboard()

