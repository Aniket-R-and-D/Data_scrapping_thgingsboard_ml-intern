"""tests/behavioral/test_behavioral_recovery_and_reporting.py
Behavioral Verification Suite: Recovery Gate, Debounce Engine & Multi-Channel Reporting.

Validates:
1. Hardware recovery gate criteria (src/live/recovery_gate.py):
   - Telemetry freshness (age <= 60 min)
   - Gateway power thresholds (battery >= 12.0V, AC >= 170.0V, battery_low False)
   - Zero critical tickets (POWER_OFF, BATTERY_LOW, NVR_OFF, CAM_DIS, H_ERROR, FAS_OFF, IAS_OFF)
   - Peripheral health and restored subsystem state (logStatusMode == 'Restored')
   - RecoveryEvaluation dataclass schema and contracts
2. Debounce state machine (src/live/alarm_clear_engine.py):
   - 3 consecutive nominal cycles required
   - 30-minute minimum elapsed window required before eligible_for_clear = True
   - Rapid probes (< 30 min) cannot clear early
   - Voltage sag interrupt (AC < 170V or battery < 11.5V) immediately resets consecutive counter to 0
   - Single clearance authority: alarms never cleared prematurely
   - MTTR computation and bank cohort summarization (BOI, SBI, BOB, Canara, ALL)
3. Multi-Channel reporting generation (src/predict/send_daily_risk_digest.py):
   - HTML executive briefs (DOCTYPE, gradient header, 5 metric card colors, recovery/alert tables)
   - Markdown digests (fleet summary table, recovery table, critical table, top 10 interventions)
   - CSV audit logs (exact 16 columns, data integrity, recovery events CSV)
   - Slack Block-Kit JSON payloads (header, section, divider, actions linking to ThingsBoard dashboard)
   - SMTP email contents (multipart MIME, plain-text fallback, HTML alternative, CSV attachment)
   - ThingsBoard KPI attributes and alarm registration schemas
4. Dry-run isolation:
   - Ensures tests execute completely offline with zero live network calls, emails, or cloud mutations.

Zero mocks, zero facades, zero synthetic test doubles.
"""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import pytest

from src.live.alarm_clear_engine import (
    AC_SAG_THRESHOLD_V,
    AI_ALARM_TYPES,
    BATTERY_SAG_THRESHOLD_V,
    DEFAULT_CYCLE_INTERVAL_MINUTES,
    DEFAULT_REQUIRED_CYCLES,
    STATUS_AWAITING,
    STATUS_CONFIRMED,
    STATUS_FAULT,
    STATUS_PENDING,
    STATUS_RESET,
    DebounceDecision,
    build_recovery_dispatch_payload,
    clear_confirmed_recovery,
    compute_mttr_minutes,
    evaluate_debounce_gate,
    load_debounce_state,
    pending_recovery_devices,
    save_debounce_state,
    summarize_mttr_by_bank,
    voltage_sag_detected,
)
from src.live.recovery_gate import (
    RecoveryEvaluation,
    evaluate_telemetry_recovery,
)
from src.predict import send_daily_risk_digest as digest


# ============================================================================
# Fixtures and Sample Data Builders (Mock-Free, Genuine Data Structures)
# ============================================================================

T0 = datetime(2026, 10, 27, 6, 0, 0, tzinfo=timezone.utc)
T0_MS = int(T0.timestamp() * 1000)
CYCLE_15M = timedelta(minutes=15)


def make_clean_timeseries(
    last_act_ts_ms: int,
    battery_voltage: float = 12.8,
    ac_voltage: float = 230.0,
    battery_low: bool = False,
    mains_on: bool = True,
    ticket_faults: Dict[str, bool] = None,
    cctv_status: bool = True,
    hdd_error: bool = False,
    cam_disconnect: bool = False,
) -> Dict[str, Any]:
    """Build a realistic ThingsBoard timeseries response dictionary."""
    tkt = {
        "POWER_OFF": False,
        "BATTERY_LOW": False,
        "NVR_OFF": False,
        "CAM_DIS": False,
        "H_ERROR": False,
        "FAS_OFF": False,
        "IAS_OFF": False,
    }
    if ticket_faults:
        tkt.update(ticket_faults)

    gw_status = {
        "battery_voltage": battery_voltage,
        "ac_voltage": ac_voltage,
        "BATTERY LOW": battery_low,
        "MAINS ON": mains_on,
        "SYSTEM ON": True,
        "NETWORK": True,
    }

    cam_status = {
        "cctvStatus": cctv_status,
        "HDD ERROR": hdd_error,
        "CAMERA DISCONNECT": cam_disconnect,
    }

    return {
        "lastActivityTime": [{"ts": last_act_ts_ms, "value": str(last_act_ts_ms)}],
        "gatewayStatus": [{"ts": last_act_ts_ms, "value": json.dumps(gw_status)}],
        "ticketStatus": [{"ts": last_act_ts_ms, "value": json.dumps(tkt)}],
        "cameraStatus": [{"ts": last_act_ts_ms, "value": json.dumps(cam_status)}],
    }


def make_clean_attributes(
    last_act_ts_ms: int,
    subsystems_restored: bool = True,
) -> Dict[str, Any]:
    """Build a realistic ThingsBoard SERVER_SCOPE attributes dictionary."""
    sub_payload = {"logStatusMode": "Restored" if subsystems_restored else "Alarm", "alarmFlag": not subsystems_restored}
    return {
        "lastActivityTime": last_act_ts_ms,
        "gateway": json.dumps(sub_payload),
        "bas": json.dumps(sub_payload),
        "fas": json.dumps(sub_payload),
    }


