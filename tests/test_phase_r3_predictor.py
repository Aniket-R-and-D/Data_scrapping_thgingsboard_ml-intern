"""tests/test_phase_r3_predictor.py
Unit tests for Phase R3 Remediated Outage Predictor.
"""

from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import pytest

from src.predict.train_remediated_predictor import CLEAN_FEATURES, ABLATION_FEATURES

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models/outage_predictor"
REPORT_FILE = ROOT / "reports/phase_r3_predictor_remediation.json"


def test_clean_features_excludes_heartbeat_gap():
    assert "ablation_heartbeat_gap_seconds" not in CLEAN_FEATURES
    assert "heartbeat_gap_seconds" not in CLEAN_FEATURES
    assert "ablation_heartbeat_gap_seconds" in ABLATION_FEATURES


def test_remediated_models_serialized():
    model_path = MODELS_DIR / "remediated_lightgbm_clean.joblib"
    calibrator_path = MODELS_DIR / "remediated_platt_calibrator.joblib"
    assert model_path.exists(), "Remediated LightGBM model must be serialized"
    assert calibrator_path.exists(), "Remediated Platt calibrator must be serialized"

    model = joblib.load(model_path)
    calibrator = joblib.load(calibrator_path)
    assert hasattr(model, "predict_proba")
    assert hasattr(calibrator, "predict_proba")


def test_remediated_benchmark_report_metrics():
    import json
    assert REPORT_FILE.exists(), "phase_r3_predictor_remediation.json must exist"
    with open(REPORT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    summary = data["evaluation_summary"]
    assert summary["test_set_samples"] > 100000
    assert summary["test_set_positives"] > 1000

    models = {m["model_name"]: m for m in data["models_comparison"]}
    clean_op_a = models["LightGBM Clean (High-Recall Warning Feed)"]
    rules = models["Deterministic Rules Baseline"]
    ablation = models["LightGBM Ablation (WITH Heartbeat Feature)"]

    # Assert LightGBM beats rules on precision
    assert clean_op_a["precision"] > rules["precision"]
    # Assert LightGBM beats rules on incident-level event recall
    assert clean_op_a["incident_recall_pct"] > rules["incident_recall_pct"]
    # Assert ablation quantified the leakage lift
    assert ablation["pr_auc"] >= clean_op_a["pr_auc"]


def test_bootstrap_ci_structure():
    import json
    with open(REPORT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    cis = data["bootstrap_confidence_intervals_op_a"]
    for metric in ["precision_ci_95", "recall_ci_95", "f1_ci_95", "pr_auc_ci_95"]:
        assert metric in cis
        low, high = cis[metric]
        assert 0.0 <= low <= high <= 1.0
