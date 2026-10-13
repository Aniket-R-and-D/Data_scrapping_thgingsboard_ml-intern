"""src/features/state_machine.py
Phase R1: Device State Classification Machine.

Categorizes each device observation into one of four mutually exclusive physical operational states:
1. ONLINE_OK: Telemetry is fresh (<= 60 min, typically <= 15 min), and power is verified healthy (AC >= 180V or mains on).
2. ONLINE_POWER_LOSS: Gateway is actively reporting (<= 60 min), but electrical sensors prove utility loss (AC < 50V or mains off or power alarm).
3. STALE_DATA: Communication stalled; no telemetry received between 60 min and 24 hours (86,400s).
4. OFFLINE_LONG: Communication severed for > 24 hours (86,400s).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
import numpy as np
import pandas as pd


class DeviceState(str, Enum):
    ONLINE_OK = "ONLINE_OK"
    ONLINE_POWER_LOSS = "ONLINE_POWER_LOSS"
    STALE_DATA = "STALE_DATA"
    OFFLINE_LONG = "OFFLINE_LONG"


def classify_device_state(
    telemetry_age_seconds: float,
    ac_voltage: Optional[float] = None,
    mains_on: Optional[float] = None,
    battery_voltage: Optional[float] = None,
    is_power_alarm_active: bool = False,
    stale_threshold_seconds: float = 3600.0,      # 60 min
    offline_threshold_seconds: float = 86400.0,    # 24 hours
    ac_outage_volts: float = 50.0,
) -> DeviceState:
    """Deterministic physical classification of device state at point-in-time."""
    # 1. Check for prolonged communication severance (> 24 hours)
    if telemetry_age_seconds is None or np.isnan(telemetry_age_seconds) or telemetry_age_seconds > offline_threshold_seconds:
        return DeviceState.OFFLINE_LONG

    # 2. Check for stalled telemetry (60 min < age <= 24 hours)
    if telemetry_age_seconds > stale_threshold_seconds:
        return DeviceState.STALE_DATA

    # 3. Telemetry is fresh (age <= 60 min). Now evaluate electrical power status:
    # A. Direct power alarm active while reporting
    if is_power_alarm_active:
        return DeviceState.ONLINE_POWER_LOSS

    # B. AC Voltage check (if equipped and not NaN)
    if ac_voltage is not None and not np.isnan(ac_voltage):
        if ac_voltage < ac_outage_volts:
            return DeviceState.ONLINE_POWER_LOSS

    # C. Mains binary indicator (if equipped and not NaN)
    if mains_on is not None and not np.isnan(mains_on):
        if mains_on == 0.0:
            return DeviceState.ONLINE_POWER_LOSS

    # D. Battery discharging critically (if equipped and not NaN)
    if battery_voltage is not None and not np.isnan(battery_voltage):
        # In an online gateway, battery < 10.8V indicates critical discharge under mains loss
        if 0.5 < battery_voltage < 10.8:
            return DeviceState.ONLINE_POWER_LOSS

    # Otherwise, power is nominal
    return DeviceState.ONLINE_OK


def classify_device_state_vectorized(
    telemetry_age_seconds: pd.Series,
    ac_voltage: pd.Series,
    mains_on: pd.Series,
    battery_voltage: pd.Series,
    is_power_alarm_active: pd.Series,
    stale_threshold_seconds: float = 3600.0,
    offline_threshold_seconds: float = 86400.0,
    ac_outage_volts: float = 50.0,
) -> pd.Series:
    """Vectorized version for processing entire DataFrames efficiently."""
    states = pd.Series(DeviceState.ONLINE_OK.value, index=telemetry_age_seconds.index)

    # Long offline mask
    offline_mask = telemetry_age_seconds.isna() | (telemetry_age_seconds > offline_threshold_seconds)
    states[offline_mask] = DeviceState.OFFLINE_LONG.value

    # Stale mask (between stale and offline threshold)
    stale_mask = (~offline_mask) & (telemetry_age_seconds > stale_threshold_seconds)
    states[stale_mask] = DeviceState.STALE_DATA.value

    # For online rows (age <= stale_threshold_seconds), check power loss conditions
    online_mask = ~offline_mask & ~stale_mask

    power_loss_cond = pd.Series(False, index=telemetry_age_seconds.index)

    # Condition 1: Alarm active
    power_loss_cond = power_loss_cond | (is_power_alarm_active == 1)

    # Condition 2: AC voltage < 50V (where not NaN)
    ac_valid = ac_voltage.notna()
    power_loss_cond = power_loss_cond | (ac_valid & (ac_voltage < ac_outage_volts))

    # Condition 3: Mains on == 0 (where not NaN)
    mains_valid = mains_on.notna()
    power_loss_cond = power_loss_cond | (mains_valid & (mains_on == 0.0))

    # Condition 4: Battery critical (where equipped and non-zero: 0.5 < batt < 10.8)
    batt_valid = battery_voltage.notna() & (battery_voltage > 0.5)
    power_loss_cond = power_loss_cond | (batt_valid & (battery_voltage < 10.8))

    states[online_mask & power_loss_cond] = DeviceState.ONLINE_POWER_LOSS.value

    return states
