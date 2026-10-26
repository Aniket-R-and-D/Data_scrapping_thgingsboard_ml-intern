"""tests/test_modernize_dashboard.py
Unit tests for ThingsBoard Cloudflare Observability Dashboard Modernization.
"""

from unittest.mock import MagicMock, patch
import pytest

from scripts.modernize_ai_dashboard import (
    build_modern_kpi_html,
    build_modern_dashboard_payload,
    update_live_kpi_card,
    CLOUDFLARE_DASHBOARD_CSS,
    TARGET_DASHBOARD_ID
)


def test_build_modern_kpi_html_zero_gradients():
    """Verify KPI HTML adheres strictly to solid surfaces and zero gradients."""
    summary = {
        "total_monitored_branches": 130,
        "fleet_health_score_pct": 55.0,
        "critical_fault_count": 28,
        "high_risk_count": 21,
        "serviced_recovered_count": 4,
        "elevated_count": 19,
        "nominal_count": 58
    }
    html = build_modern_kpi_html(summary)
    
    # Assert metrics appear
    assert "55.0%" in html
    assert "28" in html
    assert "21" in html
    assert "4" in html
    assert "19" in html
    assert "62 of 130 Branches Nominal" in html  # nominal + recovered
    
    # Assert zero gradients (strict requirement)
    assert "linear-gradient" not in html
    assert "radial-gradient" not in html
    assert "gradient" not in html.lower()
    
    # Assert solid surfaces and monospace numbers
    assert "#161b26" in html
    assert "JetBrains Mono" in html


def test_build_modern_dashboard_payload():
    """Verify payload structure meets ThingsBoard PE configuration specifications."""
    summary = {
        "total_monitored_branches": 130,
        "fleet_health_score_pct": 55.0,
        "critical_fault_count": 28,
        "high_risk_count": 21,
        "serviced_recovered_count": 4,
        "elevated_count": 19,
        "nominal_count": 58
    }
    payload = build_modern_dashboard_payload(summary)
    
    assert "widgets" in payload
    assert len(payload["widgets"]) == 3
    
    # 3 widgets check
    banner_id = "50263388-7e29-4d37-8cbd-267014732b82"
    table_id = "3ec22488-34fa-4f2f-838a-ead69b1f3d2a"
    alarms_id = "9ecdb8fd-ed07-476c-8d8b-da14aea0add8"
    assert banner_id in payload["widgets"]
    assert table_id in payload["widgets"]
    assert alarms_id in payload["widgets"]
    
    # Check layout mapping
    grid = payload["states"]["default"]["layouts"]["main"]["widgets"]
    assert banner_id in grid
    assert table_id in grid
    assert alarms_id in grid
    
    # Check CSS injected
    assert payload["settings"]["dashboardCss"] == CLOUDFLARE_DASHBOARD_CSS
    assert "linear-gradient" not in CLOUDFLARE_DASHBOARD_CSS.lower()


@patch("scripts.modernize_ai_dashboard.requests")
def test_update_live_kpi_card_success(mock_requests):
    """Test dynamic KPI banner update with mocked ThingsBoard API."""
    fake_dash = {
        "title": "AI Fleet Operations & Outage Prediction",
        "configuration": {
            "widgets": {
                "50263388-7e29-4d37-8cbd-267014732b82": {
                    "config": {
                        "settings": {
                            "cardHtml": "old",
                            "cardCss": ""
                        }
                    }
                }
            }
        }
    }
    
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = fake_dash
    
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    
    mock_requests.get.return_value = mock_get_resp
    mock_requests.post.return_value = mock_post_resp
    
    summary = {
        "total_monitored_branches": 130,
        "fleet_health_score_pct": 58.5,
        "critical_fault_count": 25,
        "high_risk_count": 18,
        "serviced_recovered_count": 7,
        "elevated_count": 15,
        "nominal_count": 65
    }
    
    res = update_live_kpi_card(summary, host="https://test.cloud", token="dummy-token")
    assert res is True
    
    # Verify post was called with updated HTML
    called_payload = mock_requests.post.call_args[1]["json"]
    card_html = called_payload["configuration"]["widgets"]["50263388-7e29-4d37-8cbd-267014732b82"]["config"]["settings"]["cardHtml"]
    assert "58.5%" in card_html
    assert "25" in card_html
    assert "72 of 130 Branches Nominal" in card_html
