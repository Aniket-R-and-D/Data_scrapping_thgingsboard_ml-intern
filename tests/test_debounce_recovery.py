"""tests/test_debounce_recovery.py
Phase 7 debounced alarm-recovery gate tests.

The gate must:
* require 3 consecutive 15-minute nominal cycles (45-minute wall clock),
* reset the counter to 0 on any offline observation or voltage sag,
* persist state across probe processes,
* clear alarms only once confirmed, and record MTTR by bank cohort.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.live.alarm_clear_engine import (
    STATUS_CONFIRMED,
    STATUS_FAULT,
    STATUS_PENDING,
    STATUS_RESET,
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
from src.live.recovery_gate import RecoveryEvaluation

T0 = datetime(2026, 10, 27, 6, 0, 0, tzinfo=timezone.utc)
INTERVAL = timedelta(minutes=15)


def nominal_eval(device_id: str = "dev-1", **vitals) -> RecoveryEvaluation:
    payload = {"ac_voltage": 228.0, "battery_voltage": 13.6, "mains_on": True}
    payload.update(vitals)
    return RecoveryEvaluation(
        device_id=device_id,
        is_recovered=True,
        confidence=0.97,
        status_tier="SERVICED_RECOVERED",
        summary_reason="Telemetry nominal for the recovery window.",
        vitals=payload,
    )


def fault_eval(device_id: str = "dev-1", **vitals) -> RecoveryEvaluation:
    payload = {"ac_voltage": 228.0, "battery_voltage": 13.6, "mains_on": True}
    payload.update(vitals)
    return RecoveryEvaluation(
        device_id=device_id,
        is_recovered=False,
        confidence=0.1,
        status_tier="CONFIRMED_FAULT",
        summary_reason="Mains loss still observed.",
        vitals=payload,
    )


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "reports" / "recovery_debounce_state.json"


@pytest.fixture
def clean_state(state_path):
    return load_debounce_state(state_path)


# ---------------------------------------------------------------------------
# Core debounce behaviour
# ---------------------------------------------------------------------------
def test_two_nominal_cycles_do_not_clear(clean_state):
    first = evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0)
    assert first.consecutive_nominal_cycles == 1
    assert first.status == STATUS_PENDING
    assert first.eligible_for_clear is False

    second = evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + INTERVAL)
    assert second.consecutive_nominal_cycles == 2
    assert second.status == STATUS_PENDING
    assert second.eligible_for_clear is False


def test_third_cycle_confirms_after_forty_five_minutes(clean_state):
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0)
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + INTERVAL)
    third = evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + 2 * INTERVAL)

    assert third.consecutive_nominal_cycles == 3
    assert third.status == STATUS_CONFIRMED
    assert third.eligible_for_clear is True
    assert third.elapsed_minutes == pytest.approx(30.0)


def test_three_rapid_probes_cannot_clear_early(clean_state):
    """Counter alone is not enough: the wall-clock window must also elapse."""
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0)
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + timedelta(minutes=1))
    third = evaluate_debounce_gate(
        clean_state, "dev-1", nominal_eval(), now=T0 + timedelta(minutes=2)
    )

    assert third.consecutive_nominal_cycles == 3
    assert third.eligible_for_clear is False
    assert third.status == STATUS_PENDING
    assert "of 30 required minutes elapsed" in third.reason


def test_voltage_sag_resets_counter_to_zero(clean_state):
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0)
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + INTERVAL)

    sag = nominal_eval(ac_voltage=150.0)
    assert voltage_sag_detected(sag) is True

    reset = evaluate_debounce_gate(clean_state, "dev-1", sag, now=T0 + 2 * INTERVAL)
    assert reset.consecutive_nominal_cycles == 0
    assert reset.status == STATUS_RESET
    assert reset.eligible_for_clear is False
    assert clean_state["devices"]["dev-1"]["consecutive_nominal_cycles"] == 0
    assert clean_state["devices"]["dev-1"]["first_nominal_at"] is None


def test_battery_sag_and_mains_off_are_detected():
    assert voltage_sag_detected(nominal_eval(battery_voltage=11.0)) is True
    assert voltage_sag_detected(nominal_eval(mains_on=False)) is True
    assert voltage_sag_detected(nominal_eval()) is False


def test_offline_observation_resets_counter(clean_state):
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0)
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + INTERVAL)

    offline = evaluate_debounce_gate(clean_state, "dev-1", fault_eval(), now=T0 + 2 * INTERVAL)
    assert offline.consecutive_nominal_cycles == 0
    assert offline.status == STATUS_RESET
    assert offline.eligible_for_clear is False


def test_never_recovered_device_stays_faulted(clean_state):
    decision = evaluate_debounce_gate(clean_state, "dev-1", fault_eval(), now=T0)
    assert decision.status == STATUS_FAULT
    assert decision.eligible_for_clear is False


def test_counter_restarts_full_window_after_reset(clean_state):
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0)
    evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + INTERVAL)
    evaluate_debounce_gate(
        clean_state, "dev-1", nominal_eval(ac_voltage=140.0), now=T0 + 2 * INTERVAL
    )

    a = evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + 3 * INTERVAL)
    b = evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + 4 * INTERVAL)
    c = evaluate_debounce_gate(clean_state, "dev-1", nominal_eval(), now=T0 + 5 * INTERVAL)

    assert a.consecutive_nominal_cycles == 1
    assert b.consecutive_nominal_cycles == 2
    assert c.consecutive_nominal_cycles == 3
    assert c.eligible_for_clear is True


def test_history_is_bounded(clean_state):
    for offset in range(20):
        evaluate_debounce_gate(
            clean_state, "dev-1", nominal_eval(), now=T0 + offset * INTERVAL
        )
    assert len(clean_state["devices"]["dev-1"]["history"]) == 12


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def test_state_round_trips_through_json(state_path):
    state = load_debounce_state(state_path)
    evaluate_debounce_gate(
        state,
        "dev-1",
        nominal_eval(),
        device_name="BOI-BALLYBAZAR",
        bank="BOI",
        now=T0,
    )
    save_debounce_state(state, state_path)

    assert state_path.exists()
    restored = load_debounce_state(state_path)
    record = restored["devices"]["dev-1"]
    assert record["device_name"] == "BOI-BALLYBAZAR"
    assert record["bank"] == "BOI"
    assert record["consecutive_nominal_cycles"] == 1
    assert record["first_nominal_at"] is not None


def test_corrupt_state_file_starts_fresh(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json", encoding="utf-8")
    state = load_debounce_state(state_path)
    assert state["devices"] == {}
    assert state["required_cycles"] == 3


def test_pending_recovery_devices_lists_only_unconfirmed(clean_state):
    evaluate_debounce_gate(clean_state, "dev-pending", nominal_eval(), now=T0)
    evaluate_debounce_gate(clean_state, "dev-confirmed", nominal_eval(), now=T0)
    clean_state["devices"]["dev-confirmed"]["cleared_at"] = "2026-10-27T06:30:00+00:00"

    ids = {item["device_id"] for item in pending_recovery_devices(clean_state)}
    assert "dev-pending" in ids
    assert "dev-confirmed" not in ids


# ---------------------------------------------------------------------------
# Clearance authority
# ---------------------------------------------------------------------------
def test_clear_confirmed_recovery_clears_on_third_cycle(state_path):
    get_alarms = MagicMock(
        return_value=[
            {
                "id": {"id": "alarm-1"},
                "type": "AI_CRITICAL_ANOMALY",
                "startTs": 1761544800000,
            }
        ]
    )
    clear_alarm = MagicMock(return_value=True)
    sync_attrs = MagicMock(return_value=True)

    with patch("src.live.alarm_clear_engine.get_active_ai_alarms", get_alarms), \
         patch("src.live.alarm_clear_engine.clear_ai_alarm", clear_alarm), \
         patch("src.live.alarm_clear_engine.sync_recovery_attributes", sync_attrs):
        decisions = [
            clear_confirmed_recovery(
                "dev-1",
                nominal_eval(),
                "http://tb",
                "tok",
                device_name="BOI-BALLYBAZAR",
                bank="BOI",
                state_path=state_path,
                now=T0 + offset * INTERVAL,
            )
            for offset in range(3)
        ]

    assert decisions[0]["recovery_confirmed"] is False
    assert decisions[1]["recovery_confirmed"] is False
    assert decisions[2]["recovery_confirmed"] is True
    assert decisions[2]["cleared_alarm_ids"] == ["alarm-1"]
    assert decisions[2]["cleared_alarms_count"] == 1
    assert decisions[2]["attributes_synced"] is True
    assert decisions[2]["mttr_minutes"] is not None
    clear_alarm.assert_called_once_with("alarm-1", "http://tb", "tok")
    sync_attrs.assert_called_once()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["devices"]["dev-1"]["cleared_at"] is not None
    assert len(persisted["mttr_records"]) == 1
    assert persisted["mttr_records"][0]["bank"] == "BOI"


def test_clear_confirmed_recovery_never_calls_http_when_held(clean_state):
    with patch("src.live.alarm_clear_engine.get_active_ai_alarms") as get_alarms, \
         patch("src.live.alarm_clear_engine.clear_ai_alarm") as clear_alarm, \
         patch("src.live.alarm_clear_engine.sync_recovery_attributes") as sync_attrs:
        result = clear_confirmed_recovery(
            "dev-1", nominal_eval(), "http://tb", "tok", state=clean_state, now=T0
        )

    assert result["recovery_confirmed"] is False
    get_alarms.assert_not_called()
    clear_alarm.assert_not_called()
    sync_attrs.assert_not_called()


def test_clear_confirmed_recovery_does_not_double_count_mttr(clean_state):
    alarms = [
        {
            "id": {"id": "alarm-1"},
            "type": "AI_CRITICAL_ANOMALY",
            "startTs": 1761544800000,
        }
    ]
    with patch("src.live.alarm_clear_engine.get_active_ai_alarms", return_value=alarms), \
         patch("src.live.alarm_clear_engine.clear_ai_alarm", return_value=True), \
         patch("src.live.alarm_clear_engine.sync_recovery_attributes", return_value=True):
        for offset in range(4):
            clear_confirmed_recovery(
                "dev-1", nominal_eval(), "http://tb", "tok",
                state=clean_state, now=T0 + offset * INTERVAL,
            )

    assert len(clean_state["mttr_records"]) == 1


# ---------------------------------------------------------------------------
# MTTR + dispatch
# ---------------------------------------------------------------------------
def test_compute_mttr_minutes_handles_iso_and_epoch_ms():
    assert compute_mttr_minutes(
        "2026-10-27T06:00:00+00:00", "2026-10-27T07:30:00+00:00"
    ) == pytest.approx(90.0)

    start_ms = int(datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2026, 10, 27, 6, 45, tzinfo=timezone.utc).timestamp() * 1000)
    assert compute_mttr_minutes(start_ms, end_ms) == pytest.approx(45.0)


def test_compute_mttr_minutes_returns_none_for_bad_input():
    assert compute_mttr_minutes(None, "2026-10-27T06:00:00+00:00") is None
    assert compute_mttr_minutes("not-a-date", "2026-10-27T06:00:00+00:00") is None


def test_summarize_mttr_by_bank_cohorts():
    state = {
        "mttr_records": [
            {"bank": "BOI", "mttr_minutes": 45.0},
            {"bank": "BOI", "mttr_minutes": 75.0},
            {"bank": "SBI", "mttr_minutes": 30.0},
            {"bank": "BOB", "mttr_minutes": 60.0},
            {"bank": "Canara", "mttr_minutes": None},
        ]
    }
    summary = summarize_mttr_by_bank(state)

    assert summary["BOI"]["recoveries"] == 2
    assert summary["BOI"]["mean_mttr_minutes"] == pytest.approx(60.0)
    assert summary["BOI"]["median_mttr_minutes"] == pytest.approx(60.0)
    assert summary["SBI"]["mean_mttr_minutes"] == pytest.approx(30.0)
    assert summary["BOB"]["recoveries"] == 1
    assert "CANARA" not in summary  # no numeric MTTR recorded
    assert summary["ALL"]["recoveries"] == 4


def test_build_recovery_dispatch_payload_contains_slack_and_email():
    cleared = [
        {
            "device_id": "dev-1",
            "device_name": "BOI-BALLYBAZAR",
            "bank": "BOI",
            "mttr_minutes": 45.0,
            "cleared_alarms_count": 2,
            "recovery_confirmed": True,
        },
        {
            "device_id": "dev-2",
            "device_name": "SBI-KOLKATA",
            "bank": "SBI",
            "mttr_minutes": 60.0,
            "cleared_alarms_count": 1,
            "recovery_confirmed": True,
        },
        {
            "device_id": "dev-3",
            "device_name": "PENDING",
            "bank": "BOB",
            "mttr_minutes": None,
            "recovery_confirmed": False,
        },
    ]
    state = {
        "mttr_records": [
            {"bank": "BOI", "mttr_minutes": 45.0},
            {"bank": "SBI", "mttr_minutes": 60.0},
        ]
    }
    payload = build_recovery_dispatch_payload(cleared, state=state)

    assert payload["confirmed_count"] == 2
    assert payload["mean_mttr_minutes"] == pytest.approx(52.5)
    assert payload["mttr_by_bank"]["BOI"]["recoveries"] == 1
    assert len(payload["slack"]["blocks"]) >= 2
    assert "BOI-BALLYBAZAR" in json.dumps(payload["slack"]["blocks"])
    assert "SBI-KOLKATA" in payload["email"]["text"]
    assert "3 consecutive" in payload["email"]["text"]
    assert payload["email"]["subject"]
    assert "<h2>" in payload["email"]["html"]


def test_build_recovery_dispatch_payload_handles_empty_list():
    payload = build_recovery_dispatch_payload([], state={})
    assert payload["confirmed_count"] == 0
    assert payload["mean_mttr_minutes"] is None
    assert payload["mttr_by_bank"] == {}


# ---------------------------------------------------------------------------
# Daily-digest integration: the debounce gate is the only clearance authority
# ---------------------------------------------------------------------------
def _digest_fleet_frame():
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "device_id": "dev-1",
                "device_name": "BOI-BALLYBAZAR",
                "bank": "BOI",
                "unified_tier": "SERVICED_RECOVERED",
                "outage_prob_24h": 0.02,
                "anomaly_score": 0.05,
                "primary_issue": "Hardware restored",
                "recommended_action": "None",
                "recovery_vitals": '{"ac_voltage": 229.0, "battery_voltage": 13.6}',
            }
        ]
    )


def _digest_env():
    return {
        "TB_HOST": "http://tb",
        "TB_EMAIL": "ops@example.com",
        "TB_PASSWORD": "secret",
    }


def test_daily_digest_cannot_clear_alarms_on_first_recovery(monkeypatch, state_path):
    import pandas as pd
    from src.predict import send_daily_risk_digest as digest

    monkeypatch.setattr(digest, "load_debounce_state", lambda: load_debounce_state(state_path))
    monkeypatch.setattr(
        digest, "save_debounce_state", lambda state: save_debounce_state(state, state_path)
    )
    monkeypatch.setattr(
        digest.requests,
        "post",
        MagicMock(return_value=MagicMock(status_code=200, json=lambda: {"token": "tok"})),
    )

    clear_alarm = MagicMock(return_value=True)
    with patch("src.live.alarm_clear_engine.get_active_ai_alarms", return_value=[
        {"id": {"id": "alarm-1"}, "type": "AI_CRITICAL_ANOMALY", "startTs": 1761544800000}
    ]), patch("src.live.alarm_clear_engine.clear_ai_alarm", clear_alarm), \
         patch("src.live.alarm_clear_engine.sync_recovery_attributes", return_value=True):
        digest.sync_to_thingsboard(_digest_fleet_frame(), _digest_env())

    clear_alarm.assert_not_called()
    state = load_debounce_state(state_path)
    assert state["devices"]["dev-1"]["consecutive_nominal_cycles"] == 1
    assert state["devices"]["dev-1"]["cleared_at"] is None


def test_daily_digest_clears_only_after_window_elapsed(monkeypatch, state_path):
    from datetime import datetime, timezone
    from src.predict import send_daily_risk_digest as digest

    seeded = load_debounce_state(state_path)
    seeded["devices"]["dev-1"] = {
        "device_id": "dev-1",
        "device_name": "BOI-BALLYBAZAR",
        "bank": "BOI",
        "consecutive_nominal_cycles": 2,
        "first_flagged_at": "2026-10-27T06:00:00+00:00",
        "first_nominal_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
        "last_evaluated_at": None,
        "last_status": STATUS_PENDING,
        "last_reason": "Nominal cycle 2/3 confirmed.",
        "cleared_at": None,
        "last_cleared_at": None,
        "mttr_recorded": False,
        "history": [],
    }
    save_debounce_state(seeded, state_path)

    monkeypatch.setattr(digest, "load_debounce_state", lambda: load_debounce_state(state_path))
    monkeypatch.setattr(
        digest, "save_debounce_state", lambda state: save_debounce_state(state, state_path)
    )
    monkeypatch.setattr(
        digest.requests,
        "post",
        MagicMock(return_value=MagicMock(status_code=200, json=lambda: {"token": "tok"})),
    )

    clear_alarm = MagicMock(return_value=True)
    with patch("src.live.alarm_clear_engine.get_active_ai_alarms", return_value=[
        {"id": {"id": "alarm-1"}, "type": "AI_CRITICAL_ANOMALY", "startTs": 1761544800000}
    ]), patch("src.live.alarm_clear_engine.clear_ai_alarm", clear_alarm), \
         patch("src.live.alarm_clear_engine.sync_recovery_attributes", return_value=True):
        digest.sync_to_thingsboard(_digest_fleet_frame(), _digest_env())

    clear_alarm.assert_called_once_with("alarm-1", "http://tb", "tok")
    state = load_debounce_state(state_path)
    assert state["devices"]["dev-1"]["cleared_at"] is not None
    assert len(state["mttr_records"]) == 1
