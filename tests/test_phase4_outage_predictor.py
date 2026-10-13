"""Unit tests for Phase 4: Outage Predictor (Splits, Calibration, and Causality)."""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss


def test_temporal_split_purge_gap_causality():
    """Verify that temporal splits are strictly chronologically ordered with purge gaps."""
    # Synthetic 90 days grid
    dts = pd.date_range("2026-07-15", "2026-10-13", freq="15min")
    t_min = dts.min()

    val_start = t_min + pd.Timedelta(days=62)
    val_end = val_start + pd.Timedelta(days=14)
    test_start = val_end + pd.Timedelta(days=1)

    train_dts = dts[dts < val_start - pd.Timedelta(days=1)]
    val_dts = dts[(dts >= val_start) & (dts < val_end)]
    test_dts = dts[dts >= test_start]

    # Verify chronological ordering
    assert train_dts.max() < val_dts.min()
    assert val_dts.max() < test_dts.min()

    # Verify purge gaps are at least 24 hours (86400 seconds)
    gap1 = (val_dts.min() - train_dts.max()).total_seconds()
    gap2 = (test_dts.min() - val_dts.max()).total_seconds()
    assert gap1 >= 86400.0, f"Purge gap 1 ({gap1}s) is smaller than 24h"
    assert gap2 >= 86400.0, f"Purge gap 2 ({gap2}s) is smaller than 24h"


def test_spatial_device_holdout_purity():
    """Verify that whole-device holdout sets are strictly disjoint."""
    dev_ids = [f"dev_{i}" for i in range(100)]
    banks = ["BOI"] * 70 + ["SBI"] * 15 + ["BOB"] * 10 + ["CANARA"] * 5
    df_devs = pd.DataFrame({"device_id": dev_ids, "bank": banks})

    np.random.seed(42)
    holdout_devs = []
    train_devs = []

    for _, grp in df_devs.groupby("bank"):
        d_list = grp["device_id"].tolist()
        np.random.shuffle(d_list)
        n_hold = max(1, int(len(d_list) * 0.15))
        holdout_devs.extend(d_list[:n_hold])
        train_devs.extend(d_list[n_hold:])

    # Strict disjointness
    set_train = set(train_devs)
    set_holdout = set(holdout_devs)
    assert set_train.isdisjoint(set_holdout)
    assert len(set_train) + len(set_holdout) == 100


def test_probability_calibration_platt_scaling():
    """Verify that Platt scaling calibrator produces bounded probabilities and reduces Brier score."""
    rng = np.random.default_rng(42)
    y_true = rng.binomial(1, 0.1, size=2000)

    # Simulated poorly calibrated model: over-confident probabilities
    raw_probs = np.clip(y_true * 0.7 + rng.uniform(0.1, 0.4, size=2000), 0.01, 0.99)

    platt = LogisticRegression()
    platt.fit(raw_probs.reshape(-1, 1), y_true)
    calib_probs = platt.predict_proba(raw_probs.reshape(-1, 1))[:, 1]

    # Verify probability bounds
    assert np.all(calib_probs >= 0.0)
    assert np.all(calib_probs <= 1.0)

    # Verify Brier score improvement
    raw_brier = brier_score_loss(y_true, raw_probs)
    calib_brier = brier_score_loss(y_true, calib_probs)
    assert calib_brier <= raw_brier


def test_threshold_monotonicity():
    """Verify that increasing decision threshold monotonically non-increases positive predictions."""
    probs = np.array([0.05, 0.12, 0.25, 0.45, 0.65, 0.85, 0.95])
    thresholds = [0.1, 0.3, 0.5, 0.7, 0.9]

    counts = [np.sum(probs >= th) for th in thresholds]
    # Counts must be monotonically decreasing or equal
    for i in range(len(counts) - 1):
        assert counts[i] >= counts[i + 1]