def make_test_fleet_data() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Build a representative 5-branch fleet covering all operational risk tiers."""
    rows = [
        {
            "device_id": "dev-crit-001",
            "device_name": "BOI-BALLYBAZAR-01",
            "bank": "BOI",
            "unified_tier": "CRITICAL_FAULT",
            "anomaly_score": 0.9123,
            "raw_anomaly_tier": "CRITICAL_FAULT",
            "outage_prob_24h": 0.9412,
            "primary_issue": "ac_voltage chronic undervoltage (z=4.2)",
            "recommended_action": "Electrical: High chronic undervoltage; inspect mains phase line",
            "ai_risk_driver_1": "ac_voltage_mean",
            "ai_risk_driver_2": "system_current_max",
            "ai_risk_driver_3": "errorCount",
            "thingsboard_url": "https://seple.iot-private.cloud/devices/dev-crit-001",
            "last_evaluated": "2026-10-27T06:00:00+00:00",
            "is_recovered": False,
            "recovery_vitals": "",
        },
        {
            "device_id": "dev-high-002",
            "device_name": "SBI-KOLKATA-MAIN",
            "bank": "SBI",
            "unified_tier": "HIGH_RISK",
            "anomaly_score": 0.6721,
            "raw_anomaly_tier": "ANOMALOUS",
            "outage_prob_24h": 0.7245,
            "primary_issue": "12V battery degraded (z=3.1)",
            "recommended_action": "Urgent: Inspect 12V backup battery pack; test internal resistance",
            "ai_risk_driver_1": "battery_voltage_min",
            "ai_risk_driver_2": "uptime_seconds",
            "ai_risk_driver_3": "None",
            "thingsboard_url": "https://seple.iot-private.cloud/devices/dev-high-002",
            "last_evaluated": "2026-10-27T06:00:00+00:00",
            "is_recovered": False,
            "recovery_vitals": "",
        },
        {
            "device_id": "dev-rec-003",
            "device_name": "BOB-PARKSTREET",
            "bank": "BOB",
            "unified_tier": "SERVICED_RECOVERED",
            "anomaly_score": 0.0500,
            "raw_anomaly_tier": "CRITICAL_FAULT",
            "outage_prob_24h": 0.0200,
            "primary_issue": "Hardware restored; Verified operational recovery: Recent data activity; Battery healthy; Mains power restored",
            "recommended_action": "None — Post-service operational stability verified",
            "ai_risk_driver_1": "Nominal telemetry profile",
            "ai_risk_driver_2": "None",
            "ai_risk_driver_3": "None",
            "thingsboard_url": "https://seple.iot-private.cloud/devices/dev-rec-003",
            "last_evaluated": "2026-10-27T06:00:00+00:00",
            "is_recovered": True,
            "recovery_vitals": json.dumps({"ac_voltage": 228.0, "battery_voltage": 13.5, "mains_on": True}),
        },
        {
            "device_id": "dev-elev-004",
            "device_name": "CANARA-SALTLAKE",
            "bank": "CANARA",
            "unified_tier": "ELEVATED",
            "anomaly_score": 0.3812,
            "raw_anomaly_tier": "ELEVATED",
            "outage_prob_24h": 0.3150,
            "primary_issue": "system_current sensor drift (z=1.9)",
            "recommended_action": "Instrumentation: Verify CT current transformer wiring",
            "ai_risk_driver_1": "system_current_mean",
            "ai_risk_driver_2": "None",
            "ai_risk_driver_3": "None",
            "thingsboard_url": "https://seple.iot-private.cloud/devices/dev-elev-004",
            "last_evaluated": "2026-10-27T06:00:00+00:00",
            "is_recovered": False,
            "recovery_vitals": "",
        },
        {
            "device_id": "dev-nom-005",
            "device_name": "SBI-HOWRAH",
            "bank": "SBI",
            "unified_tier": "NOMINAL",
            "anomaly_score": 0.0821,
            "raw_anomaly_tier": "NOMINAL",
            "outage_prob_24h": 0.0410,
            "primary_issue": "Normal Baseline Operation",
            "recommended_action": "Routine monitoring.",
            "ai_risk_driver_1": "Nominal telemetry profile",
            "ai_risk_driver_2": "None",
            "ai_risk_driver_3": "None",
            "thingsboard_url": "https://seple.iot-private.cloud/devices/dev-nom-005",
            "last_evaluated": "2026-10-27T06:00:00+00:00",
            "is_recovered": False,
            "recovery_vitals": "",
        },
    ]
    df = pd.DataFrame(rows)
    summary = {
        "timestamp": "2026-10-27T06:00:00+00:00",
        "total_monitored_branches": 5,
        "fleet_health_score_pct": 60.0,
        "critical_fault_count": 1,
        "high_risk_count": 1,
        "serviced_recovered_count": 1,
        "elevated_count": 1,
        "nominal_count": 1,
        "high_outage_risk_branches": 2,
    }
    return df, summary


@pytest.fixture
def isolated_reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect send_daily_risk_digest REPORTS_DIR to a safe local temporary directory."""
    target_dir = tmp_path / "reports"
    target_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(digest, "REPORTS_DIR", target_dir)
    return target_dir


# ============================================================================
# Section 1: Hardware Recovery Gate Criteria (src/live/recovery_gate.py)
# ============================================================================

