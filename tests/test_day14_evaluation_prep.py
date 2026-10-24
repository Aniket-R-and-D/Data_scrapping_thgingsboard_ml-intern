"""tests/test_day14_evaluation_prep.py

Tests for Day-14 evaluation preparation harness:
- Strict premature evaluation guard (<14 days)
- 24-hour closed window eligibility & exclusion accounting
- Timestep-level metrics with bootstrap confidence intervals
- Run isolation verification (rejects merged laptop data)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.live.prepare_day14_evaluation import (
    MIN_OPERATIONAL_DAYS,
    account_exclusions,
    audit_run_isolation,
    bootstrap_ci,
    check_evaluation_readiness,
    evaluate_timestep_level,
)


def test_premature_evaluation_guard():
    # Only 2 days of predictions
    now_dt = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
    preds = [
        {"pt": pd.Timestamp("2026-10-15 10:00:00", tz="UTC"), "device_id": "dev_1"},
        {"pt": pd.Timestamp("2026-10-16 10:00:00", tz="UTC"), "device_id": "dev_1"},
    ]
    df = pd.DataFrame(preds)

    readiness = check_evaluation_readiness(df, now=now_dt)

    assert readiness["ready"] is False
    assert readiness["status"] == "PREMATURE_EVALUATION_WITHHELD"
    assert readiness["days_elapsed"] < MIN_OPERATIONAL_DAYS
    assert "PREMATURE_EVALUATION_WITHHELD" in readiness["status"]


def test_mature_evaluation_guard_passes_at_14_days():
    # 15 days elapsed with closed window
    now_dt = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    preds = [
        {"pt": pd.Timestamp("2026-10-15 10:00:00", tz="UTC"), "device_id": "dev_1"},
        {"pt": pd.Timestamp("2026-10-30 10:00:00", tz="UTC"), "device_id": "dev_1"},
    ]
    df = pd.DataFrame(preds)

    readiness = check_evaluation_readiness(df, now=now_dt)

    assert readiness["ready"] is True
    assert readiness["status"] == "READY_FOR_EVALUATION"
    assert readiness["days_elapsed"] >= 14.0
    assert readiness["closed_window_predictions"] > 0


def test_audit_run_isolation():
    # Clean desktop predictions
    clean_df = pd.DataFrame([
        {"pt": pd.Timestamp("2026-10-15 10:00:00", tz="UTC"), "git_commit": "b502cfc1234", "device_id": "dev_1"},
        {"pt": pd.Timestamp("2026-10-15 10:15:00", tz="UTC"), "git_commit": "b502cfc5678", "device_id": "dev_2"},
    ])
    audit_clean = audit_run_isolation(clean_df)
    assert audit_clean["isolated"] is True
    assert len(audit_clean["violations"]) == 0

    # Contaminated predictions containing historical laptop commit
    dirty_df = pd.DataFrame([
        {"pt": pd.Timestamp("2026-10-15 10:00:00", tz="UTC"), "git_commit": "b502cfc1234", "device_id": "dev_1"},
        {"pt": pd.Timestamp("2026-10-15 10:15:00", tz="UTC"), "git_commit": "b44a775_laptop", "device_id": "dev_2"},
    ])
    audit_dirty = audit_run_isolation(dirty_df)
    assert audit_dirty["isolated"] is False
    assert len(audit_dirty["violations"]) > 0


def test_bootstrap_ci():
    # Known distribution: mean should be near 0.5
    rng = np.random.default_rng(42)
    data = rng.binomial(n=1, p=0.5, size=200)

    ci_low, ci_high = bootstrap_ci(data, lambda x: float(x.mean()), n_resamples=500, seed=42)

    assert ci_low is not None and ci_high is not None
    assert 0.35 <= ci_low <= 0.55
    assert 0.45 <= ci_high <= 0.65
    assert ci_low < ci_high


def test_evaluate_timestep_level():
    records = [
        {"y_power_outage_24h": 1, "risk_tier": "CRITICAL"},  # TP
        {"y_power_outage_24h": 1, "risk_tier": "HIGH"},      # TP
        {"y_power_outage_24h": 0, "risk_tier": "LOW"},       # TN
        {"y_power_outage_24h": 0, "risk_tier": "HIGH"},      # FP
        {"y_power_outage_24h": 1, "risk_tier": "LOW"},       # FN
    ] * 5  # 25 samples

    df = pd.DataFrame(records)
    metrics = evaluate_timestep_level(df)

    assert metrics["eligible_rows"] == 25
    assert metrics["true_positives"] == 10
    assert metrics["false_positives"] == 5
    assert metrics["false_negatives"] == 5
    assert metrics["precision"] == pytest.approx(10 / 15, rel=1e-2)
    assert metrics["recall"] == pytest.approx(10 / 15, rel=1e-2)
    assert metrics["precision_95_ci"] is not None
    assert metrics["recall_95_ci"] is not None
