"""Unit tests for src/features/drift_monitor.py."""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from src.features.drift_monitor import (
    compute_psi,
    compute_wasserstein,
    evaluate_feature_drift,
    save_drift_report,
    PSI_CRITICAL_THRESHOLD,
    PSI_MODERATE_THRESHOLD
)


def test_compute_psi_identical_distributions():
    np.random.seed(42)
    baseline = np.random.normal(loc=230.0, scale=10.0, size=500)
    target = baseline.copy()

    psi = compute_psi(baseline, target)
    assert 0.0 <= psi < 0.05


def test_compute_psi_shifted_distribution():
    np.random.seed(42)
    baseline = np.random.normal(loc=230.0, scale=10.0, size=500)
    # Severe shift: grid voltage collapsed to 40V
    target = np.random.normal(loc=40.0, scale=5.0, size=500)

    psi = compute_psi(baseline, target)
    assert psi > PSI_CRITICAL_THRESHOLD


def test_compute_psi_zero_variance():
    baseline = np.full(100, 12.6)
    target_same = np.full(100, 12.6)
    target_diff = np.full(100, 0.0)

    assert compute_psi(baseline, target_same) == 0.0
    assert compute_psi(baseline, target_diff) >= 1.0


def test_compute_wasserstein():
    np.random.seed(42)
    baseline = np.random.normal(loc=12.5, scale=0.5, size=200)
    target = np.random.normal(loc=12.5, scale=0.5, size=200)

    w = compute_wasserstein(baseline, target)
    assert w >= 0.0
    assert w < 0.3


def test_evaluate_feature_drift_stable():
    np.random.seed(42)
    n = 250
    baseline_df = pd.DataFrame({
        "device_id": [f"dev_{i}" for i in range(n)],
        "ac_voltage": np.random.normal(230, 8, n),
        "battery_voltage": np.random.normal(12.6, 0.4, n),
        "temperature": np.random.normal(28, 3, n)
    })
    target_df = pd.DataFrame({
        "device_id": [f"dev_{i}" for i in range(n)],
        "ac_voltage": np.random.normal(230, 8, n),
        "battery_voltage": np.random.normal(12.6, 0.4, n),
        "temperature": np.random.normal(28, 3, n)
    })

    result = evaluate_feature_drift(baseline_df, target_df)
    assert result["overall_status"] == "STABLE"
    assert result["critical_drift_count"] == 0
    assert result["total_features_evaluated"] == 3


def test_evaluate_feature_drift_critical():
    np.random.seed(42)
    n = 130
    baseline_df = pd.DataFrame({
        "device_id": [f"dev_{i}" for i in range(n)],
        "ac_voltage": np.random.normal(230, 8, n),
        "battery_voltage": np.random.normal(12.6, 0.4, n),
    })
    # Severe grid drop across fleet
    target_df = pd.DataFrame({
        "device_id": [f"dev_{i}" for i in range(n)],
        "ac_voltage": np.random.normal(30, 5, n),
        "battery_voltage": np.random.normal(12.6, 0.4, n),
    })

    result = evaluate_feature_drift(baseline_df, target_df)
    assert result["overall_status"] == "CRITICAL_DRIFT"
    assert "ac_voltage" in result["critical_features"]
    assert result["critical_drift_count"] >= 1


def test_save_drift_report(tmp_path: Path):
    drift_data = {
        "overall_status": "STABLE",
        "total_features_evaluated": 2,
        "mean_psi": 0.02,
        "critical_drift_count": 0,
        "moderate_drift_count": 0,
        "stable_count": 2,
        "critical_features": [],
        "evaluated_at": "2026-10-27T10:00:00Z",
        "feature_details": {}
    }
    out_file = tmp_path / "drift.json"
    p = save_drift_report(drift_data, out_file)
    assert p.exists()

    with open(p, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["overall_status"] == "STABLE"
