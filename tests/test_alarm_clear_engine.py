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


def test_evaluate_debounce_gate_cycle_progression():
    from datetime import datetime, timezone, timedelta
    from src.live.alarm_clear_engine import (
        evaluate_debounce_gate,
        _empty_debounce_state,
        STATUS_PENDING,
        STATUS_CONFIRMED,
        STATUS_RESET
    )

    state = _empty_debounce_state()
    dev_id = "branch-test-1"
    t0 = datetime(2026, 10, 27, 8, 0, 0, tzinfo=timezone.utc)

    eval_nominal = RecoveryEvaluation(
        device_id=dev_id,
        is_recovered=True,
        confidence=1.0,
        status_tier="SERVICED_RECOVERED",
        summary_reason="Telemetry nominal",
        vitals={"battery_voltage": 12.8, "ac_voltage": 225.0, "mains_on": True}
    )

    # Cycle 1
    d1 = evaluate_debounce_gate(state, dev_id, eval_nominal, now=t0)
    assert d1.consecutive_nominal_cycles == 1
    assert d1.status == STATUS_PENDING
    assert d1.eligible_for_clear is False

    # Cycle 2 (+15 mins)
    t1 = t0 + timedelta(minutes=15)
    d2 = evaluate_debounce_gate(state, dev_id, eval_nominal, now=t1)
    assert d2.consecutive_nominal_cycles == 2
    assert d2.status == STATUS_PENDING
    assert d2.eligible_for_clear is False

    # Cycle 3 (+30 mins from t0, satisfying required 3 cycles and 30 min min_elapsed)
    t2 = t0 + timedelta(minutes=30)
    d3 = evaluate_debounce_gate(state, dev_id, eval_nominal, now=t2)
    assert d3.consecutive_nominal_cycles == 3
    assert d3.status == STATUS_CONFIRMED
    assert d3.eligible_for_clear is True


def test_evaluate_debounce_gate_reset_on_voltage_sag():
    from datetime import datetime, timezone, timedelta
    from src.live.alarm_clear_engine import (
        evaluate_debounce_gate,
        _empty_debounce_state,
        STATUS_PENDING,
        STATUS_RESET
    )

    state = _empty_debounce_state()
    dev_id = "branch-sag-test"
    t0 = datetime(2026, 10, 27, 8, 0, 0, tzinfo=timezone.utc)

    eval_nominal = RecoveryEvaluation(
        device_id=dev_id,
        is_recovered=True,
        confidence=1.0,
        status_tier="SERVICED_RECOVERED",
        summary_reason="Telemetry nominal",
        vitals={"battery_voltage": 12.8, "ac_voltage": 225.0, "mains_on": True}
    )
    # Voltage sag: AC dropped to 40V
    eval_sag = RecoveryEvaluation(
        device_id=dev_id,
        is_recovered=True,
        confidence=0.5,
        status_tier="UNVERIFIED",
        summary_reason="AC Undervoltage sag",
        vitals={"battery_voltage": 12.0, "ac_voltage": 40.0, "mains_on": False}
    )

    # Cycle 1: Nominal
    d1 = evaluate_debounce_gate(state, dev_id, eval_nominal, now=t0)
    assert d1.consecutive_nominal_cycles == 1

    # Cycle 2: Sudden sag -> counter reset!
    t1 = t0 + timedelta(minutes=15)
    d2 = evaluate_debounce_gate(state, dev_id, eval_sag, now=t1)
    assert d2.consecutive_nominal_cycles == 0
    assert d2.status == STATUS_RESET
    assert d2.eligible_for_clear is False
