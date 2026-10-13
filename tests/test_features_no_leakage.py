"""Unit tests for Phase 2: Feature Engineering & Strict Zero-Leakage (Shift Tests)."""

import pytest
import shutil
import tempfile
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.features.build_features import build_device_features, compute_slope


@pytest.fixture
def temp_device_env():
    """Create isolated temporary environment for a mock device."""
    tmp_dir = Path(tempfile.mkdtemp())
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_compute_slope_causality():
    # Test linear slope computation
    times = np.array([0.0, 1.0, 2.0, 3.0])
    # Increasing by 10 per hour -> slope = 10.0
    vals = np.array([100.0, 110.0, 120.0, 130.0])
    assert pytest.approx(compute_slope(vals, times), rel=1e-3) == 10.0

    # Decreasing by 5 per hour -> slope = -5.0
    vals_dec = np.array([100.0, 95.0, 90.0, 85.0])
    assert pytest.approx(compute_slope(vals_dec, times), rel=1e-3) == -5.0


def test_feature_causality_shift_test(temp_device_env):
    """Rigorous shift test proving no feature uses future data.

    Any changes to data strictly after anchor t must have ZERO effect on features at anchor t.
    """
    dev_dir = temp_device_env
    t_anchor = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_start = t_anchor - timedelta(days=2)
    t_end = t_anchor + timedelta(days=2)

    # 1. Base telemetry before and around anchor t
    # Data points at t - 2h, t - 1h, t (anchor)
    ts_pts = [
        t_anchor - timedelta(hours=2),
        t_anchor - timedelta(hours=1),
        t_anchor,
        t_anchor + timedelta(hours=1),
        t_anchor + timedelta(hours=2),
    ]

    base_ac_vals = [220.0, 222.0, 221.0, 219.0, 220.0]
    base_batt_vals = [13.2, 13.1, 13.0, 12.9, 12.8]

    def write_series(filename: str, vals: list[float]):
        df = pd.DataFrame({
            "ts": [int(pt.timestamp() * 1000) for pt in ts_pts],
            "value": vals,
        })
        df.to_parquet(dev_dir / filename, index=False)

    write_series("ac_voltage.parquet", base_ac_vals)
    write_series("battery_voltage.parquet", base_batt_vals)

    # Run 1: Compute features with base future data
    feats_base = build_device_features(
        device_id="mock-01",
        device_name="BOI-MOCK",
        raw_dev_dir=dev_dir,
        outage_spans=[],
        alarms_dev_df=pd.DataFrame(),
        start_dt=t_start,
        end_dt=t_end,
        resample_interval="1h",
    )

    # Extract feature row at t_anchor
    row_base = feats_base[feats_base["timestamp"] == t_anchor.isoformat()].iloc[0]

    # 2. Mutate FUTURE data (> t_anchor) drastically:
    # Future AC spikes to 999V, future battery collapses to 0V
    mutated_ac_vals = [220.0, 222.0, 221.0, 999.0, 888.0]
    mutated_batt_vals = [13.2, 13.1, 13.0, 0.0, 0.0]

    write_series("ac_voltage.parquet", mutated_ac_vals)
    write_series("battery_voltage.parquet", mutated_batt_vals)

    # Run 2: Recompute features with mutated future data
    feats_mutated = build_device_features(
        device_id="mock-01",
        device_name="BOI-MOCK",
        raw_dev_dir=dev_dir,
        outage_spans=[],
        alarms_dev_df=pd.DataFrame(),
        start_dt=t_start,
        end_dt=t_end,
        resample_interval="1h",
    )

    row_mutated = feats_mutated[feats_mutated["timestamp"] == t_anchor.isoformat()].iloc[0]

    # 3. Assert strict invariance across all feature columns (excluding metadata)
    cols_to_check = [
        c for c in feats_base.columns
        if c not in ("device_id", "device_name", "timestamp", "y_outage_24h")
    ]

    for col in cols_to_check:
        val_base = row_base[col]
        val_mut = row_mutated[col]
        assert val_base == val_mut, (
            f"LEAKAGE DETECTED in column '{col}': base value {val_base} != mutated value {val_mut}"
        )


