"""tests/test_monthly_retrainer.py
Unit tests for the autonomous monthly retraining pipeline & Champion/Challenger gate.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import pytest

from src.predict.monthly_retrainer import (
    evaluate_predictions,
    make_challenger_clf,
    execute_champion_challenger_gate,
    prepare_dataset
)


class TestMonthlyRetrainer:
    """Test suite for monthly model retraining and model governance."""

    def test_evaluate_predictions_perfect(self):
        y_true = np.array([0, 0, 1, 1])
        y_proba = np.array([0.05, 0.10, 0.90, 0.95])
        metrics = evaluate_predictions(y_true, y_proba)
        
        assert metrics["roc_auc"] == 1.0
        assert metrics["pr_auc"] == 1.0
        assert metrics["brier_score"] < 0.05
        assert metrics["recall_at_040"] == 1.0
        assert metrics["precision_at_040"] == 1.0

    def test_make_challenger_clf(self):
        clf = make_challenger_clf()
        assert clf.max_iter == 300
        assert clf.learning_rate == 0.06
        assert clf.class_weight == "balanced"
        assert clf.min_samples_leaf == 50

    def test_prepare_dataset_merging(self, tmp_path):
        fr = pd.DataFrame({
            "device_id": ["dev1", "dev2"],
            "anchor_ts": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
            "feat_a": [1.0, 2.0],
            "feat_b": [10.0, 20.0]
        })
        lb = pd.DataFrame({
            "device_id": ["dev1", "dev2"],
            "anchor_ts": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
            "y_outage_24h": [0, 1],
            "label_status": ["measured", "measured"]
        })
        
        m, feats = prepare_dataset(fr, lb)
        assert len(m) == 2
        assert "y" in m.columns
        assert m["y"].tolist() == [0, 1]
        assert "feat_a" in feats
        assert "feat_b" in feats

    @patch("src.predict.monthly_retrainer.get_s3_client")
    @patch("src.predict.monthly_retrainer.notify_slack")
    def test_execute_champion_challenger_gate_pass(self, mock_slack, mock_s3, tmp_path):
        # Create synthetic dataset with distinct separation
        np.random.seed(42)
        n = 200
        feat_1 = np.random.randn(n)
        y = (feat_1 > 0).astype(int)
        
        df = pd.DataFrame({
            "device_id": [f"dev_{i%5}" for i in range(n)],
            "anchor_ts": pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC"),
            "feat_signal": feat_1 + (y * 2.0), # Strong signal
            "feat_noise": np.random.randn(n),
            "y": y
        })
        active_feats = ["feat_signal", "feat_noise"]

        prod_dir = tmp_path / "prod_model"
        prod_dir.mkdir()
        
        # Run gate
        result = execute_champion_challenger_gate(
            m=df,
            active_feats=active_feats,
            prod_dir=prod_dir,
            force_promote=False
        )

        assert result["status"] == "promoted"
        assert (prod_dir / "model.joblib").exists()
        assert (prod_dir / "metadata.json").exists()
        assert (prod_dir / "feature_names.json").exists()

        # Check metadata
        meta = json.load(open(prod_dir / "metadata.json"))
        assert "version" in meta
        assert meta["validation_metrics"]["roc_auc"] >= 0.60
        mock_slack.assert_called_once()

    @patch("src.predict.monthly_retrainer.notify_slack")
    def test_execute_champion_challenger_gate_reject_poor_model(self, mock_slack, tmp_path):
        # Create unlearnable random noise where ROC-AUC will fail
        np.random.seed(99)
        n = 200
        df = pd.DataFrame({
            "device_id": [f"dev_{i%5}" for i in range(n)],
            "anchor_ts": pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC"),
            "feat_noise": np.random.randn(n),
            "y": np.random.randint(0, 2, size=n)
        })
        active_feats = ["feat_noise"]

        prod_dir = tmp_path / "prod_model_reject"
        prod_dir.mkdir()

        with patch("src.predict.monthly_retrainer.evaluate_predictions") as mock_eval:
            # Force simulated failing metrics
            mock_eval.return_value = {
                "roc_auc": 0.51, # Below 0.60 threshold
                "pr_auc": 0.10,
                "brier_score": 0.28, # Above 0.20 calibration limit
                "recall_at_040": 0.30,
                "precision_at_040": 0.15
            }

            result = execute_champion_challenger_gate(
                m=df,
                active_feats=active_feats,
                prod_dir=prod_dir,
                force_promote=False
            )

            assert result["status"] == "rejected"
            assert "reasons" in result
            assert not (prod_dir / "model.joblib").exists()
            mock_slack.assert_called_once()
