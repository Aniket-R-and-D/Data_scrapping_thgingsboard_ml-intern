"""tests/test_recovery_gate.py
Unit tests for the Real-Time Telemetry & Attribute Post-Fix Recovery Gate.
"""

import json
import time
import pytest
from src.live.recovery_gate import (
    evaluate_telemetry_recovery,
    _safe_json_parse,
    _to_bool,
    _to_float,
    RecoveryEvaluation
)


def test_safe_json_parse():
    assert _safe_json_parse('{"SYSTEM ON": "true"}') == {"SYSTEM ON": "true"}
    assert _safe_json_parse("regular string") == "regular string"
    assert _safe_json_parse(123) == 123
    assert _safe_json_parse({"already": "dict"}) == {"already": "dict"}


def test_to_bool():
    assert _to_bool("true") is True
    assert _to_bool("True") is True
    assert _to_bool("On") is True
    assert _to_bool(True) is True
    assert _to_bool(1) is True

    assert _to_bool("false") is False
    assert _to_bool("False") is False
    assert _to_bool("Off") is False
    assert _to_bool(False) is False
    assert _to_bool("N/A") is False
    assert _to_bool(None) is None


def test_to_float():
    assert _to_float("14.0") == 14.0
    assert _to_float(14.5) == 14.5
    assert _to_float("invalid") is None
    assert _to_float(None) is None


def test_evaluate_recovery_fully_healthy():
    now_ms = int(time.time() * 1000)
    dev_id = "test-dev-001"

    timeseries = {
        "gatewayStatus": [
            {
                "ts": now_ms - 60000,
                "value": json.dumps({
                    "SYSTEM ON": "true",
                    "MAINS ON": "true",
                    "BATTERY LOW": "false",
                    "NETWORK": "true",
                    "ac_voltage": 220.0,
                    "battery_voltage": 13.8
                })
            }
        ],
        "ticketStatus": [
            {
                "ts": now_ms - 60000,
                "value": json.dumps({
                    "POWER_OFF": "false",
                    "BATTERY_LOW": "false",
                    "NVR_OFF": "false",
                    "CAM_DIS": "false",
                    "H_ERROR": "false"
                })
            }
        ],
        "cameraStatus": [
            {
                "ts": now_ms - 60000,
                "value": json.dumps({
                    "cctvStatus": "On",
                    "CAMERA DISCONNECT": "false",
                    "HDD ERROR": "false"
                })
            }
        ]
    }

    attributes = {
        "lastActivityTime": now_ms - 60000,
        "gateway": json.dumps({"logStatusMode": "Restored", "alarmFlag": False})
    }

    eval_res = evaluate_telemetry_recovery(dev_id, timeseries, attributes, now_ts_ms=now_ms)
    assert eval_res.is_recovered is True
    assert eval_res.status_tier == "SERVICED_RECOVERED"
    assert eval_res.vitals["battery_voltage"] == 13.8
    assert eval_res.vitals["ac_voltage"] == 220.0
    assert eval_res.vitals["last_activity_age_minutes"] == pytest.approx(1.0, 0.1)


def test_evaluate_recovery_still_failing_battery():
    now_ms = int(time.time() * 1000)
    dev_id = "test-dev-002"

    timeseries = {
        "gatewayStatus": [
            {
                "ts": now_ms - 30000,
                "value": json.dumps({
                    "SYSTEM ON": "true",
                    "MAINS ON": "false",
                    "BATTERY LOW": "true",
                    "NETWORK": "true",
                    "ac_voltage": 0.0,
                    "battery_voltage": 10.5
                })
            }
        ],
        "ticketStatus": [
            {
                "ts": now_ms - 30000,
                "value": json.dumps({
                    "POWER_OFF": "true",
                    "BATTERY_LOW": "true",
                    "NVR_OFF": "false",
                    "CAM_DIS": "false"
                })
            }
        ]
    }

    attributes = {
        "lastActivityTime": now_ms - 30000
    }

    eval_res = evaluate_telemetry_recovery(dev_id, timeseries, attributes, now_ts_ms=now_ms)
    assert eval_res.is_recovered is False
    assert eval_res.status_tier == "CONFIRMED_FAULT"
    assert "POWER_OFF" in eval_res.vitals["active_ticket_faults"]
    assert "BATTERY_LOW" in eval_res.vitals["active_ticket_faults"]


def test_evaluate_recovery_stale_inactivity():
    now_ms = int(time.time() * 1000)
    dev_id = "test-dev-003"

    # Device last reported 120 minutes ago
    two_hours_ago = now_ms - (120 * 60 * 1000)

    timeseries = {
        "gatewayStatus": [
            {
                "ts": two_hours_ago,
                "value": json.dumps({
                    "SYSTEM ON": "true",
                    "MAINS ON": "true",
                    "BATTERY LOW": "false",
                    "battery_voltage": 13.5
                })
            }
        ]
    }

    attributes = {
        "lastActivityTime": two_hours_ago
    }

    eval_res = evaluate_telemetry_recovery(
        dev_id, timeseries, attributes, now_ts_ms=now_ms, max_inactivity_minutes=60.0
    )
    # Stale device cannot be confirmed recovered
    assert eval_res.is_recovered is False
    assert eval_res.vitals["last_activity_age_minutes"] >= 120.0