def test_target_alignment(temp_device_env):
    """Test that target y_outage_24h aligns exactly to the future (t, t + 24h] window."""
    dev_dir = temp_device_env
    t_anchor = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_start = t_anchor - timedelta(hours=6)
    t_end = t_anchor + timedelta(hours=6)

    # Case A: Outage starts at t + 10h -> within (t, t + 24h] -> target = 1
    outage_t_plus_10h_ms = (t_anchor + timedelta(hours=10)).timestamp() * 1000.0
    feats_a = build_device_features(
        device_id="mock-01",
        device_name="BOI-MOCK",
        raw_dev_dir=dev_dir,
        outage_spans=[(outage_t_plus_10h_ms, outage_t_plus_10h_ms + 3600 * 1000)],
        alarms_dev_df=pd.DataFrame(),
        start_dt=t_start,
        end_dt=t_end,
        resample_interval="1h",
    )
    row_a = feats_a[feats_a["timestamp"] == t_anchor.isoformat()].iloc[0]
    assert row_a["y_outage_24h"] == 1

    # Case B: Outage starts at t + 25h -> outside (t, t + 24h] -> target = 0
    outage_t_plus_25h_ms = (t_anchor + timedelta(hours=25)).timestamp() * 1000.0
    feats_b = build_device_features(
        device_id="mock-01",
        device_name="BOI-MOCK",
        raw_dev_dir=dev_dir,
        outage_spans=[(outage_t_plus_25h_ms, outage_t_plus_25h_ms + 3600 * 1000)],
        alarms_dev_df=pd.DataFrame(),
        start_dt=t_start,
        end_dt=t_end,
        resample_interval="1h",
    )
    row_b = feats_b[feats_b["timestamp"] == t_anchor.isoformat()].iloc[0]
    assert row_b["y_outage_24h"] == 0

    # Case C: Outage in the past (t - 2h) -> target = 0
    outage_past_ms = (t_anchor - timedelta(hours=2)).timestamp() * 1000.0
    feats_c = build_device_features(
        device_id="mock-01",
        device_name="BOI-MOCK",
        raw_dev_dir=dev_dir,
        outage_spans=[(outage_past_ms, outage_past_ms + 3600 * 1000)],
        alarms_dev_df=pd.DataFrame(),
        start_dt=t_start,
        end_dt=t_end,
        resample_interval="1h",
    )
    row_c = feats_c[feats_c["timestamp"] == t_anchor.isoformat()].iloc[0]
    assert row_c["y_outage_24h"] == 0


def test_gap_encoding_and_missingness(temp_device_env):
    """Test that missing readings are explicitly encoded as gaps and not silently filled."""
    dev_dir = temp_device_env
    t_anchor = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_start = t_anchor - timedelta(hours=2)
    t_end = t_anchor + timedelta(hours=2)

    # Telemetry last seen at t - 2 hours (7200 seconds ago)
    t_old = t_anchor - timedelta(hours=2)
    df_hb = pd.DataFrame({
        "ts": [int(t_old.timestamp() * 1000)],
        "value": [1.0],
    })
    df_hb.to_parquet(dev_dir / "uptimeHeartbeat.parquet", index=False)

    feats = build_device_features(
        device_id="mock-01",
        device_name="BOI-MOCK",
        raw_dev_dir=dev_dir,
        outage_spans=[],
        alarms_dev_df=pd.DataFrame(),
        start_dt=t_start,
        end_dt=t_end,
        resample_interval="1h",
    )
    row = feats[feats["timestamp"] == t_anchor.isoformat()].iloc[0]

    # Gap must be approx 7200 seconds
    assert pytest.approx(row["heartbeat_gap_seconds"], abs=60) == 7200.0
    assert row["missing_indicator"] == 1
