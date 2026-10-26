"""tests/test_modernize_dashboard.py
Unit tests for the AI Fleet Operations dashboard deployment payload.
"""

import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from scripts.modernize_ai_dashboard import (
    ALARMS_WIDGET_ID,
    BANKS_WIDGET_ID,
    BANNER_WIDGET_ID,
    BANK_BREAKDOWN_JS,
    DASHBOARD_CSS,
    FILTER_ID,
    HISTOGRAM_WIDGET_ID,
    RISK_HISTOGRAM_JS,
    TABLE_WIDGET_ID,
    TARGET_DASHBOARD_ID,
    build_modern_dashboard_payload,
    build_modern_kpi_html,
    update_live_kpi_card,
)

SUMMARY = {
    "timestamp": "2026-10-26T04:06:08.114665+00:00",
    "total_monitored_branches": 130,
    "fleet_health_score_pct": 55.0,
    "critical_fault_count": 28,
    "high_risk_count": 21,
    "serviced_recovered_count": 4,
    "elevated_count": 19,
    "nominal_count": 58,
    "high_outage_risk_branches": 45,
}


def test_kpi_html_metrics_and_light_surface():
    html = build_modern_kpi_html(SUMMARY)
    for text in ("55.0%", ">28<", ">21<", ">19<", ">4<", "62 of 130 branches nominal or restored",
                 "26 Oct 2026, 09:36 IST", "45 branches at or above 50%"):
        assert text in html
    assert "gradient" not in html.lower()
    assert "height:100%" in html  # deck fills its widget, no dead margins


def test_kpi_html_tolerates_minimal_summary():
    html = build_modern_kpi_html({"total_monitored_branches": 10, "fleet_health_score_pct": 80.0, "nominal_count": 8})
    assert "80.0%" in html and "latest run" in html


def test_payload_follows_tb_schema_rules():
    payload = build_modern_dashboard_payload(SUMMARY)
    assert set(payload["widgets"]) == {BANNER_WIDGET_ID, HISTOGRAM_WIDGET_ID, BANKS_WIDGET_ID, TABLE_WIDGET_ID, ALARMS_WIDGET_ID}

    state = payload["states"]["default"]
    assert state["name"] == "default" and state["root"] is True
    assert set(state["layouts"]["main"]["widgets"]) == set(payload["widgets"])

    alarms = payload["widgets"][ALARMS_WIDGET_ID]["config"]
    assert alarms["datasources"] == []
    src = alarms["alarmSource"]
    assert src["type"] == "entity" and src["name"] == "alarms" and src["entityAliasId"] and src["dataKeys"]
    assert alarms["settings"]["defaultSortOrder"] == "-createdTime"
    assert all(alarms["settings"][k] for k in ("allowAcknowledgment", "allowClear", "allowAssign"))

    table = payload["widgets"][TABLE_WIDGET_ID]["config"]
    assert table["settings"]["defaultSortOrder"] == "-ai_outage_risk_prob"
    for key in table["datasources"][0]["dataKeys"]:
        assert key["settings"]["useCellContentFunction"] is True
        assert key["settings"]["useCellStyleFunction"] is False


def test_branch_table_and_alarms_restricted_to_ai_evaluated_devices():
    payload = build_modern_dashboard_payload(SUMMARY)
    for wid in (TABLE_WIDGET_ID, HISTOGRAM_WIDGET_ID, BANKS_WIDGET_ID):
        assert payload["widgets"][wid]["config"]["datasources"][0]["filterId"] == FILTER_ID
    assert payload["widgets"][ALARMS_WIDGET_ID]["config"]["alarmSource"]["filterId"] == FILTER_ID

    kf = payload["filters"][FILTER_ID]["keyFilters"][0]
    assert kf["key"]["key"] == "ai_health_tier"
    pred = kf["predicates"][0]["keyFilterPredicate"]
    assert pred["operation"] == "IN"
    assert set(pred["value"]["defaultValue"].split(",")) == {
        "CRITICAL_FAULT", "HIGH_RISK", "SERVICED_RECOVERED", "ELEVATED", "NOMINAL"}


