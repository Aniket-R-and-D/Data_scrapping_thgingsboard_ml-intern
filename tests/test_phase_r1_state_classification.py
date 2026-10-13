"""tests/test_phase_r1_state_classification.py
Unit tests for Phase R1 Device State Classification Machine.
"""

import numpy as np
import pandas as pd
import pytest

from src.features.state_machine import (
    DeviceState,
    classify_device_state,
    classify_device_state_vectorized,
)


def test_state_online_ok_nominal():
    state = classify_device_state(
        telemetry_age_seconds=180.0,
        ac_voltage=224.5,
        mains_on=1.0,
        battery_voltage=13.8,
        is_power_alarm_active=False,
    )
    assert state == DeviceState.ONLINE_OK


def test_state_online_power_loss_ac_zero():
    state = classify_device_state(
        telemetry_age_seconds=300.0,
        ac_voltage=0.0,
        mains_on=0.0,
        battery_voltage=11.5,
        is_power_alarm_active=False,
    )
    assert state == DeviceState.ONLINE_POWER_LOSS


def test_state_online_power_loss_alarm_active():
    state = classify_device_state(
        telemetry_age_seconds=120.0,
        ac_voltage=220.0,
        mains_on=1.0,
        battery_voltage=13.0,
        is_power_alarm_active=True,
    )
    assert state == DeviceState.ONLINE_POWER_LOSS


def test_state_unequipped_battery_remains_online_ok():
    # Crucial Fix B test: A device without battery sensor (battery=NaN or 0.0 unequipped)
    # MUST NOT be classified as power loss when AC is healthy!
    state_nan = classify_device_state(
        telemetry_age_seconds=300.0,
        ac_voltage=228.0,
        mains_on=1.0,
        battery_voltage=np.nan,
        is_power_alarm_active=False,
    )
    assert state_nan == DeviceState.ONLINE_OK

    state_zero_unequipped = classify_device_state(
        telemetry_age_seconds=300.0,
        ac_voltage=228.0,
        mains_on=1.0,
        battery_voltage=0.0,
        is_power_alarm_active=False,
    )
    assert state_zero_unequipped == DeviceState.ONLINE_OK


def test_state_stale_data_transition():
    # 2 hours without telemetry
    state = classify_device_state(
        telemetry_age_seconds=7200.0,
        ac_voltage=0.0,
        mains_on=0.0,
        battery_voltage=0.0,
        is_power_alarm_active=False,
    )
    assert state == DeviceState.STALE_DATA


def test_state_offline_long_transition():
    # > 24 hours without telemetry
    state = classify_device_state(
        telemetry_age_seconds=100000.0,
        ac_voltage=220.0,
        mains_on=1.0,
        battery_voltage=13.0,
        is_power_alarm_active=False,
    )
    assert state == DeviceState.OFFLINE_LONG


def test_vectorized_matches_scalar():
    df = pd.DataFrame({
        "telemetry_age_seconds": [180.0, 300.0, 5000.0, 95000.0, 300.0],
        "ac_voltage": [220.0, 0.0, 220.0, np.nan, 220.0],
        "mains_on": [1.0, 0.0, 1.0, np.nan, 1.0],
        "battery_voltage": [13.5, 11.2, np.nan, np.nan, 0.0],
        "is_power_alarm_active": [0, 0, 0, 0, 1],
    })

    vec_states = classify_device_state_vectorized(
        df["telemetry_age_seconds"],
        df["ac_voltage"],
        df["mains_on"],
        df["battery_voltage"],
        df["is_power_alarm_active"],
    )

    expected = [
        DeviceState.ONLINE_OK.value,
        DeviceState.ONLINE_POWER_LOSS.value,
        DeviceState.STALE_DATA.value,
        DeviceState.OFFLINE_LONG.value,
        DeviceState.ONLINE_POWER_LOSS.value,
    ]

    assert list(vec_states) == expected
