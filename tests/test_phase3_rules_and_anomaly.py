"""Unit tests for Phase 3: Rules and Anomaly Baseline."""

import numpy as np
import pandas as pd
import pytest
import torch

from src.anomaly.train_autoencoder import AnomalyAutoencoder
from src.anomaly.train_isolation_forest import explain_anomalies_vectorized
from src.rules.eval_rules import apply_rules


def test_deterministic_rules_boundary_triggers():
    """Verify exact boundary conditions for deterministic physical rules."""
    config = {
        "rules": {
            "ac_zero_volts": 0.0,
            "ac_surge_volts": 250.0,
            "battery_critical_volts": 10.8,
            "heartbeat_timeout_seconds": 1800.0,
            "storage_error_threshold": 1.0,
        }
    }

    df = pd.DataFrame({
        "missing_indicator": [0, 1, 0, 0, 0, 0],
        "ac_voltage": [0.0, 0.0, 260.0, 220.0, 220.0, 220.0],
        "ac_voltage_max_1h": [0.0, 0.0, 260.0, 220.0, 220.0, 220.0],
        "battery_voltage": [13.2, 0.0, 13.0, 10.5, 14.0, 14.0],
        "time_since_mains_loss_hours": [24.0, 0.0, 50.0, 12.0, 0.0, 10.0],
        "heartbeat_gap_seconds": [300.0, 86400.0, 600.0, 900.0, 120.0, 3600.0],
        "storage_fault_flag": [0, 0, 0, 0, 0, 1],
    })

    rules = apply_rules(df, config)

    # Row 0: Active AC zero (ac=0, missing=0) -> rule_ac_zero fired
    assert rules.loc[0, "rule_ac_zero"] == True
    assert rules.loc[0, "rule_ac_surge"] == False
    assert rules.loc[0, "rule_combined"] == True

    # Row 1: Missing telem (missing=1) -> rule_ac_zero False, rule_mains_lost True, rule_hb True
    assert rules.loc[1, "rule_ac_zero"] == False
    assert rules.loc[1, "rule_mains_lost"] == True
    assert rules.loc[1, "rule_heartbeat_gap"] == True

    # Row 2: AC surge (260V > 250V) -> rule_ac_surge True
    assert rules.loc[2, "rule_ac_surge"] == True

    # Row 3: Battery critical (10.5V < 10.8V, missing=0) -> rule_battery_critical True
    assert rules.loc[3, "rule_battery_critical"] == True

    # Row 4: Mains disconnected (time_since_mains=0) -> rule_mains_lost True
    assert rules.loc[4, "rule_mains_lost"] == True

    # Row 5: Heartbeat gap (3600s > 1800s) & storage fault (flag=1) -> both True
    assert rules.loc[5, "rule_heartbeat_gap"] == True
    assert rules.loc[5, "rule_storage_fault"] == True
    assert rules.loc[5, "rule_combined"] == True


def test_isolation_forest_explanation_ranking():
    """Verify feature explanations sort descending by standardized z-score deviation."""
    feature_names = ["ac_volt", "batt_volt", "mains_loss", "hb_gap"]
    means = np.array([220.0, 13.0, 10.0, 300.0])
    stds = np.array([10.0, 1.0, 5.0, 100.0])

    # Sample with extreme hb_gap (3300s -> z=30) and batt_volt (10.0V -> z=3.0)
    X = np.array([[220.0, 10.0, 10.0, 3300.0]])

    explanations = explain_anomalies_vectorized(X, feature_names, means, stds, top_k=2)

    assert len(explanations) == 1
    sample_expl = explanations[0]
    assert len(sample_expl) == 2

    # Top feature must be hb_gap with largest z-score
    assert sample_expl[0][0] == "hb_gap"
    assert sample_expl[0][1] == pytest.approx(30.0, rel=1e-2)

    # Second feature must be batt_volt (z=3.0)
    assert sample_expl[1][0] == "batt_volt"
    assert sample_expl[1][1] == pytest.approx(3.0, rel=1e-2)


def test_autoencoder_reconstruction_shape_and_convergence():
    """Verify PyTorch Autoencoder forward pass preserves dimension and reduces loss."""
    input_dim = 20
    model = AnomalyAutoencoder(input_dim=input_dim, latent_dim=6)
    model.eval()

    dummy_input = torch.randn(16, input_dim)
    with torch.no_grad():
        output = model(dummy_input)

    assert output.shape == (16, input_dim)

    # Verify reconstruction loss is non-negative and differentiable
    model.train()
    criterion = torch.nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)

    x = torch.randn(32, input_dim)
    initial_loss = criterion(model(x), x).item()

    for _ in range(5):
        optimizer.zero_grad()
        loss = criterion(model(x), x)
        loss.backward()
        optimizer.step()

    final_loss = criterion(model(x), x).item()
    assert final_loss <= initial_loss


def test_false_alarm_episode_clustering():
    """Verify episode clustering collapses contiguous false positive steps."""
    # 5 consecutive FP steps should form exactly 1 episode
    is_fp = pd.Series([0, 1, 1, 1, 1, 1, 0, 0, 1, 1, 0])
    dev_series = pd.Series(["dev1"] * len(is_fp))

    fp_starts = (is_fp == 1) & (
        (is_fp.shift(1, fill_value=0) == 0) | (dev_series != dev_series.shift(1))
    )
    assert fp_starts.sum() == 2  # exactly 2 episodes
