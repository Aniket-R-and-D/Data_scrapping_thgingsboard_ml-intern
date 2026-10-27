"""tests/test_data_autocleaner.py
Unit tests for automated telemetry data profiling and autocleaning.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.features.data_autocleaner import (
    BATTERY_DISCONNECTED_THRESHOLD,
    clean_telemetry,
    compare_profiles,
    profile_telemetry,
    save_quality_evidence,
)


@pytest.fixture
def sample_telemetry_df() -> pd.DataFrame:
    """Create realistic raw telemetry dataframe containing anomalies, duplicates, and units."""
    n = 30
    timestamps = pd.date_range("2026-10-25 00:00:00", periods=n, freq="15min", tz="UTC")
    data = {
        "device_id": ["DEV-001"] * n,
        "timestamp": list(timestamps[:-1]) + [timestamps[10]],  # exactly 1 duplicate timestamp
        "ac_voltage": [230.0] * 20 + [40.0, 380.0, 225.0] + [230.0] * 7,      # low < 50V, surge > 350V
        "battery_voltage": [12400.0] * 15 + [0.2] * 5 + [12300.0] * 10,       # mV readings + <= 0.5V unequipped
        "temperature": [28.0] * 30,                                            # stuck sensor: zero std dev
        "unapproved_sensor_key": [1.0] * 30,                                   # policy filter target
    }
    return pd.DataFrame(data)


def test_profile_telemetry_detects_stuck_sensors_and_anomalies(sample_telemetry_df):
    profile = profile_telemetry(sample_telemetry_df)

    assert profile.total_rows == 30
    assert profile.unique_devices == 1
    assert "temperature" in profile.columns
    assert profile.columns["temperature"]["is_stuck"] is True
    assert profile.stuck_sensor_count >= 1
    assert profile.total_anomalies_detected > 0


def test_clean_telemetry_deduplication(sample_telemetry_df):
    clean_df, summary = clean_telemetry(sample_telemetry_df)

    assert summary["deduplicated_rows"] == 1
    assert len(clean_df) == 29
    assert clean_df.duplicated(subset=["device_id", "timestamp"]).sum() == 0


def test_clean_telemetry_unit_normalization(sample_telemetry_df):
    clean_df, summary = clean_telemetry(sample_telemetry_df)

    # Battery voltage in mV (12400.0) should be normalized to ~12.4V
    valid_battery = clean_df["battery_voltage"].dropna()
    assert (valid_battery >= 10.0).all() and (valid_battery <= 15.0).all()
    assert "battery_voltage" in summary["units_normalized"]


def test_clean_telemetry_sentinel_disentanglement(sample_telemetry_df):
    clean_df, summary = clean_telemetry(sample_telemetry_df)

    # Readings <= 0.5V must be converted to NaN
    assert not (clean_df["battery_voltage"] <= BATTERY_DISCONNECTED_THRESHOLD).any()
    assert "is_battery_sensor_present" in clean_df.columns
    assert (clean_df["is_battery_sensor_present"] == 0).sum() == 5


def test_clean_telemetry_ac_voltage_bounds(sample_telemetry_df):
    clean_df, summary = clean_telemetry(sample_telemetry_df)

    # Surge > 350V should be capped to 320V
    assert (clean_df["ac_voltage"] <= 320.0).all()
    # Reading < 50V sets outage indicator
    assert "is_ac_outage_detected" in clean_df.columns
    assert (clean_df["is_ac_outage_detected"] == 1).any()


def test_clean_telemetry_feature_policy_allowlist(sample_telemetry_df):
    policy = {
        "allowlist": ["device_id", "timestamp", "ac_voltage", "battery_voltage", "temperature"]
    }
    clean_df, summary = clean_telemetry(sample_telemetry_df, feature_policy=policy)

    assert "unapproved_sensor_key" not in clean_df.columns
    assert summary["policy_keys_filtered"] == 1


def test_compare_profiles_and_save_evidence(sample_telemetry_df, tmp_path):
    raw_profile = profile_telemetry(sample_telemetry_df)
    clean_df, _ = clean_telemetry(sample_telemetry_df)
    clean_profile = profile_telemetry(clean_df)

    comparison = compare_profiles(raw_profile, clean_profile)
    assert comparison["rows_before"] == 30
    assert comparison["rows_after"] == 29
    assert comparison["anomalies_resolved"] >= 0

    evidence_file = tmp_path / "test_data_quality_profile.json"
    saved_path = save_quality_evidence(comparison, report_path=evidence_file)
    assert saved_path.exists()

    with open(saved_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["rows_before"] == 30
