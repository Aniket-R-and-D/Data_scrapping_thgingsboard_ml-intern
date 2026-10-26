"""scripts/modernize_ai_dashboard.py
Deploy the SEPLE NovaEdge "AI Fleet Operations & Outage Prediction" dashboard.

Unified light theme that sits inside the light ThingsBoard PE shell:
- One KPI deck card: fleet health index, tier distribution bar, four tier counts.
- Monitored Branches table restricted by a dashboard key filter to devices whose
  ai_health_tier is set (the 130 AI-evaluated branches), sorted by 24h outage risk.
- Active AI incident console (AI_CRITICAL_ANOMALY / AI_PREDICTIVE_OUTAGE_RISK) with
  acknowledge / clear / assign actions.

Safety: only TARGET_DASHBOARD_ID is ever read or written.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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

ALIAS_ID = "256ad357-53ba-458f-a3aa-2ec9b040f001"
FILTER_ID = "7c1e5a40-2f6b-4b8e-9d3a-a1f0c2e4b001"
BANNER_WIDGET_ID = "50263388-7e29-4d37-8cbd-267014732b82"
TABLE_WIDGET_ID = "3ec22488-34fa-4f2f-838a-ead69b1f3d2a"
ALARMS_WIDGET_ID = "9ecdb8fd-ed07-476c-8d8b-da14aea0add8"
HISTOGRAM_WIDGET_ID = "b3d1f6a2-5c47-4e8a-9f21-6a0c3e7d1b01"
BANKS_WIDGET_ID = "c84e2b19-7a3d-4f65-8e0b-2d9f5a6c1b02"

IST = timezone(timedelta(hours=5, minutes=30))

# Palette: cool neutrals tinted toward ink navy; tier colors tuned for >=4.5:1 text on their tint.
INK = "#0f1b2d"
INK_2 = "#475569"
INK_3 = "#64748b"
MUTED = "#94a3b8"
CANVAS = "#eef2f8"
BRAND = "#1f3a93"  # SEPLE royal blue
BRAND_DEEP = "#14245f"
BRAND_TINT = "#eef2fc"
SURFACE = "#ffffff"
HAIRLINE = "#e3e8ef"
DIVIDER = "#edf1f5"
FONT = "Roboto, Helvetica Neue, Arial, sans-serif"  # unquoted: also embedded in JS strings

# key -> (label, action horizon, fg, bg, border, dot)
TIERS: Dict[str, tuple] = {
    "CRITICAL_FAULT": ("Critical fault", "Dispatch now", "#b4232c", "#fdecec", "#f5c2c4", "#dc2626"),
    "HIGH_RISK": ("High risk", "Within 24 h", "#9a4d00", "#fff4e5", "#f7d7a8", "#ea8a0c"),
    "ELEVATED": ("Elevated drift", "Within 7 days", "#1d4ed8", "#eef4ff", "#c9dafc", "#3b6fe0"),
    "SERVICED_RECOVERED": ("Serviced", "Verified restored", "#0f7a55", "#e8f7f0", "#b9e5d2", "#16a36f"),
    "NOMINAL": ("Nominal", "No action", "#475569", "#f1f4f8", "#dfe5ec", "#94a3b8"),
}
TIER_KEYS_CSV = ",".join(TIERS)

CARD_STYLE = {
    "border": f"1px solid {HAIRLINE}",
    "borderRadius": "12px",
    "boxShadow": "0 1px 2px rgba(15,27,45,0.04), 0 4px 12px rgba(15,27,45,0.04)",
    "overflow": "hidden",
}

# TB 4 tables take their header from the widget title; without it the toolbar icons float over the column headers.
TITLE_CONFIG = {
    "showTitle": True,
    "showTitleIcon": False,
    "titleFont": {"size": 15, "sizeUnit": "px", "family": "Roboto", "weight": "600", "style": "normal", "lineHeight": "24px"},
    "titleColor": BRAND_DEEP,
    "titleStyle": {"fontSize": "15px", "fontWeight": 600, "color": BRAND_DEEP, "padding": "4px 8px"},
    "displayTimewindow": False,
}

# ThingsBoard namespaces dashboardCss under the dashboard root, so every rule here
# only reaches widget internals. Rows get one explicit background (no zebra).
DASHBOARD_CSS = f"""
.mat-mdc-table, table.mat-mdc-table {{
    background: {SURFACE} !important;
    font-family: {FONT};
}}
.mat-mdc-header-row, .mat-mdc-header-cell {{
    background: {BRAND_TINT} !important;
}}
.mat-mdc-header-cell {{
    color: {BRAND_DEEP} !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em;
    border-bottom: 1px solid {HAIRLINE} !important;
}}
.mat-mdc-row, .mat-mdc-row:nth-child(even), .mat-mdc-row:nth-child(odd) {{
    background: {SURFACE} !important;
}}
.mat-mdc-row:hover {{
    background: #f4f7fe !important;
}}
.mat-mdc-cell {{
    color: {INK} !important;
    font-size: 13px !important;
    border-bottom: 1px solid {DIVIDER} !important;
    font-variant-numeric: tabular-nums;
    padding-top: 10px !important;
    padding-bottom: 10px !important;
}}
.mat-mdc-paginator {{
    background: {SURFACE} !important;
    color: {INK_2} !important;
    border-top: 1px solid {HAIRLINE};
}}
* {{
    scrollbar-width: thin;
    scrollbar-color: #c3ccd8 transparent;
}}
::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: #c3ccd8; border-radius: 4px; }}
::-webkit-scrollbar-thumb:hover {{ background: #a5b1c2; }}
.widget-type-system-cards-markdown_card, .widget-type-system-cards-markdown_card .tb-absolute-fill {{
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
}}
::selection {{ background: #d6e4ff; color: {INK}; }}
"""

# --- Cell renderers (each body runs standalone inside ThingsBoard) ------------------

_JS_ESC = (
    "var esc = function (s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
    ".replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); };\n"
)
_JS_EMPTY = f"'<span style=\"color:{MUTED};\">&mdash;</span>'"
_JS_PILL = (
    "var pill = function (t) { return '<span style=\"display:inline-flex;align-items:center;gap:6px;"
    "padding:3px 10px 3px 8px;border-radius:999px;font-size:12px;font-weight:600;line-height:16px;"
    "white-space:nowrap;color:' + t[2] + ';background:' + t[3] + ';border:1px solid ' + t[4] + ';\">"
    "<span style=\"width:6px;height:6px;border-radius:50%;background:' + t[5] + ';\"></span>' + t[0] + '</span>'; };\n"
)
_JS_WHEN = (
    "var when = function (v) {\n"
    "  var d = new Date(isNaN(+v) ? v : +v);\n"
    f"  if (!v || isNaN(d.getTime())) return {_JS_EMPTY};\n"
    "  var mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];\n"
    "  var p = function (n) { return (n < 10 ? '0' : '') + n; };\n"
    "  var mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));\n"
    "  var rel = mins < 1 ? 'just now' : mins < 60 ? mins + ' min ago' : mins < 1440 ? Math.round(mins / 60) + ' h ago' : Math.round(mins / 1440) + ' d ago';\n"
    f"  return '<div style=\"line-height:18px;\"><div style=\"color:{INK};\">' + d.getDate() + ' ' + mo + ', ' + p(d.getHours()) + ':' + p(d.getMinutes()) + '</div>'"
    f" + '<div style=\"color:{INK_3};font-size:12px;\">' + rel + '</div></div>';\n"
    "};\n"
)
_JS_BRANCH = (
    "var i = String(value || '').indexOf('-');\n"
    f"if (!value) return {_JS_EMPTY};\n"
    f"if (i < 1) return '<span style=\"font-weight:600;color:{INK};\">' + esc(value) + '</span>';\n"
    f"return '<span style=\"display:inline-block;min-width:46px;margin-right:8px;padding:1px 6px;border-radius:4px;"
    f"font-size:11px;font-weight:600;letter-spacing:0.02em;text-align:center;color:{INK_2};background:#f1f4f8;\">' + esc(value.slice(0, i)) + '</span>'"
    f" + '<span style=\"font-weight:600;color:{INK};\">' + esc(value.slice(i + 1)) + '</span>';\n"
)


def _cell(body: str) -> str:
    return _JS_ESC + body


TIER_CELL_JS = _cell(
    _JS_PILL
    + f"var tiers = {json.dumps(TIERS)};\n"
    + f"if (!value) return {_JS_EMPTY};\n"
    + "return pill(tiers[value] || [esc(value), '', '#475569', '#f1f4f8', '#dfe5ec', '#94a3b8']);\n"
)

RISK_CELL_JS = _cell(
    f"if (value === undefined || value === null || value === '') return {_JS_EMPTY};\n"
    "var p = Math.max(0, Math.min(1, parseFloat(value)));\n"
    "var c = p >= 0.8 ? '#c42b33' : p >= 0.5 ? '#b35c00' : p >= 0.25 ? '#2559c9' : '#64748b';\n"
    "var b = p >= 0.8 ? '#dc2626' : p >= 0.5 ? '#ea8a0c' : p >= 0.25 ? '#3b6fe0' : '#b8c2cf';\n"
    "return '<div style=\"display:flex;align-items:center;gap:10px;\">'"
    " + '<span style=\"min-width:46px;text-align:right;font-weight:600;font-variant-numeric:tabular-nums;color:' + c + ';\">' + (p * 100).toFixed(1) + '%</span>'"
    f" + '<span style=\"position:relative;width:64px;height:6px;border-radius:3px;background:{DIVIDER};overflow:hidden;\">'"
    " + '<span style=\"position:absolute;left:0;top:0;bottom:0;width:' + (p * 100).toFixed(1) + '%;background:' + b + ';border-radius:3px;\"></span></span></div>';\n"
)

ANOMALY_CELL_JS = _cell(
    f"if (value === undefined || value === null || value === '') return {_JS_EMPTY};\n"
    "var s = parseFloat(value);\n"
    f"return '<span style=\"font-variant-numeric:tabular-nums;color:' + (s > 0 ? '{INK}' : '{INK_3}') + ';\">' + (s > 0 ? '+' : '') + s.toFixed(3) + '</span>';\n"
)


def _clamped_text_js(color: str) -> str:
    return _cell(
        f"if (!value) return {_JS_EMPTY};\n"
        "return '<span title=\"' + esc(value) + '\" style=\"display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;"
        f"overflow:hidden;white-space:normal;line-height:18px;color:{color};\">' + esc(value) + '</span>';\n"
    )


BRANCH_CELL_JS = _cell(_JS_BRANCH)
WHEN_CELL_JS = _cell(_JS_WHEN + "return when(value);\n")

ALARM_TYPE_CELL_JS = _cell(
    "var m = { AI_CRITICAL_ANOMALY: ['Critical anomaly', 'Ongoing severe fault'],"
    " AI_PREDICTIVE_OUTAGE_RISK: ['Predicted outage', '24 h outage risk at or above 50%'] };\n"
    "var t = m[value] || [esc(value || ''), ''];\n"
    f"return '<div style=\"line-height:18px;\"><div style=\"font-weight:600;color:{INK};\">' + t[0] + '</div>'"
    f" + (t[1] ? '<div style=\"font-size:12px;color:{INK_3};\">' + t[1] + '</div>' : '') + '</div>';\n"
)

SEVERITY_CELL_JS = _cell(
    _JS_PILL
    + "var m = { CRITICAL: ['Critical', '', '#b4232c', '#fdecec', '#f5c2c4', '#dc2626'],"
    " MAJOR: ['Major', '', '#9a4d00', '#fff4e5', '#f7d7a8', '#ea8a0c'],"
    " MINOR: ['Minor', '', '#1d4ed8', '#eef4ff', '#c9dafc', '#3b6fe0'],"
    " WARNING: ['Warning', '', '#475569', '#f1f4f8', '#dfe5ec', '#94a3b8'] };\n"
    + "return pill(m[value] || [esc(value || ''), '', '#475569', '#f1f4f8', '#dfe5ec', '#94a3b8']);\n"
)

STATUS_CELL_JS = _cell(
    "var m = { ACTIVE_UNACK: ['Needs acknowledgement', '#9a4d00', '#ea8a0c'],"
    " ACTIVE_ACK: ['Acknowledged', '#475569', '#94a3b8'],"
    " CLEARED_UNACK: ['Cleared', '#0f7a55', '#16a36f'], CLEARED_ACK: ['Cleared', '#0f7a55', '#16a36f'] };\n"
    "var s = m[value] || [esc(value || ''), '#475569', '#94a3b8'];\n"
    "return '<span style=\"display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;white-space:nowrap;color:' + s[1] + ';\">'"
    " + '<span style=\"width:6px;height:6px;border-radius:50%;background:' + s[2] + ';\"></span>' + s[0] + '</span>';\n"
)


_JS_ROWS = (
    "var rows = (data || []).filter(function (r) { return r && r.ai_health_tier; });\n"
    "var num = function (v) { var x = parseFloat(v); return isNaN(x) ? 0 : x; };\n"
    f"if (!rows.length) return '<div style=\"height:100%;display:flex;align-items:center;justify-content:center;color:{INK_3};font-size:13px;\">Waiting for the next AI evaluation&hellip;</div>';\n"
)

RISK_HISTOGRAM_JS = _JS_ROWS + (
    "var b = [['Below 25%', 0, 0.25, '#94a3b8'], ['25 to 50%', 0.25, 0.5, '#3b6fe0'], ['50 to 75%', 0.5, 0.75, '#ea8a0c'],"
    " ['75 to 90%', 0.75, 0.9, '#e35d1f'], ['90% and up', 0.9, 1.01, '#dc2626']];\n"
    "var c = b.map(function (x) { return rows.filter(function (r) { var p = num(r.ai_outage_risk_prob); return p >= x[1] && p < x[2]; }).length; });\n"
    "var max = Math.max.apply(null, c.concat([1]));\n"
    "var hi = c[2] + c[3] + c[4];\n"
    "var cols = b.map(function (x, i) { return '<div style=\"flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:6px;min-width:0;height:100%;\">'"
    f" + '<span style=\"font-size:15px;font-weight:600;color:{INK};font-variant-numeric:tabular-nums;\">' + c[i] + '</span>'"
    " + '<span style=\"width:100%;max-width:72px;height:' + Math.max(3, Math.round(c[i] / max * 70)) + '%;border-radius:6px 6px 2px 2px;background:' + x[3] + ';\"></span>'"
    f" + '<span style=\"font-size:11px;color:{INK_3};white-space:nowrap;\">' + x[0] + '</span></div>'; }}).join('');\n"
    f"return '<div style=\"box-sizing:border-box;height:100%;display:flex;flex-direction:column;gap:12px;padding:4px 12px 10px;font-family:{FONT};\">'"
    f" + '<div style=\"font-size:13px;color:{INK_2};\"><b style=\"color:#b4232c;font-size:15px;\">' + hi + '</b> of ' + rows.length + ' branches at or above 50% predicted outage risk in the next 24 h</div>'"
    " + '<div style=\"flex:1;min-height:0;display:flex;align-items:stretch;gap:14px;\">' + cols + '</div></div>';\n"
)

BANK_BREAKDOWN_JS = _JS_ROWS + (
    f"var order = {json.dumps(list(TIERS))};\n"
    f"var colors = {json.dumps({k: v[5] for k, v in TIERS.items()})};\n"
    "var banks = {};\n"
    "rows.forEach(function (r) { var n = String(r.entityName || r.name || ''); var i = n.indexOf('-');"
    " var k = i > 0 ? n.slice(0, i).toUpperCase() : 'OTHER'; var g = banks[k] = banks[k] || { n: 0, risk: 0, t: {} };"
    " g.n++; g.risk += num(r.ai_outage_risk_prob); g.t[r.ai_health_tier] = (g.t[r.ai_health_tier] || 0) + 1; });\n"
    "var list = Object.keys(banks).map(function (k) { return [k, banks[k]]; }).sort(function (a, b) { return b[1].n - a[1].n; });\n"
    "var esc = function (s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); };\n"
    "var grid = 'display:grid;grid-template-columns:72px 44px minmax(80px,1fr) 64px 56px;align-items:center;gap:12px;';\n"
    "var body = list.map(function (e) { var g = e[1]; var avg = g.risk / g.n; var crit = g.t.CRITICAL_FAULT || 0;"
    " var seg = order.filter(function (t) { return g.t[t]; }).map(function (t) { return '<span title=\"' + t + ': ' + g.t[t] + '\" style=\"flex:' + g.t[t] + ' 1 0;min-width:3px;background:' + colors[t] + ';\"></span>'; }).join('');"
    f" return '<div style=\"' + grid + 'padding:7px 0;border-bottom:1px solid {DIVIDER};\">'"
    f" + '<span style=\"font-weight:600;color:{INK};font-size:13px;\">' + esc(e[0]) + '</span>'"
    f" + '<span style=\"font-size:13px;color:{INK_2};text-align:right;font-variant-numeric:tabular-nums;\">' + g.n + '</span>'"
    " + '<span style=\"display:flex;gap:2px;height:10px;border-radius:5px;overflow:hidden;\">' + seg + '</span>'"
    f" + '<span style=\"font-size:13px;text-align:right;font-variant-numeric:tabular-nums;font-weight:600;color:' + (avg >= 0.5 ? '#b35c00' : '{INK_2}') + ';\">' + (avg * 100).toFixed(0) + '%</span>'"
    f" + '<span style=\"font-size:13px;text-align:right;font-variant-numeric:tabular-nums;color:' + (crit ? '#b4232c' : '{MUTED}') + ';font-weight:' + (crit ? 600 : 400) + ';\">' + crit + '</span></div>'; }}).join('');\n"
    f"var head = '<div style=\"' + grid + 'padding:0 0 6px;font-size:11px;font-weight:600;color:{BRAND_DEEP};border-bottom:1px solid {HAIRLINE};\">'"
    " + '<span>Bank</span><span style=\"text-align:right;\">Sites</span><span>Tier mix</span><span style=\"text-align:right;\">Avg risk</span><span style=\"text-align:right;\">Critical</span></div>';\n"
    f"return '<div style=\"box-sizing:border-box;height:100%;overflow:auto;padding:4px 12px 10px;font-family:{FONT};\">' + head + body + '</div>';\n"
)


def _analytics_widget(widget_id: str, title: str, col: int, js: str) -> Dict[str, Any]:
    """Markdown card fed by the same alias + tier filter; the function renders live HTML from latest attributes."""
    return {
        "id": widget_id,
        "typeFullFqn": "system.cards.markdown_card",
        "type": "latest",
        "title": title,
        "sizeX": 12,
        "sizeY": 5,
        "row": 3,
        "col": col,
        "config": {
            **TITLE_CONFIG,
            "title": title,
            "backgroundColor": SURFACE,
            "color": INK,
            "padding": "4px 4px 0 4px",
            "dropShadow": False,
            "enableFullscreen": True,
            "widgetStyle": CARD_STYLE,
            "mobileHeight": 6,
            "datasources": [
                {
                    "type": "entity",
                    "name": None,
                    "entityAliasId": ALIAS_ID,
                    "filterId": FILTER_ID,
                    "dataKeys": [
                        {"name": k, "type": "attribute", "label": k, "color": INK, "settings": {}}
                        for k in ("ai_health_tier", "ai_outage_risk_prob")
                    ]
                }
            ],
            "settings": {
                "useMarkdownTextFunction": True,
                "markdownTextFunction": js,
                "markdownTextPattern": "",
                "applyDefaultMarkdownStyle": False,
                "markdownCss": ""
            }
        }
    }


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


def _format_run_time(ts: Optional[str]) -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(IST)
    except (TypeError, ValueError):
        return "latest run"
    return f"{dt.day} {dt:%b %Y}, {dt:%H:%M} IST"


def build_modern_kpi_html(summary: Dict[str, Any]) -> str:
    """KPI deck: navy fleet-health panel plus one tinted card per actionable tier."""
    total = int(summary.get("total_monitored_branches", 130)) or 1
    health = float(summary.get("fleet_health_score_pct", 55.0))
    counts = {
        "CRITICAL_FAULT": int(summary.get("critical_fault_count", 0)),
        "HIGH_RISK": int(summary.get("high_risk_count", 0)),
        "ELEVATED": int(summary.get("elevated_count", 0)),
        "SERVICED_RECOVERED": int(summary.get("serviced_recovered_count", 0)),
        "NOMINAL": int(summary.get("nominal_count", 0)),
    }
    healthy = counts["NOMINAL"] + counts["SERVICED_RECOVERED"]
    risk_50 = summary.get("high_outage_risk_branches")
    run_time = _format_run_time(summary.get("timestamp"))

    bar = "".join(
        f'<span title="{TIERS[k][0]}: {n}" style="flex:{n} 1 0;min-width:4px;background:{TIERS[k][5]};"></span>'
        for k, n in counts.items() if n
    )
    legend = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;white-space:nowrap;">'
        f'<span style="width:8px;height:8px;border-radius:2px;background:{TIERS[k][5]};"></span>{TIERS[k][0]} {n}</span>'
        for k, n in counts.items()
    )
    footer = f"Model run {run_time}"
    if risk_50 is not None:
        footer += f" &middot; {int(risk_50)} branches at or above 50% 24 h outage risk"

    card = "box-sizing:border-box;border-radius:12px;box-shadow:0 1px 2px rgba(15,27,45,0.05),0 6px 16px rgba(15,27,45,0.06);overflow:hidden;"
    tiles = ""
    for k in ("CRITICAL_FAULT", "HIGH_RISK", "ELEVATED", "SERVICED_RECOVERED"):
        label, horizon, fg, bg, border, dot = TIERS[k]
        n = counts[k]
        tiles += f"""
  <div style="{card}flex:1 1 160px;min-width:160px;display:flex;flex-direction:column;justify-content:center;gap:6px;padding:14px 20px;background:{bg};border:1px solid {border};border-top:4px solid {dot};">
    <span style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
      <span style="font-size:13px;font-weight:600;color:{fg};">{label}</span>
      <span style="padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;color:{fg};background:{SURFACE};border:1px solid {border};white-space:nowrap;">{horizon}</span>
    </span>
    <span style="font-size:38px;font-weight:600;line-height:44px;letter-spacing:-0.02em;color:{fg};font-variant-numeric:tabular-nums;">{n}</span>
    <span style="font-size:12px;line-height:16px;color:{fg};">{n / total * 100:.1f}% of {total} branches</span>
  </div>"""

    return f"""<div style="box-sizing:border-box;height:100%;display:flex;flex-wrap:wrap;gap:12px;align-items:stretch;font-family:{FONT};color:{INK};">
  <div style="{card}flex:1.7 1 320px;min-width:300px;display:flex;flex-direction:column;justify-content:center;gap:8px;padding:14px 24px;background:{BRAND_DEEP};color:#ffffff;">
    <span style="font-size:13px;font-weight:600;color:#c7d2fe;">Fleet health index</span>
    <span style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">
      <span style="font-size:42px;font-weight:600;line-height:46px;letter-spacing:-0.02em;font-variant-numeric:tabular-nums;">{health:.1f}%</span>
      <span style="font-size:13px;color:#dbe3ff;">{healthy} of {total} branches nominal or restored</span>
    </span>
    <span style="display:flex;gap:2px;height:10px;border-radius:5px;overflow:hidden;background:rgba(255,255,255,0.15);">{bar}</span>
    <span style="display:flex;flex-wrap:wrap;gap:6px 14px;font-size:11px;color:#dbe3ff;">{legend}</span>
    <span style="font-size:11px;color:#a5b4fc;">{footer}</span>
  </div>{tiles}
</div>"""


def _key(name: str, key_type: str, label: str, width: str, js: str) -> Dict[str, Any]:
    return {
        "name": name,
        "type": key_type,
        "label": label,
        "color": INK,
        "settings": {
            "columnWidth": width,
            "useCellStyleFunction": False,
            "useCellContentFunction": True,
            "cellContentFunction": js,
        },
    }


def build_modern_dashboard_payload(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the full ThingsBoard dashboard configuration."""
    banner_widget = {
        "id": BANNER_WIDGET_ID,
        "typeFullFqn": "system.cards.html_card",
        "type": "static",
        "title": "Fleet health",
        "sizeX": 24,
        "sizeY": 3,
        "row": 0,
        "col": 0,
        "config": {
            "showTitle": False,
            "backgroundColor": "transparent",
            "color": INK,
            "padding": "0px",
            "dropShadow": False,
            "enableFullscreen": False,
            "mobileHeight": 7,
            "datasources": [
                {
                    "type": "static",
                    "name": "function",
                    "dataKeys": [
                        {
                            "name": "f(x)",
                            "type": "function",
                            "label": "kpi",
                            "color": INK,
                            "settings": {},
                            "funcBody": "return true;"
                        }
                    ]
                }
            ],
            "settings": {
                "cardHtml": build_modern_kpi_html(summary),
                "cardCss": ""
            }
        }
    }

    table_widget = {
        "id": TABLE_WIDGET_ID,
        "typeFullFqn": "system.cards.entities_table",
        "type": "latest",
        "title": "Monitored branches · ranked by 24 h outage risk",
        "sizeX": 24,
        "sizeY": 10,
        "row": 8,
        "col": 0,
        "config": {
            **TITLE_CONFIG,
            "backgroundColor": SURFACE,
            "color": INK,
            "padding": "4px 0 0 0",
            "dropShadow": False,
            "enableFullscreen": True,
            "widgetStyle": CARD_STYLE,
            "settings": {
                "entitiesTitle": "Monitored branches, ranked by 24 h outage risk",
                "enableSearch": True,
                "enableSelectColumnDisplay": True,
                "enableStickyHeader": True,
                "enableStickyAction": False,
                "displayEntityName": False,
                "displayEntityLabel": False,
                "displayEntityType": False,
                "displayPagination": True,
                "defaultPageSize": 10,
                "pageStepIncrement": 10,
                "defaultSortOrder": "-ai_outage_risk_prob",
                "useRowStyleFunction": False
            },
            "datasources": [
                {
                    "type": "entity",
                    "name": None,
                    "entityAliasId": ALIAS_ID,
                    "filterId": FILTER_ID,
                    "dataKeys": [
                        _key("name", "entityField", "Branch", "200px", BRANCH_CELL_JS),
                        _key("ai_health_tier", "attribute", "Health", "150px", TIER_CELL_JS),
                        _key("ai_outage_risk_prob", "attribute", "24 h outage risk", "150px", RISK_CELL_JS),
                        _key("ai_primary_issue", "attribute", "Root cause", "28%", _clamped_text_js(INK)),
                        _key("ai_recommended_action", "attribute", "Recommended action", "28%", _clamped_text_js(INK_2)),
                        _key("ai_anomaly_score", "attribute", "Anomaly score", "110px", ANOMALY_CELL_JS),
                        _key("ai_last_evaluated", "attribute", "Last evaluated", "120px", WHEN_CELL_JS),
                    ]
                }
            ]
        }
    }

    alarm_filter = {
        "statusList": ["ACTIVE"],
        "severityList": ["CRITICAL", "MAJOR", "MINOR", "WARNING"],
        "typeList": ["AI_CRITICAL_ANOMALY", "AI_PREDICTIVE_OUTAGE_RISK"],
        "searchPropagatedAlarms": False
    }

    alarms_widget = {
        "id": ALARMS_WIDGET_ID,
        "typeFullFqn": "system.alarm_widgets.alarms_table",
        "type": "alarm",
        "title": "Active AI incidents",
        "sizeX": 24,
        "sizeY": 9,
        "row": 18,
        "col": 0,
        "config": {
            "timewindow": {
                "selectedTab": 0,
                "realtime": {
                    "realtimeType": 0,
                    "timewindowMs": 2592000000
                }
            },
            "useDashboardTimewindow": False,
            **TITLE_CONFIG,
            "backgroundColor": SURFACE,
            "color": INK,
            "padding": "4px 0 0 0",
            "dropShadow": False,
            "enableFullscreen": True,
            "widgetStyle": CARD_STYLE,
            "showLegend": False,
            "alarmFilterConfig": alarm_filter,
            "alarmSearchStatus": "ACTIVE",
            "alarmTypeList": alarm_filter["typeList"],
            "alarmSeverityList": alarm_filter["severityList"],
            "settings": {
                "alarmsTitle": "Active AI incidents",
                "enableSelection": True,
                "enableSearch": True,
                "enableFilter": True,
                "enableSelectColumnDisplay": True,
                "enableStickyHeader": True,
                "enableStickyAction": True,
                "displayDetails": True,
                "allowAcknowledgment": True,
                "allowClear": True,
                "allowAssign": True,
                "displayActivity": False,
                "displayPagination": True,
                "defaultPageSize": 10,
                "pageStepIncrement": 10,
                "defaultSortOrder": "-createdTime"
            },
            "alarmSource": {
                "type": "entity",
                "name": "alarms",
                "entityAliasId": ALIAS_ID,
                "filterId": FILTER_ID,
                "dataKeys": [
                    _key("createdTime", "alarm", "Raised", "140px", WHEN_CELL_JS),
                    _key("originator", "alarm", "Branch", "220px", BRANCH_CELL_JS),
                    _key("type", "alarm", "Incident", "260px", ALARM_TYPE_CELL_JS),
                    _key("severity", "alarm", "Severity", "130px", SEVERITY_CELL_JS),
                    _key("status", "alarm", "Status", "200px", STATUS_CELL_JS),
                ]
            },
            "datasources": []
        }
    }

    tier_filter = {
        "id": FILTER_ID,
        "filter": "AI-evaluated branches",
        "editable": False,
        # No null fields here: TB websocket parser rejects them ("Failed to parse the payload").
        "keyFilters": [
            {
                "key": {"type": "ATTRIBUTE", "key": "ai_health_tier"},
                "valueType": "STRING",
                "predicates": [
                    {
                        "keyFilterPredicate": {
                            "type": "STRING",
                            "operation": "IN",
                            "value": {"defaultValue": TIER_KEYS_CSV},
                            "ignoreCase": False
                        },
                        "userInfo": {"editable": False, "label": "", "autogeneratedLabel": True, "order": 0}
                    }
                ]
            }
        ]
    }

    return {
        "description": "AI fleet health, 24 h outage prediction and active AI incidents for SEPLE-monitored bank branches",
        "widgets": {
            BANNER_WIDGET_ID: banner_widget,
            TABLE_WIDGET_ID: table_widget,
            ALARMS_WIDGET_ID: alarms_widget,
            HISTOGRAM_WIDGET_ID: _analytics_widget(HISTOGRAM_WIDGET_ID, "24 h outage risk distribution", 0, RISK_HISTOGRAM_JS),
            BANKS_WIDGET_ID: _analytics_widget(BANKS_WIDGET_ID, "Risk by bank", 12, BANK_BREAKDOWN_JS)
        },
        "states": {
            "default": {
                "name": "default",
                "root": True,
                "layouts": {
                    "main": {
                        "widgets": {
                            BANNER_WIDGET_ID: {"sizeX": 24, "sizeY": 3, "row": 0, "col": 0, "mobileHeight": 7},
                            HISTOGRAM_WIDGET_ID: {"sizeX": 12, "sizeY": 5, "row": 3, "col": 0, "mobileHeight": 6},
                            BANKS_WIDGET_ID: {"sizeX": 12, "sizeY": 5, "row": 3, "col": 12, "mobileHeight": 6},
                            TABLE_WIDGET_ID: {"sizeX": 24, "sizeY": 10, "row": 8, "col": 0},
                            ALARMS_WIDGET_ID: {"sizeX": 24, "sizeY": 9, "row": 18, "col": 0}
                        },
                        "gridSettings": {
                            "layoutType": "default",
                            "backgroundColor": CANVAS,
                            "columns": 24,
                            "margin": 12,
                            "outerMargin": True,
                            "backgroundSizeMode": "100%",
                            "autoFillHeight": False,
                            "mobileAutoFillHeight": False,
                            "mobileRowHeight": 70
                        }
                    }
                }
            }
        },
        # ponytail: alias takes every device; the key filter narrows it to devices the AI engine evaluated.
        "entityAliases": {
            ALIAS_ID: {
                "id": ALIAS_ID,
                "alias": "AI-monitored bank branches",
                "filter": {
                    "type": "entityType",
                    "resolveMultiple": True,
                    "entityType": "DEVICE"
                }
            }
        },
        "filters": {FILTER_ID: tier_filter},
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
            "showFilters": False,
            "showDashboardTimewindow": False,
            "showDashboardExport": True,
            "showUpdateDashboardImage": False,
            "dashboardCss": DASHBOARD_CSS
        }
    }


def _assert_target(dash: Dict[str, Any]) -> None:
    dash_id = (dash.get("id") or {}).get("id")
    if dash_id != TARGET_DASHBOARD_ID:
        raise RuntimeError(f"Refusing to write dashboard {dash_id!r}; only {TARGET_DASHBOARD_ID} is allowed.")


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
        _assert_target(dash)
        widgets = dash.get("configuration", {}).get("widgets", {})
        if BANNER_WIDGET_ID in widgets:
            widgets[BANNER_WIDGET_ID]["config"]["settings"]["cardHtml"] = build_modern_kpi_html(summary)
            post_resp = requests.post(f"{host}/api/dashboard", json=dash, headers=headers, timeout=10)
            return post_resp.status_code == 200
        return False
    except Exception as e:
        logger.warning(f"Could not update live KPI card: {e}")
        return False


def modernize_dashboard():
    """Deploy the full dashboard configuration to TARGET_DASHBOARD_ID."""
    logger.info("Authenticating with ThingsBoard...")
    token = get_auth_token()
    headers = {"X-Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if LATEST_DIGEST_JSON.exists():
        logger.info(f"Loading latest fleet risk summary from {LATEST_DIGEST_JSON}...")
        with open(LATEST_DIGEST_JSON, "r", encoding="utf-8") as f:
            summary = json.load(f).get("summary", {})
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

    logger.info(f"Fetching target dashboard {TARGET_DASHBOARD_ID}...")
    resp = requests.get(f"{TB_HOST}/api/dashboard/{TARGET_DASHBOARD_ID}", headers=headers, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not fetch target dashboard: {resp.status_code} {resp.text}")
    dash_obj = resp.json()
    _assert_target(dash_obj)

    dash_obj["title"] = "AI Fleet Operations & Outage Prediction"
    dash_obj["configuration"] = build_modern_dashboard_payload(summary)

    logger.info(f"Deploying dashboard configuration to {TB_HOST}...")
    save_resp = requests.post(f"{TB_HOST}/api/dashboard", json=dash_obj, headers=headers, timeout=20)
    if save_resp.status_code != 200:
        raise RuntimeError(f"Failed to update dashboard: {save_resp.status_code} {save_resp.text}")

    logger.info(f"Deployed: {TB_HOST}/dashboards/{TARGET_DASHBOARD_ID}")


if __name__ == "__main__":
    modernize_dashboard()
