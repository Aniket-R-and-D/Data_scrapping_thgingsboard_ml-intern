"""tests/test_model_comparator.py
Unit tests for ML model comparison, benchmarking, bootstrapping, and slice error analysis.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from src.predict.model_comparator import (
    bootstrap_ci,
    compare_models,
    compute_slice_metrics,
    extract_bank_cohort,
    save_benchmark_report,
)


def test_bootstrap_ci_returns_valid_interval():
    rng = np.random.default_rng(42)
    y_true = np.array([0] * 50 + [1] * 50)
    # Add noise to true labels
    y_proba = y_true * 0.8 + rng.uniform(0.0, 0.2, size=len(y_true))

    pt, lower, upper = bootstrap_ci(y_true, y_proba, roc_auc_score, n_bootstraps=50)

    assert 0.80 <= pt <= 1.0
    assert lower <= pt <= upper
    assert 0.0 <= lower <= 1.0
    assert 0.0 <= upper <= 1.0


def test_extract_bank_cohort():
    assert extract_bank_cohort("BOI-BABAI") == "BOI"
    assert extract_bank_cohort("SBI-MAIN-BRANCH") == "SBI"
    assert extract_bank_cohort("BOB-REGIONAL") == "BOB"
    assert extract_bank_cohort("CANARA-BARUIPUR") == "CANARA"
    assert extract_bank_cohort("DX2-UNKNOWN") == "OTHER"


def test_compute_slice_metrics():
    y_true = np.array([0, 1, 0, 1, 0, 1, 0, 0, 1, 1] * 2)
    y_proba = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.7, 0.2, 0.1, 0.85, 0.95] * 2)
    cohorts = ["BOI"] * 10 + ["SBI"] * 10

    slices = compute_slice_metrics(y_true, y_proba, cohorts)

    assert "BOI" in slices
    assert "SBI" in slices
    assert slices["BOI"]["samples"] == 10
    assert slices["BOI"]["roc_auc"] > 0.80
    assert 0.0 <= slices["BOI"]["brier_score"] <= 1.0


def test_compare_models_runs_full_benchmarking():
    # Construct synthetic chronological dataset
    n_train, n_val = 120, 60
    rng = np.random.default_rng(42)

    feats = [f"f_{i}" for i in range(5)]
    
    # Training data
    train_data = {f: rng.normal(size=n_train) for f in feats}
    # Signal: positive when f_0 + f_1 > 0
    train_y = ((train_data["f_0"] + train_data["f_1"] + rng.normal(scale=0.5, size=n_train)) > 0).astype(int)
    train_data["y"] = train_y
    train_data["device_name"] = ["BOI-BRANCH"] * 80 + ["SBI-BRANCH"] * 40
    train_df = pd.DataFrame(train_data)

    # Validation data (chronologically later)
    val_data = {f: rng.normal(size=n_val) for f in feats}
    val_y = ((val_data["f_0"] + val_data["f_1"] + rng.normal(scale=0.5, size=n_val)) > 0).astype(int)
    val_data["y"] = val_y
    val_data["device_name"] = ["BOI-BRANCH"] * 40 + ["SBI-BRANCH"] * 20
    val_df = pd.DataFrame(val_data)

    report = compare_models(
        train_df=train_df,
        val_df=val_df,
        feature_cols=feats,
        target_col="y",
        cohort_col="device_name",
        n_bootstraps=20,  # Fast for test suite
    )

    assert report["train_samples"] == n_train
    assert report["validation_samples"] == n_val
    assert "naive_baseline" in report["models"]
    assert "logistic_l2_baseline" in report["models"]
    assert "random_forest" in report["models"]
    assert "hist_gradient_boosting" in report["models"]

    # Verify GBDT outperforms naive baseline
    gbdt_pr = report["models"]["hist_gradient_boosting"]["pr_auc"]["point_estimate"]
    naive_pr = report["models"]["naive_baseline"]["pr_auc"]["point_estimate"]
    assert gbdt_pr >= naive_pr

    # Check slice metrics inside report
    gbdt_slices = report["models"]["hist_gradient_boosting"]["slice_metrics"]
    assert "BOI" in gbdt_slices


def test_save_benchmark_report(tmp_path):
    report_dict = {
        "best_performing_model": "hist_gradient_boosting",
        "best_pr_auc": 0.85,
        "models": {},
    }
    out_file = tmp_path / "test_benchmark.json"
    saved = save_benchmark_report(report_dict, output_path=out_file)

    assert saved.exists()
    with open(saved, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["best_performing_model"] == "hist_gradient_boosting"