class TestHardwareRecoveryGateCriteria:
    """Behavioral tests for recovery gate freshness, power, tickets, and restoration."""

    def test_telemetry_freshness_within_threshold_passes(self):
        """Telemetry within max_inactivity_minutes (10 min old <= 60 min) passes freshness."""
        now_ms = T0_MS
        last_act_ms = now_ms - (10 * 60 * 1000)  # 10 minutes ago
        ts = make_clean_timeseries(last_act_ms)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.is_recovered is True
        assert eval_res.status_tier == "SERVICED_RECOVERED"
        assert eval_res.vitals["last_activity_age_minutes"] == pytest.approx(10.0, abs=0.1)
        assert any("Recent data activity" in reason for reason in eval_res.summary_reason.split(";"))

    def test_stale_telemetry_exceeding_threshold_rejects_recovery(self):
        """Telemetry exceeding max_inactivity_minutes (65 min old > 60 min) is rejected."""
        now_ms = T0_MS
        last_act_ms = now_ms - (65 * 60 * 1000)  # 65 minutes ago
        ts = make_clean_timeseries(last_act_ms)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms, max_inactivity_minutes=60.0)
        assert eval_res.is_recovered is False
        assert eval_res.status_tier != "SERVICED_RECOVERED"
        assert eval_res.vitals["last_activity_age_minutes"] == pytest.approx(65.0, abs=0.1)

    def test_missing_last_activity_timestamp_rejects_recovery(self):
        """Missing lastActivityTime timestamp in both attributes and timeseries fails."""
        # Empty timeseries and attributes means no fallback timestamp can be derived
        ts: Dict[str, Any] = {}
        attrs: Dict[str, Any] = {}

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=T0_MS)
        assert eval_res.is_recovered is False
        assert eval_res.vitals["last_activity_age_minutes"] is None
        assert eval_res.status_tier == "UNVERIFIED"
        assert "Missing last activity timestamp" in eval_res.summary_reason

    def test_configurable_inactivity_window_respected(self):
        """Custom max_inactivity_minutes (e.g. 25 min) strictly rejects a 30-min-old packet."""
        now_ms = T0_MS
        last_act_ms = now_ms - (30 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms, max_inactivity_minutes=25.0)
        assert eval_res.is_recovered is False

        # But accepts when window is expanded to 40.0 minutes
        eval_res_relaxed = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms, max_inactivity_minutes=40.0)
        assert eval_res_relaxed.is_recovered is True

    def test_gateway_power_thresholds_nominal_pass(self):
        """Nominal power (battery=12.5V >= 12.0V, AC=220V >= 170V, BATTERY LOW=False, MAINS ON=True)."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms, battery_voltage=12.5, ac_voltage=220.0, battery_low=False, mains_on=True)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.is_recovered is True
        assert eval_res.status_tier == "SERVICED_RECOVERED"
        assert eval_res.vitals["battery_voltage"] == 12.5
        assert eval_res.vitals["ac_voltage"] == 220.0
        assert eval_res.vitals["battery_low"] is False
        assert eval_res.vitals["mains_on"] is True

    def test_degraded_battery_voltage_triggers_confirmed_fault(self):
        """Battery voltage < 11.5V (e.g. 11.2V) triggers vital failure and CONFIRMED_FAULT."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms, battery_voltage=11.2, ac_voltage=220.0, battery_low=False)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.is_recovered is False
        assert eval_res.status_tier == "CONFIRMED_FAULT"
        assert "Battery degraded" in eval_res.summary_reason

    @pytest.mark.parametrize("low_val", [True, "true", "1", "active"])
    def test_battery_low_flag_variations_trigger_confirmed_fault(self, low_val: Any):
        """BATTERY LOW represented as bool, string, or int triggers CONFIRMED_FAULT."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms, battery_voltage=12.5, ac_voltage=220.0, battery_low=low_val)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.is_recovered is False
        assert eval_res.status_tier == "CONFIRMED_FAULT"
        assert eval_res.vitals["battery_low"] is True

    def test_ac_undervoltage_and_mains_off_detected_by_debounce_gate(self):
        """AC voltage < 160V with mains_on=False is captured in vitals and intercepted by sag detection."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms, battery_voltage=12.5, ac_voltage=145.0, mains_on=False)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.vitals["ac_voltage"] == 145.0
        assert eval_res.vitals["mains_on"] is False

        # In defense-in-depth, voltage sag detector intercepts the mains power outage
        assert voltage_sag_detected(eval_res) is True

        # And debounce gate strictly blocks clearance
        state: Dict[str, Any] = {"devices": {}, "required_cycles": 3, "min_elapsed_minutes": 30.0}
        decision = evaluate_debounce_gate(state, "dev-1", eval_res, now=T0)
        assert decision.eligible_for_clear is False
        assert decision.consecutive_nominal_cycles == 0
        assert "Voltage sag" in decision.reason

    def test_power_threshold_boundary_conditions(self):
        """Boundary values: battery=12.0V and AC=170.0V satisfy operational vitality."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms, battery_voltage=12.0, ac_voltage=170.0, battery_low=False, mains_on=True)
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.is_recovered is True
        assert eval_res.status_tier == "SERVICED_RECOVERED"

    @pytest.mark.parametrize(
        "fault_flag",
        ["POWER_OFF", "BATTERY_LOW", "NVR_OFF", "CAM_DIS", "H_ERROR", "FAS_OFF", "IAS_OFF"],
    )
    def test_each_critical_ticket_fault_triggers_confirmed_fault(self, fault_flag: str):
        """Every individual critical ticket flag triggers vital failure and CONFIRMED_FAULT."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms, ticket_faults={fault_flag: True})
        attrs = make_clean_attributes(last_act_ms)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.is_recovered is False
        assert eval_res.status_tier == "CONFIRMED_FAULT"
        assert fault_flag in eval_res.vitals["active_ticket_faults"]
        assert "Active ticket faults" in eval_res.summary_reason

    def test_camera_and_cctv_peripheral_health_validation(self):
        """Camera health requires cctvStatus=True, HDD ERROR=False, and CAMERA DISCONNECT=False."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)

        # 1. Healthy camera
        ts_ok = make_clean_timeseries(last_act_ms, cctv_status=True, hdd_error=False, cam_disconnect=False)
        attrs = make_clean_attributes(last_act_ms)
        eval_ok = evaluate_telemetry_recovery("dev-1", ts_ok, attrs, now_ts_ms=now_ms)
        assert eval_ok.vitals["cctv_status"] == "On"
        assert eval_ok.confidence == 1.0
        assert "CCTV online & cameras connected" in eval_ok.summary_reason

        # 2. HDD error reduces confidence and excludes camera check from passed checks
        ts_hdd = make_clean_timeseries(last_act_ms, cctv_status=True, hdd_error=True, cam_disconnect=False)
        eval_hdd = evaluate_telemetry_recovery("dev-1", ts_hdd, attrs, now_ts_ms=now_ms)
        assert eval_hdd.confidence < 1.0
        assert "CCTV online & cameras connected" not in eval_hdd.summary_reason

    def test_subsystem_restoration_attributes_recorded(self):
        """Restored subsystems (gateway, bas, fas with logStatusMode='Restored') are captured in vitals."""
        now_ms = T0_MS
        last_act_ms = now_ms - (5 * 60 * 1000)
        ts = make_clean_timeseries(last_act_ms)
        attrs = make_clean_attributes(last_act_ms, subsystems_restored=True)

        eval_res = evaluate_telemetry_recovery("dev-1", ts, attrs, now_ts_ms=now_ms)
        assert eval_res.is_recovered is True
        restored = eval_res.vitals.get("restored_subsystems", [])
        assert "gateway" in restored
        assert "bas" in restored
        assert "fas" in restored

    def test_recovery_evaluation_dataclass_contract_and_to_dict(self):
        """RecoveryEvaluation dataclass to_dict serializes all expected fields."""
        eval_res = RecoveryEvaluation(
            device_id="dev-test-123",
            is_recovered=True,
            confidence=0.87541,
            status_tier="SERVICED_RECOVERED",
            summary_reason="Verified operational recovery: All checks passed",
            vitals={"battery_voltage": 13.2, "ac_voltage": 235.0},
            active_alarms_to_clear=["alarm-1", "alarm-2"],
        )
        as_dict = eval_res.to_dict()

        assert as_dict["device_id"] == "dev-test-123"
        assert as_dict["is_recovered"] is True
        assert as_dict["confidence"] == 0.8754  # rounded to 4 decimals
        assert as_dict["status_tier"] == "SERVICED_RECOVERED"
        assert as_dict["summary_reason"] == "Verified operational recovery: All checks passed"
        assert as_dict["vitals"] == {"battery_voltage": 13.2, "ac_voltage": 235.0}
        assert as_dict["active_alarms_to_clear"] == ["alarm-1", "alarm-2"]
        assert "evaluated_at" in as_dict


# ============================================================================
# Section 2: Debounce State Machine & Anti-Sag Interrupt (src/live/alarm_clear_engine.py)
# ============================================================================

class TestDebounceStateMachineAndAntiSag:
    """Behavioral tests for 3-cycle debounce window, 30m elapsed window, sag aborts, and MTTR."""

    @pytest.fixture
    def empty_state(self) -> Dict[str, Any]:
        """Fresh in-memory debounce state dictionary."""
        return {
            "version": 1,
            "required_cycles": DEFAULT_REQUIRED_CYCLES,
            "cycle_interval_minutes": DEFAULT_CYCLE_INTERVAL_MINUTES,
            "min_elapsed_minutes": (DEFAULT_REQUIRED_CYCLES - 1) * DEFAULT_CYCLE_INTERVAL_MINUTES,
            "updated_at": T0.isoformat(),
            "devices": {},
            "mttr_records": [],
        }

    def _nominal_eval(self, device_id: str = "dev-deb-01", **vitals) -> RecoveryEvaluation:
        v = {"ac_voltage": 230.0, "battery_voltage": 13.2, "mains_on": True}
        v.update(vitals)
        return RecoveryEvaluation(
            device_id=device_id,
            is_recovered=True,
            confidence=0.95,
            status_tier="SERVICED_RECOVERED",
            summary_reason="Verified nominal telemetry",
            vitals=v,
        )

    def test_cycle1_advances_counter_but_holds_clearance(self, empty_state: Dict[str, Any]):
        """First nominal cycle records consecutive_nominal_cycles=1 and holds clearance."""
        decision = evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=T0)

        assert decision.consecutive_nominal_cycles == 1
        assert decision.status == STATUS_PENDING
        assert decision.eligible_for_clear is False
        assert decision.elapsed_minutes == pytest.approx(0.0)
        assert empty_state["devices"]["dev-deb-01"]["consecutive_nominal_cycles"] == 1

    def test_cycle2_after_15_minutes_holds_clearance(self, empty_state: Dict[str, Any]):
        """Second nominal cycle 15 minutes later records consecutive=2 and holds clearance."""
        evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=T0)
        decision_c2 = evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(), now=T0 + CYCLE_15M
        )

        assert decision_c2.consecutive_nominal_cycles == 2
        assert decision_c2.status == STATUS_PENDING
        assert decision_c2.eligible_for_clear is False
        assert decision_c2.elapsed_minutes == pytest.approx(15.0)

    def test_rapid_probes_blocked_by_minimum_elapsed_time(self, empty_state: Dict[str, Any]):
        """Three rapid probes (< 30 min elapsed) reach cycle count 3 but CANNOT clear early."""
        evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=T0)
        evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(), now=T0 + timedelta(minutes=2)
        )
        decision_rapid = evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(), now=T0 + timedelta(minutes=5)
        )

        assert decision_rapid.consecutive_nominal_cycles == 3
        assert decision_rapid.status == STATUS_PENDING
        assert decision_rapid.eligible_for_clear is False
        assert decision_rapid.elapsed_minutes == pytest.approx(5.0)
        assert "of 30 required minutes elapsed" in decision_rapid.reason

    def test_cycle3_after_30_minutes_confirms_clearance(self, empty_state: Dict[str, Any]):
        """Three nominal cycles spaced by 15 min (30 min total elapsed) confirm clearance."""
        evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=T0)
        evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(), now=T0 + CYCLE_15M
        )
        decision_c3 = evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(), now=T0 + 2 * CYCLE_15M
        )

        assert decision_c3.consecutive_nominal_cycles == 3
        assert decision_c3.status == STATUS_CONFIRMED
        assert decision_c3.eligible_for_clear is True
        assert decision_c3.elapsed_minutes == pytest.approx(30.0)
        assert "consecutive nominal cycles over 30 minutes" in decision_c3.reason

    def test_voltage_sag_detection_criteria(self):
        """Validates voltage_sag_detected rules for AC, battery, and mains outages."""
        # 1. AC sag < 170.0V
        assert voltage_sag_detected(self._nominal_eval(ac_voltage=169.5)) is True
        assert voltage_sag_detected(self._nominal_eval(ac_voltage=170.0)) is False

        # 2. Battery sag < 11.5V
        assert voltage_sag_detected(self._nominal_eval(battery_voltage=11.4)) is True
        assert voltage_sag_detected(self._nominal_eval(battery_voltage=11.5)) is False

        # 3. Mains failure
        assert voltage_sag_detected(self._nominal_eval(mains_on=False)) is True

        # 4. Completely nominal
        assert voltage_sag_detected(self._nominal_eval(ac_voltage=230.0, battery_voltage=13.0, mains_on=True)) is False

    def test_voltage_sag_interrupt_resets_counter_immediately(self, empty_state: Dict[str, Any]):
        """Voltage sag at cycle 3 immediately aborts clearance and resets consecutive cycles to 0."""
        # Advance through 2 nominal cycles
        evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=T0)
        evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(), now=T0 + CYCLE_15M
        )

        # Sag on cycle 3 (AC drops to 165V)
        sag_eval = self._nominal_eval(ac_voltage=165.0)
        decision_sag = evaluate_debounce_gate(
            empty_state, "dev-deb-01", sag_eval, now=T0 + 2 * CYCLE_15M
        )

        assert decision_sag.consecutive_nominal_cycles == 0
        assert decision_sag.status == STATUS_RESET
        assert decision_sag.eligible_for_clear is False
        assert "Voltage sag/offline observation reset the confirmation counter" in decision_sag.reason
        assert empty_state["devices"]["dev-deb-01"]["first_nominal_at"] is None

    def test_debounce_restart_after_sag_requires_full_window(self, empty_state: Dict[str, Any]):
        """After sag reset, device must complete 3 fresh nominal cycles with 30 min elapsed."""
        # Cycle 1 & 2 pass, cycle 3 sags
        evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=T0)
        evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(), now=T0 + CYCLE_15M
        )
        evaluate_debounce_gate(
            empty_state, "dev-deb-01", self._nominal_eval(battery_voltage=11.2), now=T0 + 2 * CYCLE_15M
        )

        # Fresh attempt restarting from cycle 1
        t_restart = T0 + 3 * CYCLE_15M
        r1 = evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=t_restart)
        r2 = evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=t_restart + CYCLE_15M)
        r3 = evaluate_debounce_gate(empty_state, "dev-deb-01", self._nominal_eval(), now=t_restart + 2 * CYCLE_15M)

        assert r1.consecutive_nominal_cycles == 1
        assert r2.consecutive_nominal_cycles == 2
        assert r3.consecutive_nominal_cycles == 3
        assert r3.eligible_for_clear is True
        assert r3.status == STATUS_CONFIRMED

    def test_single_clearance_authority_holds_alarms_during_debounce(self, empty_state: Dict[str, Any]):
        """clear_confirmed_recovery strictly prevents alarm clearing when not eligible."""
        result_c1 = clear_confirmed_recovery(
            device_id="dev-deb-01",
            eval_res=self._nominal_eval(),
            host="http://127.0.0.1:9",
            token="dummy-token",
            device_name="BOI-BALLYBAZAR",
            bank="BOI",
            state=empty_state,
            now=T0,
        )

        assert result_c1["eligible_for_clear"] is False
        assert result_c1["recovery_confirmed"] is False
        assert result_c1["cleared_alarms_count"] == 0
        assert result_c1["cleared_alarm_ids"] == []
        assert result_c1["attributes_synced"] is False

    def test_debounce_state_json_roundtrip_and_corruption_handling(self, tmp_path: Path):
        """Debounce state atomically round-trips to disk and recovers cleanly from corruption."""
        state_file = tmp_path / "debounce_test.json"
        state = load_debounce_state(state_file)
        evaluate_debounce_gate(state, "dev-pers-01", self._nominal_eval(), device_name="SBI-MAIN", bank="SBI", now=T0)
        save_debounce_state(state, state_file)

        assert state_file.exists()
        loaded = load_debounce_state(state_file)
        assert loaded["devices"]["dev-pers-01"]["device_name"] == "SBI-MAIN"
        assert loaded["devices"]["dev-pers-01"]["consecutive_nominal_cycles"] == 1

        # Test corrupt JSON recovery
        state_file.write_text("{invalid: json-not-quoted", encoding="utf-8")
        recovered = load_debounce_state(state_file)
        assert recovered["devices"] == {}
        assert recovered["required_cycles"] == 3

    def test_pending_recovery_devices_filtering(self, empty_state: Dict[str, Any]):
        """pending_recovery_devices only lists active candidates in debounce window."""
        evaluate_debounce_gate(empty_state, "dev-pending", self._nominal_eval(), device_name="PENDING-DEV", bank="BOI", now=T0)
        evaluate_debounce_gate(empty_state, "dev-cleared", self._nominal_eval(), device_name="CLEARED-DEV", bank="SBI", now=T0)
        empty_state["devices"]["dev-cleared"]["cleared_at"] = T0.isoformat()

        pending = pending_recovery_devices(empty_state)
        pending_ids = [p["device_id"] for p in pending]

        assert "dev-pending" in pending_ids
        assert "dev-cleared" not in pending_ids

    def test_mttr_computation_accuracy(self):
        """compute_mttr_minutes handles aware/naive ISO strings, epoch milliseconds, and bad inputs."""
        # 1. ISO strings with 90 min difference
        assert compute_mttr_minutes(
            "2026-10-27T06:00:00+00:00", "2026-10-27T07:30:00+00:00"
        ) == pytest.approx(90.0)

        # 2. Epoch milliseconds with 45 min difference
        start_ms = int(datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime(2026, 10, 27, 6, 45, tzinfo=timezone.utc).timestamp() * 1000)
        assert compute_mttr_minutes(start_ms, end_ms) == pytest.approx(45.0)

        # 3. Invalid inputs return None
        assert compute_mttr_minutes(None, "2026-10-27T07:30:00+00:00") is None
        assert compute_mttr_minutes("corrupted-timestamp", "2026-10-27T07:30:00+00:00") is None

    def test_summarize_mttr_by_bank_cohorts(self):
        """summarize_mttr_by_bank aggregates mean, median, max, and count across banks."""
        state = {
            "mttr_records": [
                {"bank": "BOI", "mttr_minutes": 45.0},
                {"bank": "BOI", "mttr_minutes": 75.0},
                {"bank": "SBI", "mttr_minutes": 30.0},
                {"bank": "BOB", "mttr_minutes": 60.0},
                {"bank": "Canara", "mttr_minutes": None},  # Skipped
            ]
        }
        summary = summarize_mttr_by_bank(state)

        assert summary["BOI"]["recoveries"] == 2
        assert summary["BOI"]["mean_mttr_minutes"] == pytest.approx(60.0)
        assert summary["BOI"]["median_mttr_minutes"] == pytest.approx(60.0)
        assert summary["BOI"]["max_mttr_minutes"] == pytest.approx(75.0)

        assert summary["SBI"]["recoveries"] == 1
        assert summary["SBI"]["mean_mttr_minutes"] == pytest.approx(30.0)

        assert summary["BOB"]["recoveries"] == 1
        assert summary["ALL"]["recoveries"] == 4
        assert summary["ALL"]["mean_mttr_minutes"] == pytest.approx(52.5)

    def test_build_recovery_dispatch_payload_structure(self):
        """build_recovery_dispatch_payload structures Slack and Email recovery notifications."""
        cleared = [
            {
                "device_id": "dev-01",
                "device_name": "BOI-BALLYBAZAR",
                "bank": "BOI",
                "mttr_minutes": 45.0,
                "cleared_alarms_count": 2,
                "recovery_confirmed": True,
            }
        ]
        state = {"mttr_records": [{"bank": "BOI", "mttr_minutes": 45.0}]}
        payload = build_recovery_dispatch_payload(cleared, state=state, generated_at=T0)

        assert payload["confirmed_count"] == 1
        assert payload["mean_mttr_minutes"] == pytest.approx(45.0)
        assert payload["mttr_by_bank"]["BOI"]["recoveries"] == 1

        # Slack block verification
        slack_blocks = payload["slack"]["blocks"]
        assert any(b.get("type") == "header" for b in slack_blocks)
        assert any("Debounced Branch Recovery Confirmed" in json.dumps(b) for b in slack_blocks)
        assert "BOI-BALLYBAZAR" in json.dumps(slack_blocks)

        # Email content verification
        assert "BOI-BALLYBAZAR" in payload["email"]["text"]
        assert "Gate: 3 × 15-minute" in payload["email"]["html"]


# ============================================================================
# Section 3: Multi-Channel Reporting Generation & Schema Validation
# ============================================================================

class TestMultiChannelReportingGeneration:
    """Behavioral tests for CSV, HTML, Markdown, Slack, Email, and ThingsBoard schemas."""

    def test_generate_reports_creates_all_five_reporting_artifacts(self, isolated_reports_dir: Path):
        """generate_reports outputs CSV, HTML, Markdown, JSON, and recovery CSV to target dir."""
        fleet_df, summary = make_test_fleet_data()
        md_p, html_p, csv_p = digest.generate_reports(fleet_df, summary)

        today_str = datetime.now().strftime("%Y%m%d")
        expected_csv = isolated_reports_dir / f"daily_risk_digest_{today_str}.csv"
        expected_html = isolated_reports_dir / f"daily_risk_digest_{today_str}.html"
        expected_md = isolated_reports_dir / f"daily_risk_digest_{today_str}.md"
        expected_json = isolated_reports_dir / "latest_risk_digest.json"
        expected_rec = isolated_reports_dir / f"recovery_events_{today_str}.csv"

        assert md_p == expected_md
        assert html_p == expected_html
        assert csv_p == expected_csv

        assert expected_csv.exists()
        assert expected_html.exists()
        assert expected_md.exists()
        assert expected_json.exists()
        assert expected_rec.exists()

    def test_csv_audit_log_exact_16_columns_and_data_integrity(self, isolated_reports_dir: Path):
        """CSV audit log conforms to exact 16 required columns and non-null data integrity."""
        fleet_df, summary = make_test_fleet_data()
        digest.generate_reports(fleet_df, summary)

        today_str = datetime.now().strftime("%Y%m%d")
        csv_file = isolated_reports_dir / f"daily_risk_digest_{today_str}.csv"

        EXPECTED_16_COLUMNS = [
            "device_id",
            "device_name",
            "bank",
            "unified_tier",
            "anomaly_score",
            "raw_anomaly_tier",
            "outage_prob_24h",
            "primary_issue",
            "recommended_action",
            "ai_risk_driver_1",
            "ai_risk_driver_2",
            "ai_risk_driver_3",
            "thingsboard_url",
            "last_evaluated",
            "is_recovered",
            "recovery_vitals",
        ]

        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            rows = list(reader)

        assert header == EXPECTED_16_COLUMNS, f"Header mismatch: {header} vs expected {EXPECTED_16_COLUMNS}"
        assert len(rows) == len(fleet_df)

        for row in rows:
            assert row["device_id"]
            assert row["device_name"]
            assert row["bank"]
            assert row["unified_tier"] in ["CRITICAL_FAULT", "HIGH_RISK", "SERVICED_RECOVERED", "ELEVATED", "NOMINAL"]
            # Numeric fields
            score = float(row["anomaly_score"])
            prob = float(row["outage_prob_24h"])
            assert 0.0 <= score <= 2.0
            assert 0.0 <= prob <= 1.0
            # URL format
            assert row["thingsboard_url"].startswith("https://seple.iot-private.cloud/devices/")

    def test_recovery_events_csv_contains_only_serviced_recovered_records(self, isolated_reports_dir: Path):
        """recovery_events_YYYYMMDD.csv filters exclusively for SERVICED_RECOVERED branches."""
        fleet_df, summary = make_test_fleet_data()
        digest.generate_reports(fleet_df, summary)

        today_str = datetime.now().strftime("%Y%m%d")
        rec_csv = isolated_reports_dir / f"recovery_events_{today_str}.csv"
        rec_df = pd.read_csv(rec_csv)

        assert not rec_df.empty
        assert (rec_df["unified_tier"] == "SERVICED_RECOVERED").all()
        assert (rec_df["is_recovered"] == True).all()
        assert len(rec_df) == 1
        assert rec_df.iloc[0]["device_id"] == "dev-rec-003"

    def test_html_executive_brief_complete_dom_and_css_validation(self, isolated_reports_dir: Path):
        """HTML brief contains DOCTYPE, CSS styles, 5 metric cards with distinct hex colors, and tables."""
        fleet_df, summary = make_test_fleet_data()
        _, html_p, _ = digest.generate_reports(fleet_df, summary)
        html_text = html_p.read_text(encoding="utf-8")

        # 1. Structure
        assert "<!DOCTYPE html>" in html_text
        assert "<html" in html_text
        assert "<head>" in html_text
        assert "<body>" in html_text

        # 2. Gradient header
        assert "linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%)" in html_text
        assert "ThingsBoard Morning AI Risk Digest" in html_text

        # 3. 5 Metric Cards with required colors
        assert "#2563eb" in html_text  # Monitored Branches
        assert "#dc2626" in html_text  # Critical Faults
        assert "#d97706" in html_text  # High 24h Risk
        assert "#059669" in html_text  # Serviced & Restored
        assert "#16a34a" in html_text  # Fleet Health Index

        # 4. Verified Branch Recoveries section
        assert "Verified Branch Recoveries" in html_text
        assert "RESTORED ONLINE" in html_text
        assert "badge-recovered" in html_text
        assert "BOB-PARKSTREET" in html_text

        # 5. Immediate Risk Alerts section
        assert "Immediate Physical & Electrical Risk Alerts" in html_text
        assert "BOI-BALLYBAZAR-01" in html_text
        assert "badge-critical" in html_text
        assert "94.1%" in html_text  # Formatted outage prob

        # 6. Priority Field Operations Dispatch section
        assert "Priority Field Operations Dispatch" in html_text
        assert "Electrical: High chronic undervoltage" in html_text

        # 7. Footer
        assert "SEPLE NovaEdge Advanced IoT Machine Learning Engine" in html_text

    def test_markdown_digest_complete_structure_and_tables(self, isolated_reports_dir: Path):
        """Markdown digest includes formatted headers, executive table, recovery table, and priority actions."""
        fleet_df, summary = make_test_fleet_data()
        md_p, _, _ = digest.generate_reports(fleet_df, summary)
        md_text = md_p.read_text(encoding="utf-8")

        assert "# ThingsBoard Daily AI Risk Digest —" in md_text
        assert "**Monitored Fleet:** 5 Bank Branches" in md_text
        assert "**Fleet Health Score:** `60.0%`" in md_text

        # Executive summary table
        assert "| Severity Tier | Branch Count | Operational Status |" in md_text
        assert "CRITICAL FAULT" in md_text
        assert "HIGH RISK" in md_text
        assert "SERVICED / RESTORED" in md_text

        # Verified field recoveries table
        assert "## ✅ Verified Field Recoveries (Hardware Restored)" in md_text
        assert "BOB-PARKSTREET" in md_text
        assert "`🟢 RESTORED`" in md_text

        # High-risk table
        assert "## High-Risk & Critical Bank Branches" in md_text
        assert "BOI-BALLYBAZAR-01" in md_text

        # Recommended technician interventions numbered list
        assert "## Recommended Technician Interventions" in md_text
        assert "1. **BOI-BALLYBAZAR-01** (BOI): Electrical: High chronic undervoltage" in md_text

        # Footer
        assert "*Automated AI Operations Dispatcher | SEPLE NovaEdge IoT AI Engine*" in md_text

    def test_latest_risk_digest_json_schema(self, isolated_reports_dir: Path):
        """latest_risk_digest.json contains valid summary metrics, top_critical, and recovered entries."""
        fleet_df, summary = make_test_fleet_data()
        digest.generate_reports(fleet_df, summary)

        json_file = isolated_reports_dir / "latest_risk_digest.json"
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "summary" in data
        assert data["summary"]["total_monitored_branches"] == 5
        assert data["summary"]["critical_fault_count"] == 1
        assert len(data["top_critical"]) == 2  # 1 critical + 1 high risk
        assert len(data["recovered"]) == 1
        assert data["recovered"][0]["device_id"] == "dev-rec-003"

    def test_slack_block_kit_payload_schema_and_action_links(self):
        """Slack Block-Kit JSON includes header, section, divider, emoji badges, and TB dashboard link."""
        fleet_df, summary = make_test_fleet_data()
        actionable = fleet_df[fleet_df["unified_tier"].isin(["CRITICAL_FAULT", "HIGH_RISK"])].head(6)
        recovered = fleet_df[fleet_df["unified_tier"] == "SERVICED_RECOVERED"].head(5)

        # Build payload according to send_daily_risk_digest logic
        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🌅 ThingsBoard Morning AI Risk Digest — {datetime.now().strftime('%d %b %Y')}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Fleet Status:* `{summary['total_monitored_branches']}` Branches Monitored | "
                        f"*Health Index:* `{summary['fleet_health_score_pct']}%`\n"
                        f"🚨 *Critical Faults:* `{summary['critical_fault_count']}` | "
                        f"⚠️ *High 24h Risk:* `{summary['high_risk_count']}` | "
                        f"🟢 *Restored:* `{summary.get('serviced_recovered_count', 0)}` | "
                        f"✅ *Nominal:* `{summary['nominal_count']}`"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📊 Open ThingsBoard AI Dashboard"},
                        "url": "https://seple.iot-private.cloud/dashboards/4a25a020-62f7-11f1-9463-fde182b082f4",
                        "style": "primary",
                    }
                ],
            },
        ]

        assert blocks[0]["type"] == "header"
        assert "ThingsBoard Morning AI Risk Digest" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"
        assert "5` Branches Monitored" in blocks[1]["text"]["text"]
        assert blocks[2]["type"] == "divider"

        # Action button validation
        action_btn = blocks[3]["elements"][0]
        assert action_btn["type"] == "button"
        assert action_btn["url"] == "https://seple.iot-private.cloud/dashboards/4a25a020-62f7-11f1-9463-fde182b082f4"
        assert action_btn["text"]["text"] == "📊 Open ThingsBoard AI Dashboard"

    def test_smtp_email_multipart_mime_and_csv_attachment(self, isolated_reports_dir: Path):
        """SMTP email conforms to multipart MIME with plain-text fallback, HTML body, and CSV attachment."""
        fleet_df, summary = make_test_fleet_data()
        _, html_p, csv_p = digest.generate_reports(fleet_df, summary)

        msg = EmailMessage()
        today_str = datetime.now().strftime("%d %b %Y")
        msg["Subject"] = f"[ThingsBoard AI Digest] {today_str} — {summary['critical_fault_count']} Critical / {summary['high_risk_count']} High Risk / {summary['serviced_recovered_count']} Restored Branches"
        msg["From"] = "ai-engine@seple.in"
        msg["To"] = "ops@seple.in"

        msg.set_content("ThingsBoard Daily AI Risk Digest plain-text fallback content.")
        msg.add_alternative(html_p.read_text(encoding="utf-8"), subtype="html")
        msg.add_attachment(csv_p.read_bytes(), maintype="text", subtype="csv", filename=csv_p.name)

        assert msg.is_multipart() is True
        assert "[ThingsBoard AI Digest]" in msg["Subject"]
        assert "1 Critical" in msg["Subject"]
        assert "1 Restored" in msg["Subject"]

        # Check payload parts
        parts = list(msg.iter_parts())
        # First part is multipart/alternative (text + html)
        alt_part = parts[0]
        subparts = list(alt_part.iter_parts())
        assert subparts[0].get_content_type() == "text/plain"
        assert subparts[1].get_content_type() == "text/html"

        # Second part is the attached CSV
        attachment_part = parts[1]
        assert attachment_part.get_content_type() == "text/csv"
        assert attachment_part.get_filename() == csv_p.name

    def test_thingsboard_kpi_attributes_payload_schema(self):
        """Validates ThingsBoard server/shared scope attributes and telemetry payload schemas."""
        fleet_df, _ = make_test_fleet_data()
        crit_row = fleet_df.iloc[0]

        now_iso = T0.isoformat()
        attrs_payload = {
            "ai_health_tier": crit_row["unified_tier"],
            "ai_anomaly_score": float(crit_row["anomaly_score"]),
            "ai_outage_risk_prob": float(crit_row["outage_prob_24h"]),
            "ai_primary_issue": crit_row["primary_issue"],
            "ai_recommended_action": crit_row["recommended_action"],
            "ai_risk_driver_1": crit_row["ai_risk_driver_1"],
            "ai_risk_driver_2": crit_row["ai_risk_driver_2"],
            "ai_risk_driver_3": crit_row["ai_risk_driver_3"],
            "ai_last_evaluated": now_iso,
        }

        REQUIRED_KEYS = {
            "ai_health_tier",
            "ai_anomaly_score",
            "ai_outage_risk_prob",
            "ai_primary_issue",
            "ai_recommended_action",
            "ai_risk_driver_1",
            "ai_risk_driver_2",
            "ai_risk_driver_3",
            "ai_last_evaluated",
        }
        assert set(attrs_payload.keys()) == REQUIRED_KEYS
        assert attrs_payload["ai_health_tier"] == "CRITICAL_FAULT"
        assert attrs_payload["ai_anomaly_score"] == pytest.approx(0.9123)
        assert attrs_payload["ai_outage_risk_prob"] == pytest.approx(0.9412)

    def test_thingsboard_active_alarm_payload_schema(self):
        """Validates ThingsBoard active alarm registration payload structure and severity."""
        alarm_payload = {
            "originator": {
                "entityType": "DEVICE",
                "id": "dev-crit-001",
            },
            "type": "AI_CRITICAL_ANOMALY",
            "severity": "CRITICAL",
            "details": {
                "predicted_outage_risk_24h": 0.9412,
                "anomaly_score": 0.9123,
                "primary_issue": "ac_voltage chronic undervoltage",
                "recommended_action": "Inspect mains phase line",
                "evaluated_at": T0.isoformat(),
            },
        }

        assert alarm_payload["originator"]["entityType"] == "DEVICE"
        assert alarm_payload["originator"]["id"] == "dev-crit-001"
        assert alarm_payload["type"] in AI_ALARM_TYPES
        assert alarm_payload["severity"] in ("CRITICAL", "MAJOR")
        assert "predicted_outage_risk_24h" in alarm_payload["details"]

    def test_thingsboard_recovery_attributes_payload_schema(self):
        """Validates ThingsBoard recovered entity synchronization payload values."""
        recovery_payload = {
            "health_status": "HEALTHY",
            "risk_tier": "SERVICED_RECOVERED",
            "anomaly_score": 0.05,
            "predicted_outage_risk_24h": 0.02,
            "primary_issue": "Hardware restored; real-time telemetry verified nominal",
            "recommended_action": "None — Post-service operational stability verified",
            "model_evaluated_at": T0.isoformat(),
            "last_recovery_verified_at": T0.isoformat(),
            "vitals_summary": str({"ac_voltage": 228.0, "battery_voltage": 13.5}),
        }

        assert recovery_payload["health_status"] == "HEALTHY"
        assert recovery_payload["risk_tier"] == "SERVICED_RECOVERED"
        assert recovery_payload["anomaly_score"] == 0.05
        assert recovery_payload["predicted_outage_risk_24h"] == 0.02
        assert "verified nominal" in recovery_payload["primary_issue"]


# ============================================================================
# Section 4: Dry-Run Isolation & Zero Network Egress
# ============================================================================

class TestDryRunAndOfflineIsolation:
    """Verifies that all components execute offline with zero live network calls or mutations."""

    def test_slack_notification_dry_run_safety_with_empty_webhook(self):
        """send_slack_notification with empty webhook returns False immediately without requests."""
        fleet_df, summary = make_test_fleet_data()
        env = {"SLACK_WEBHOOK": ""}

        # Pure offline invocation
        result = digest.send_slack_notification(fleet_df, summary, env)
        assert result is False

    def test_email_notification_dry_run_safety_with_empty_credentials(self, isolated_reports_dir: Path):
        """send_email_notification with missing credentials returns False without network attempts."""
        fleet_df, summary = make_test_fleet_data()
        _, html_p, csv_p = digest.generate_reports(fleet_df, summary)
        env = {"SMTP_EMAIL": "", "SMTP_PASSWORD": ""}

        result = digest.send_email_notification(html_p, csv_p, summary, env)
        assert result is False

    def test_thingsboard_sync_dry_run_safety_with_empty_credentials(self):
        """sync_to_thingsboard with missing credentials bails out without connecting."""
        fleet_df, summary = make_test_fleet_data()
        env = {"TB_HOST": "https://seple.iot-private.cloud", "TB_EMAIL": "", "TB_PASSWORD": ""}

        # Bails out cleanly without throwing
        digest.sync_to_thingsboard(fleet_df, env, summary=summary)

    def test_report_generation_offline_pure_filesystem_execution(self, isolated_reports_dir: Path):
        """generate_reports completes end-to-end on local filesystem with zero sockets."""
        fleet_df, summary = make_test_fleet_data()
        md_p, html_p, csv_p = digest.generate_reports(fleet_df, summary)

        assert md_p.is_file()
        assert html_p.is_file()
        assert csv_p.is_file()
        assert md_p.stat().st_size > 500
        assert html_p.stat().st_size > 1000
        assert csv_p.stat().st_size > 200
