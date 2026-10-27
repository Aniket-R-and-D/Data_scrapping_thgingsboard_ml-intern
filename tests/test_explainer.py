"""Unit tests for src/predict/explainer.py."""

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from src.predict.explainer import (
    _get_human_label,
    explain_instance_outage_risk,
    batch_explain_fleet,
    FEATURE_TAXONOMY
)


def test_get_human_label():
    assert "AC Grid" in _get_human_label("ac_voltage__mean_24h")
    assert "Acute Drop" in _get_human_label("ac_voltage__min_6h")
    assert "DC Battery" in _get_human_label("battery_voltage__last")
    assert "processor" in _get_human_label("cpu__mean_1h").lower()


def test_explain_instance_outage_risk():
    np.random.seed(42)
    n = 200
    # Synthetic dataset where low AC voltage causes outage (Class 1)
    ac = np.random.normal(230, 15, n)
    temp = np.random.normal(25, 5, n)
    # Target: outage when AC < 225 (sufficient positive cases)
    y = (ac < 225).astype(int)

    X = np.column_stack([ac, temp])
    feature_names = ["ac_voltage", "temperature"]

    model = HistGradientBoostingClassifier(random_state=42, min_samples_leaf=5)
    model.fit(X, y)

    baseline_medians = np.median(X, axis=0)

    # Test an instance with collapsed AC voltage (170V)
    fault_sample = np.array([170.0, 25.0])
    factors = explain_instance_outage_risk(
        sample_values=fault_sample,
        baseline_medians=baseline_medians,
        model=model,
        feature_names=feature_names,
        top_k=2
    )

    assert len(factors) == 2
    # The primary driver MUST be ac_voltage
    assert factors[0]["feature"] == "ac_voltage"
    assert factors[0]["direction"] == "INCREASES_RISK"
    assert factors[0]["marginal_delta"] > 0
    assert "impact_pct" in factors[0]
    assert "formatted_summary" in factors[0]
    assert "AC Grid mains failure" in factors[0]["formatted_summary"]


def test_batch_explain_fleet():
    np.random.seed(42)
    n = 30
    df = pd.DataFrame({
        "device_id": [f"branch_{i}" for i in range(n)],
        "ac_voltage": np.random.normal(230, 20, n),
        "temperature": np.random.normal(28, 4, n)
    })
    # Force branch_0 to have severe voltage failure
    df.loc[0, "ac_voltage"] = 35.0

    y = (df["ac_voltage"] < 180).astype(int)
    feature_names = ["ac_voltage", "temperature"]

    model = HistGradientBoostingClassifier(random_state=42, min_samples_leaf=2)
    model.fit(df[feature_names].values, y)

    explanations = batch_explain_fleet(
        features_df=df,
        model=model,
        feature_names=feature_names,
        top_k=2
    )

    assert len(explanations) == n
    assert "branch_0" in explanations
    b0 = explanations["branch_0"]
    assert "primary_driver" in b0
    assert "AC Grid" in b0["primary_driver"]
    assert len(b0["factors"]) == 2
