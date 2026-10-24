"""tests/test_alarm_clear_engine.py
Unit tests for ThingsBoard Alarm Clearance Engine using mock HTTP responses.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.live.alarm_clear_engine import (
    get_active_ai_alarms,
    clear_ai_alarm,
    sync_recovery_attributes,
    process_device_recovery_lifecycle
)
from src.live.recovery_gate import RecoveryEvaluation


@pytest.fixture
def dummy_eval():
    return RecoveryEvaluation(
        device_id="dev-123",
        is_recovered=True,
        confidence=1.0,
        status_tier="SERVICED_RECOVERED",
        summary_reason="All vitals normal",
        vitals={"battery_voltage": 13.5, "ac_voltage": 220.0}
    )


def test_get_active_ai_alarms_filtering():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"id": {"id": "alarm-1"}, "type": "AI_CRITICAL_ANOMALY"},
            {"id": {"id": "alarm-2"}, "type": "BATTERY_LOW"},
            {"id": {"id": "alarm-3"}, "type": "AI_PREDICTIVE_OUTAGE_RISK"},
        ]
    }

    with patch("requests.get", return_value=mock_response):
        alarms = get_active_ai_alarms("dev-123", "http://test-tb", "mock-token")
        assert len(alarms) == 2
        alarm_types = [a["type"] for a in alarms]
        assert "AI_CRITICAL_ANOMALY" in alarm_types
        assert "AI_PREDICTIVE_OUTAGE_RISK" in alarm_types
        assert "BATTERY_LOW" not in alarm_types


def test_clear_ai_alarm_success():
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("requests.post", return_value=mock_response):
        assert clear_ai_alarm("alarm-1", "http://test-tb", "mock-token") is True


def test_clear_ai_alarm_failure():
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"

    with patch("requests.post", return_value=mock_response):
        assert clear_ai_alarm("alarm-1", "http://test-tb", "mock-token") is False


def test_process_device_recovery_lifecycle(dummy_eval):
    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.json.return_value = {
        "data": [{"id": {"id": "alarm-ai-1"}, "type": "AI_CRITICAL_ANOMALY"}]
    }

    mock_post = MagicMock()
    mock_post.status_code = 200

    with patch("requests.get", return_value=mock_get), patch("requests.post", return_value=mock_post):
        result = process_device_recovery_lifecycle("dev-123", dummy_eval, "http://test-tb", "mock-token")
        assert result["is_recovered"] is True
        assert result["cleared_alarms_count"] == 1
        assert "alarm-ai-1" in result["cleared_alarm_ids"]