def test_css_scoped_and_light():
    payload = build_modern_dashboard_payload(SUMMARY)
    assert payload["settings"]["dashboardCss"] == DASHBOARD_CSS
    css = DASHBOARD_CSS.lower()
    assert "body" not in css
    assert "gradient" not in css
    # rows share one explicit light background, so no zebra inversion is possible
    assert ".mat-mdc-row:nth-child(even)" in css and "background: #ffffff" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cell_functions_compile_and_render():
    payload = build_modern_dashboard_payload(SUMMARY)
    keys = payload["widgets"][TABLE_WIDGET_ID]["config"]["datasources"][0]["dataKeys"]
    keys += payload["widgets"][ALARMS_WIDGET_ID]["config"]["alarmSource"]["dataKeys"]
    samples = {
        "name": ["CANARA-KAMARHATI", "Plain", None],
        "ai_health_tier": ["CRITICAL_FAULT", "NOMINAL", "WEIRD", None],
        "ai_outage_risk_prob": [0.9942, 0.31, "0.05", None],
        "ai_primary_issue": ["Low <Phase> Voltage", None],
        "ai_recommended_action": ["Inspect battery", ""],
        "ai_anomaly_score": [0.0442, -0.0232, None],
        "ai_last_evaluated": ["2026-10-26T04:06:08+00:00", "garbage", None],
        "createdTime": [1774072593484, None],
        "originator": ["BOI-BABAI"],
        "type": ["AI_CRITICAL_ANOMALY", "AI_PREDICTIVE_OUTAGE_RISK"],
        "severity": ["CRITICAL", "MAJOR"],
        "status": ["ACTIVE_UNACK", "ACTIVE_ACK"],
    }
    cases = [{"fn": k["settings"]["cellContentFunction"], "values": samples[k["name"]]} for k in keys]
    script = (
        "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "for (const c of cases) { const f = new Function('value', c.fn);"
        " for (const v of c.values) { const out = f(v); if (typeof out !== 'string' || out.includes('undefined'))"
        " { throw new Error('bad render: ' + JSON.stringify(v) + ' -> ' + out); } } }"
    )
    res = subprocess.run(["node", "-e", script], input=json.dumps(cases), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    # HTML in values must be escaped
    issue_fn = next(c["fn"] for c in cases if "-webkit-line-clamp" in c["fn"])
    res = subprocess.run(["node", "-e", f"process.stdout.write(new Function('value', {json.dumps(issue_fn)})('<b>x</b>'))"],
                         capture_output=True, text=True)
    assert "<b>" not in res.stdout and "&lt;b&gt;" in res.stdout


@patch("scripts.modernize_ai_dashboard.requests")
def test_update_live_kpi_card_success(mock_requests):
    fake_dash = {
        "id": {"id": TARGET_DASHBOARD_ID, "entityType": "DASHBOARD"},
        "configuration": {"widgets": {BANNER_WIDGET_ID: {"config": {"settings": {"cardHtml": "old", "cardCss": ""}}}}},
    }
    mock_requests.get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=fake_dash))
    mock_requests.post.return_value = MagicMock(status_code=200)

    summary = dict(SUMMARY, fleet_health_score_pct=58.5, critical_fault_count=25, serviced_recovered_count=7, nominal_count=65)
    assert update_live_kpi_card(summary, host="https://test.cloud", token="dummy-token") is True

    card_html = mock_requests.post.call_args[1]["json"]["configuration"]["widgets"][BANNER_WIDGET_ID]["config"]["settings"]["cardHtml"]
    assert "58.5%" in card_html and ">25<" in card_html and "72 of 130 branches" in card_html


@patch("scripts.modernize_ai_dashboard.requests")
def test_update_live_kpi_card_refuses_other_dashboards(mock_requests):
    other = {"id": {"id": "01041560-2bc2-11f0-ba5e-e55fd4a29280"},
             "configuration": {"widgets": {BANNER_WIDGET_ID: {"config": {"settings": {}}}}}}
    mock_requests.get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=other))
    assert update_live_kpi_card(SUMMARY, host="https://test.cloud", token="t") is False
    mock_requests.post.assert_not_called()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_analytics_functions_render_live_rows():
    rows = [
        {"entityName": "BOI-BABAI", "ai_health_tier": "CRITICAL_FAULT", "ai_outage_risk_prob": "0.95"},
        {"entityName": "BOI-ITARSI", "ai_health_tier": "NOMINAL", "ai_outage_risk_prob": 0.1},
        {"entityName": "CANARA-CHETLA", "ai_health_tier": "HIGH_RISK", "ai_outage_risk_prob": 0.6},
        {"entityName": "Hestia 10", "ai_health_tier": "", "ai_outage_risk_prob": ""},
    ]
    script = (
        "const [fns, rows] = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const out = fns.map(f => new Function('data', 'ctx', f)(rows, {}));"
        "const empty = fns.map(f => new Function('data', 'ctx', f)([], {}));"
        "process.stdout.write(JSON.stringify([out, empty]));"
    )
    res = subprocess.run(["node", "-e", script], input=json.dumps([[RISK_HISTOGRAM_JS, BANK_BREAKDOWN_JS], rows]),
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    (hist, banks), empty = json.loads(res.stdout)
    assert "<b style" in hist and ">2</b> of 3 branches" in hist  # untiered device ignored
    assert ">BOI<" in banks and ">CANARA<" in banks and "OTHER" not in banks
    assert all("Waiting for the next AI evaluation" in e for e in empty)
