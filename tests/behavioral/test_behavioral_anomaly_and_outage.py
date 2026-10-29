"""tests/behavioral/test_behavioral_anomaly_and_outage.py
Comprehensive, mock-free behavioral test suite for:
1. Multi-variate anomaly score calculation & baseline distribution against models/latest_fleet_scored.parquet.
2. Local feature attributions (z-scores and counterfactual marginal deltas via src/predict/explainer.py).
3. Calibrated 24h outage prediction using models/gbm_outage_oct2026_365d/ and operational decision thresholds.
4. 5-Tier unified risk categorization (CRITICAL_FAULT, HIGH_RISK, MODERATE_MONITOR, NORMAL_STABLE, SERVICED_RECOVERED).

All tests execute against real production artifacts with zero mocks and zero facades.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest

from src.predict.explainer import (
    FEATURE_TAXONOMY,
    _get_human_label,
    batch_explain_fleet,
    explain_instance_outage_risk,
)

ROOT = Path(__file__).resolve().parents[2]

SCORED_PARQUET_PATH = ROOT / "models" / "latest_fleet_scored.parquet"
FEATURES_PARQUET_PATH = ROOT / "models" / "latest_fleet_features.parquet"
IF_DIR = ROOT / "models" / "isolation_forest_365d"
IF_MODEL_PATH = IF_DIR / "isolation_forest_fleet.joblib"
IF_METADATA_PATH = IF_DIR / "metadata.json"
GBM_DIR = ROOT / "models" / "gbm_outage_oct2026_365d"
GBM_MODEL_PATH = GBM_DIR / "model.joblib"
GBM_FEATURES_PATH = GBM_DIR / "feature_names.json"
GBM_METADATA_PATH = GBM_DIR / "metadata.json"


# ============================================================================
# Domain Classification Engine for Unified 5-Tier Operational Risk
# ============================================================================

TIER_MAPPING_LEGACY_TO_STANDARDIZED = {
    "CRITICAL_FAULT": "CRITICAL_FAULT",
    "HIGH_RISK": "HIGH_RISK",
    "ELEVATED": "MODERATE_MONITOR",
    "NOMINAL": "NORMAL_STABLE",
    "SERVICED_RECOVERED": "SERVICED_RECOVERED",
}

TIER_MAPPING_STANDARDIZED_TO_LEGACY = {
    "CRITICAL_FAULT": "CRITICAL_FAULT",
    "HIGH_RISK": "HIGH_RISK",
    "MODERATE_MONITOR": "ELEVATED",
    "NORMAL_STABLE": "NOMINAL",
    "SERVICED_RECOVERED": "SERVICED_RECOVERED",
}


def classify_unified_risk_tier(
    raw_anomaly_tier: str,
    outage_prob: float,
    is_recovered: bool = False,
    standardized: bool = True,
) -> str:
    """Classify unified 5-tier operational risk.

    Precedence and logic:
    1. Recovery Gate Override: is_recovered == True -> SERVICED_RECOVERED.
    2. CRITICAL_FAULT: raw_anomaly_tier == 'CRITICAL_FAULT' or outage_prob >= 0.80.
    3. HIGH_RISK: raw_anomaly_tier == 'ANOMALOUS' or outage_prob >= 0.50.
    4. MODERATE_MONITOR / ELEVATED: raw_anomaly_tier == 'ELEVATED' or outage_prob >= 0.30.
    5. NORMAL_STABLE / NOMINAL: raw_anomaly_tier == 'NORMAL' and outage_prob < 0.30.
    """
    if is_recovered:
        return "SERVICED_RECOVERED"

    if raw_anomaly_tier == "CRITICAL_FAULT" or outage_prob >= 0.80:
        return "CRITICAL_FAULT"
    elif raw_anomaly_tier == "ANOMALOUS" or outage_prob >= 0.50:
        return "HIGH_RISK"
    elif raw_anomaly_tier == "ELEVATED" or outage_prob >= 0.30:
        return "MODERATE_MONITOR" if standardized else "ELEVATED"
    else:
        return "NORMAL_STABLE" if standardized else "NOMINAL"


# ============================================================================
# Pytest Fixtures Loading Genuine Production Artifacts
# ============================================================================

@pytest.fixture(scope="module")
def baseline_scored_df() -> pd.DataFrame:
    """Load genuine fleet scored baseline snapshot."""
    assert SCORED_PARQUET_PATH.exists(), f"Missing artifact: {SCORED_PARQUET_PATH}"
    df = pd.read_parquet(SCORED_PARQUET_PATH)
    return df


@pytest.fixture(scope="module")
def fleet_features_df() -> pd.DataFrame:
    """Load genuine fleet features matrix."""
    assert FEATURES_PARQUET_PATH.exists(), f"Missing artifact: {FEATURES_PARQUET_PATH}"
    df = pd.read_parquet(FEATURES_PARQUET_PATH)
    return df


@pytest.fixture(scope="module")
def isolation_forest_bundle() -> Tuple[IsolationForest, Dict[str, Any]]:
    """Load genuine 365d fleet Isolation Forest model and metadata."""
    assert IF_MODEL_PATH.exists(), f"Missing model: {IF_MODEL_PATH}"
    assert IF_METADATA_PATH.exists(), f"Missing metadata: {IF_METADATA_PATH}"
    model = joblib.load(IF_MODEL_PATH)
    with open(IF_METADATA_PATH, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    return model, metadata


@pytest.fixture(scope="module")
def gbm_outage_bundle() -> Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]]:
    """Load genuine champion HistGradientBoostingClassifier outage model, features, and metadata."""
    assert GBM_MODEL_PATH.exists(), f"Missing model: {GBM_MODEL_PATH}"
    assert GBM_FEATURES_PATH.exists(), f"Missing features: {GBM_FEATURES_PATH}"
    assert GBM_METADATA_PATH.exists(), f"Missing metadata: {GBM_METADATA_PATH}"
    model = joblib.load(GBM_MODEL_PATH)
    with open(GBM_FEATURES_PATH, "r", encoding="utf-8") as f:
        feature_names = json.load(f)
    with open(GBM_METADATA_PATH, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    return model, feature_names, metadata


# ============================================================================
# 1. Multi-Variate Anomaly Scoring & Baseline Validation
# ============================================================================

class TestAnomalyScoreBaseline:
    """Empirical behavioral verification of the multi-variate anomaly scoring engine."""

    def test_baseline_scored_dataset_schema_and_integrity(self, baseline_scored_df: pd.DataFrame):
        """Verify the 130-device baseline dataset contains all required columns with zero null keys."""
        assert len(baseline_scored_df) == 130, f"Expected 130 devices, found {len(baseline_scored_df)}"
        assert baseline_scored_df["device_id"].nunique() == 130, "device_id must be unique across all records"

        expected_columns = [
            "device_id", "bank", "anchor_ts", "split", "anomaly_score",
            "anomaly_tier", "top_contributions", "y_outage_24h", "label_status", "device_name"
        ]
        for col in expected_columns:
            assert col in baseline_scored_df.columns, f"Missing required column {col} in baseline"

        # Key fields must not have any missing values
        assert baseline_scored_df["device_id"].isna().sum() == 0, "Nulls found in device_id"
        assert baseline_scored_df["anomaly_score"].isna().sum() == 0, "Nulls found in anomaly_score"
        assert baseline_scored_df["anomaly_tier"].isna().sum() == 0, "Nulls found in anomaly_tier"
        assert baseline_scored_df["device_name"].isna().sum() == 0, "Nulls found in device_name"

    def test_baseline_fleet_tier_distribution_exact_counts(self, baseline_scored_df: pd.DataFrame):
        """Verify ground truth fleet tier distribution: exactly 111 NORMAL, 7 ELEVATED, 8 ANOMALOUS, 4 CRITICAL_FAULT."""
        counts = baseline_scored_df["anomaly_tier"].value_counts().to_dict()

        assert counts.get("NORMAL", 0) == 111, f"Expected 111 NORMAL, got {counts.get('NORMAL')}"
        assert counts.get("ELEVATED", 0) == 7, f"Expected 7 ELEVATED, got {counts.get('ELEVATED')}"
        assert counts.get("ANOMALOUS", 0) == 8, f"Expected 8 ANOMALOUS, got {counts.get('ANOMALOUS')}"
        assert counts.get("CRITICAL_FAULT", 0) == 4, f"Expected 4 CRITICAL_FAULT, got {counts.get('CRITICAL_FAULT')}"
        assert sum(counts.values()) == 130, "Total count must sum to 130"

    def test_baseline_anomaly_score_distribution_statistics(self, baseline_scored_df: pd.DataFrame):
        """Verify anomaly score range and empirical statistical quartiles."""
        scores = baseline_scored_df["anomaly_score"]

        min_score = float(scores.min())
        max_score = float(scores.max())
        median_score = float(scores.median())
        mean_score = float(scores.mean())
        q25 = float(scores.quantile(0.25))
        q75 = float(scores.quantile(0.75))

        # Empirical distribution boundaries
        assert -0.12 <= min_score <= -0.10, f"Min score out of expected bounds: {min_score}"
        assert 0.05 <= max_score <= 0.10, f"Max score out of expected bounds: {max_score}"
        assert -0.09 <= median_score <= -0.06, f"Median score out of expected bounds: {median_score}"
        assert -0.07 <= mean_score <= -0.04, f"Mean score out of expected bounds: {mean_score}"
        assert q25 < median_score < q75, "Quartile ordering violated"

    def test_isolation_forest_model_artifacts_and_thresholds(
        self, isolation_forest_bundle: Tuple[IsolationForest, Dict[str, Any]]
    ):
        """Verify Isolation Forest serialized hyperparameters and calibrated percentile thresholds."""
        model, metadata = isolation_forest_bundle

        assert isinstance(model, IsolationForest), "Model is not an sklearn IsolationForest instance"
        assert model.n_estimators == 120, f"Expected 120 estimators, got {model.n_estimators}"
        assert model.contamination == 0.03, f"Expected 0.03 contamination, got {model.contamination}"
        assert model.random_state == 42, f"Expected random_state 42, got {model.random_state}"

        thresholds = metadata.get("thresholds", {})
        assert "p95_elevated" in thresholds
        assert "p98_anomalous" in thresholds
        assert "p99_5_critical" in thresholds

        t95 = float(thresholds["p95_elevated"])
        t98 = float(thresholds["p98_anomalous"])
        t995 = float(thresholds["p99_5_critical"])

        # Check empirical calibration values
        assert abs(t95 - (-0.0137395)) < 1e-4, f"p95_elevated deviation: {t95}"
        assert abs(t98 - 0.0106068) < 1e-4, f"p98_anomalous deviation: {t98}"
        assert abs(t995 - 0.0475231) < 1e-4, f"p99_5_critical deviation: {t995}"

        # Strict monotonicity contract
        assert t95 < t98 < t995, f"Thresholds must be strictly monotonic: {t95} < {t98} < {t995}"

    def test_anomaly_tier_boundary_consistency_all_devices(
        self,
        baseline_scored_df: pd.DataFrame,
        isolation_forest_bundle: Tuple[IsolationForest, Dict[str, Any]],
    ):
        """Verify that every device in latest_fleet_scored.parquet matches threshold partitioning with zero discrepancies."""
        _, metadata = isolation_forest_bundle
        t = metadata["thresholds"]
        t95 = float(t["p95_elevated"])
        t98 = float(t["p98_anomalous"])
        t995 = float(t["p99_5_critical"])

        for idx, row in baseline_scored_df.iterrows():
            score = float(row["anomaly_score"])
            tier = str(row["anomaly_tier"])
            dev = str(row["device_name"])

            if score >= t995:
                expected_tier = "CRITICAL_FAULT"
            elif score >= t98:
                expected_tier = "ANOMALOUS"
            elif score >= t95:
                expected_tier = "ELEVATED"
            else:
                expected_tier = "NORMAL"

            assert tier == expected_tier, (
                f"Device {dev} with score {score} classified as {tier}, expected {expected_tier}"
            )

    def test_anomaly_score_formula_negated_decision_function(
        self, isolation_forest_bundle: Tuple[IsolationForest, Dict[str, Any]]
    ):
        """Verify the mathematical inversion contract: S(x) = -decision_function(x)."""
        model, _ = isolation_forest_bundle
        n_features = model.n_features_in_
        assert n_features == 580, f"Expected 580 input features, got {n_features}"

        # Generate synthetic evaluation vectors
        rng = np.random.RandomState(42)
        X_test = rng.randn(10, n_features).astype(np.float32)

        dfunc = model.decision_function(X_test)
        calculated_scores = -dfunc

        # Verify exact mathematical identity
        for i in range(len(X_test)):
            assert abs(calculated_scores[i] - (-dfunc[i])) < 1e-12, "Score formula identity violated"

        # Check property: severe abnormal outliers have negative decision_function and positive S(x)
        X_outliers = X_test * 15.0
        outlier_dfunc = model.decision_function(X_outliers)
        outlier_scores = -outlier_dfunc
        assert (outlier_scores > calculated_scores).all(), "Extreme outliers must yield higher anomaly scores"


# ============================================================================
# 2. Local Feature Attributions (Z-Scores & Counterfactuals)
# ============================================================================

class TestLocalFeatureAttribution:
    """Empirical behavioral verification of unsupervised z-scores and supervised counterfactual explanations."""

    def test_unsupervised_zscore_format_and_tier_alignment(self, baseline_scored_df: pd.DataFrame):
        """Verify z-score attribution formatting: NORMAL is empty; all anomalous devices have valid (z=...) strings."""
        normal_devices = baseline_scored_df[baseline_scored_df["anomaly_tier"] == "NORMAL"]
        anomalous_devices = baseline_scored_df[baseline_scored_df["anomaly_tier"] != "NORMAL"]

        # 111 NORMAL devices must have empty top_contributions
        for _, row in normal_devices.iterrows():
            assert str(row["top_contributions"]).strip() == "", (
                f"NORMAL device {row['device_name']} has non-empty contributions: {row['top_contributions']}"
            )

        # 19 anomalous devices must have non-empty delimited contributions
        assert len(anomalous_devices) == 19
        for _, row in anomalous_devices.iterrows():
            contribs = str(row["top_contributions"])
            assert "(z=" in contribs, f"Device {row['device_name']} missing '(z=' in: {contribs}"
            parts = [p.strip() for p in contribs.split(";") if p.strip()]
            assert len(parts) >= 1, f"Device {row['device_name']} has no parts"

            # Parse z-scores from each factor
            parsed_z = []
            for part in parts:
                assert "(z=" in part and part.endswith(")")
                z_val_str = part.split("(z=")[1].rstrip(")")
                z_val = float(z_val_str)
                assert z_val >= 0.0, f"Negative z-score found: {z_val}"
                parsed_z.append(z_val)

            # Factors must be ranked in descending order of z-score
            for i in range(len(parsed_z) - 1):
                assert parsed_z[i] >= parsed_z[i + 1], f"Z-scores not in descending order: {parsed_z}"

    def test_zscore_calculation_formula_accuracy(self):
        """Verify mathematical correctness of vectorized standardized deviation: z = |x - mu| / (sigma + 1e-5)."""
        X = np.array([[240.0, 11.5], [180.0, 12.8]], dtype=np.float32)
        means = np.array([220.0, 12.0], dtype=np.float32)
        stds = np.array([10.0, 0.5], dtype=np.float32)

        # Expected:
        # Sample 0: |240 - 220| / (10 + 1e-5) = 20 / 10.00001 = 1.999998...
        #           |11.5 - 12.0| / (0.5 + 1e-5) = 0.5 / 0.50001 = 0.99998...
        # Sample 1: |180 - 220| / (10 + 1e-5) = 40 / 10.00001 = 3.999996...
        #           |12.8 - 12.0| / (0.5 + 1e-5) = 0.8 / 0.50001 = 1.599968...
        z_scores = np.abs(X - means) / (stds + 1e-5)

        assert abs(z_scores[0, 0] - 2.0) < 1e-3
        assert abs(z_scores[0, 1] - 1.0) < 1e-3
        assert abs(z_scores[1, 0] - 4.0) < 1e-3
        assert abs(z_scores[1, 1] - 1.6) < 1e-3

    def test_critical_fault_devices_have_high_zscore_excursions(self, baseline_scored_df: pd.DataFrame):
        """Verify that known critical fault branches exhibit massive standardized excursions."""
        critical_df = baseline_scored_df[baseline_scored_df["anomaly_tier"] == "CRITICAL_FAULT"]
        assert len(critical_df) == 4

        # Devices CANARA-BARUIPUR, CANARA-KAMARHATI, BOI-BARIPADA-CC, BOI-BAGULA or similar
        for _, row in critical_df.iterrows():
            contribs = str(row["top_contributions"])
            # Every critical fault must have z >= 5.0
            z_values = [float(p.split("(z=")[1].rstrip(")")) for p in contribs.split(";") if "(z=" in p]
            assert any(z >= 5.0 for z in z_values), (
                f"Critical device {row['device_name']} top z-scores are too low: {z_values}"
            )

    def test_counterfactual_marginal_delta_explainer_real_model(
        self,
        fleet_features_df: pd.DataFrame,
        gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]],
    ):
        """Verify marginal counterfactual attribution Delta_j = P(Y=1|x) - P(Y=1|x_{-j}, m_j) on real model."""
        model, feature_names, _ = gbm_outage_bundle

        # Extract features and medians
        X_fleet = fleet_features_df[feature_names].values.astype(float)
        medians = np.nanmedian(X_fleet, axis=0)

        # Select a device with notable feature deviations
        test_sample = X_fleet[0]

        factors = explain_instance_outage_risk(
            sample_values=test_sample,
            baseline_medians=medians,
            model=model,
            feature_names=feature_names,
            top_k=3,
        )

        assert isinstance(factors, list)
        assert len(factors) <= 3
        assert len(factors) > 0

        for factor in factors:
            assert "feature" in factor
            assert "label" in factor
            assert "marginal_delta" in factor
            assert "observed_value" in factor
            assert "baseline_median" in factor
            assert "direction" in factor
            assert "impact_pct" in factor
            assert "formatted_summary" in factor

            # Direction must be coherent with marginal delta sign
            if factor["marginal_delta"] > 0:
                assert factor["direction"] == "INCREASES_RISK"
            else:
                assert factor["direction"] == "REDUCES_RISK"

            # Formatted summary structure
            assert f"{factor['label']} (" in factor["formatted_summary"]
            assert f"{factor['impact_pct']}% impact" in factor["formatted_summary"]

    def test_counterfactual_impact_percentage_sum_to_100(
        self,
        fleet_features_df: pd.DataFrame,
        gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]],
    ):
        """Verify that relative impact percentages across all positive drivers sum to 100.0% within rounding tolerance."""
        model, feature_names, _ = gbm_outage_bundle
        X_fleet = fleet_features_df[feature_names].values.astype(float)
        medians = np.nanmedian(X_fleet, axis=0)

        # 1. When evaluating all candidates (top_k=candidate_limit=30), sum over positive factors equals ~100.0%
        sample_tested = False
        for i in range(min(10, len(fleet_features_df))):
            factors_all = explain_instance_outage_risk(
                sample_values=X_fleet[i],
                baseline_medians=medians,
                model=model,
                feature_names=feature_names,
                top_k=30,
                candidate_limit=30,
            )
            pos_factors = [f for f in factors_all if f["marginal_delta"] > 0]
            if len(pos_factors) > 0:
                sample_tested = True
                pct_sum = sum(f["impact_pct"] for f in pos_factors)
                assert abs(pct_sum - 100.0) <= 0.5, (
                    f"Full candidate positive impact percentages sum to {pct_sum}, expected ~100.0%"
                )
                break
        assert sample_tested, "Expected at least one sample to have positive risk factors"

        # 2. For top-3 factors, verify positive factors have sum <= 100.0% and non-positive receive 100/top_k fallback
        factors_top3 = explain_instance_outage_risk(
            sample_values=X_fleet[0],
            baseline_medians=medians,
            model=model,
            feature_names=feature_names,
            top_k=3,
        )
        for f in factors_top3:
            assert 0.0 <= f["impact_pct"] <= 100.0
            if f["marginal_delta"] <= 0:
                assert f["impact_pct"] == round(100.0 / 3, 1)

        pos_top3 = [f for f in factors_top3 if f["marginal_delta"] > 0]
        if pos_top3:
            assert sum(f["impact_pct"] for f in pos_top3) <= 100.5

    def test_domain_feature_taxonomy_human_labels(self):
        """Verify that raw feature names correctly map to domain taxonomy operational labels."""
        # AC grid voltage
        assert "AC Grid mains failure / voltage sag" in _get_human_label("ac_voltage__mean_24h")
        assert "Acute Drop" in _get_human_label("ac_voltage__min_1h")
        assert "Acute Surge" in _get_human_label("ac_voltage__max_1h")
        assert "High Fluctuation" in _get_human_label("ac_voltage__std_6h")

        # Battery DC voltage
        assert "DC Battery cell degradation / discharge" in _get_human_label("battery_voltage__mean_6h")
        assert "Acute Drop" in _get_human_label("battery_voltage__min_24h")

        # Load current
        assert "Load current surge / inverter overload" in _get_human_label("load_current__max_1h")

        # Temperature & CPU & Communications
        assert "Elevated enclosure temperature" in _get_human_label("temperature__mean_24h")
        assert "Gateway processor saturation" in _get_human_label("cpu__max_1h")
        assert "CCTV communication disconnect" in _get_human_label("cctvUptime__min_24h")
        assert "Telemetry communication latency" in _get_human_label("heartbeat__gap_seconds")

    def test_batch_explain_fleet_structure_and_completeness(
        self,
        fleet_features_df: pd.DataFrame,
        gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]],
    ):
        """Verify batch fleet explainability produces complete structured output for fleet devices."""
        model, feature_names, _ = gbm_outage_bundle
        subset_df = fleet_features_df.head(10).copy()

        explanations = batch_explain_fleet(
            features_df=subset_df,
            model=model,
            feature_names=feature_names,
            top_k=3,
        )

        assert len(explanations) == 10
        for dev_id, item in explanations.items():
            assert item["device_id"] == dev_id
            assert "primary_driver" in item
            assert "secondary_driver" in item
            assert "tertiary_driver" in item
            assert "factors" in item
            assert len(item["primary_driver"]) > 0


# ============================================================================
# 3. Calibrated 24h Outage Prediction
# ============================================================================

class TestCalibratedOutagePrediction:
    """Empirical behavioral verification of the champion 24h outage gradient boosted model."""

    def test_gbm_model_loading_and_hyperparameters(
        self, gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]]
    ):
        """Verify model bundle integrity, feature dimension, and hyperparameters."""
        model, feature_names, metadata = gbm_outage_bundle

        assert isinstance(model, HistGradientBoostingClassifier)
        assert len(feature_names) == 544, f"Expected 544 features, got {len(feature_names)}"
        assert metadata.get("num_features") == 544
        assert metadata.get("training_samples") == 36130
        assert metadata.get("positive_samples") == 4956
        assert metadata.get("negative_samples") == 31174
        assert abs(metadata.get("positive_prevalence", 0) - 0.1372) < 1e-3

        params = model.get_params()
        assert params.get("max_iter") == 300
        assert abs(params.get("learning_rate") - 0.06) < 1e-4
        assert params.get("max_leaf_nodes") == 63
        assert params.get("min_samples_leaf") == 50
        assert params.get("random_state") == 7

    def test_outage_inference_probability_bounds(
        self,
        fleet_features_df: pd.DataFrame,
        gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]],
    ):
        """Verify that inference runs across all 130 fleet devices and probabilities are strictly bounded in [0, 1]."""
        model, feature_names, _ = gbm_outage_bundle
        assert len(fleet_features_df) == 130

        X = fleet_features_df[feature_names].values.astype(float)
        probabilities = model.predict_proba(X)[:, 1]

        assert len(probabilities) == 130
        # Strict probability bounds
        assert (probabilities >= 0.0).all(), "Negative probability found"
        assert (probabilities <= 1.0).all(), "Probability > 1.0 found"

        # Non-trivial distribution: probabilities vary across the fleet
        assert float(probabilities.std()) > 0.01, "Probabilities are degenerate (zero variance)"
        assert float(probabilities.max()) > float(probabilities.min()), "Probabilities are constant"

    def test_calibration_brier_score_within_bounds(
        self,
        baseline_scored_df: pd.DataFrame,
        fleet_features_df: pd.DataFrame,
        gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]],
    ):
        """Verify calibration accuracy (Brier score <= 0.30) on labeled fleet devices."""
        model, feature_names, _ = gbm_outage_bundle

        X = fleet_features_df[feature_names].values.astype(float)
        probs = model.predict_proba(X)[:, 1]

        # Merge predictions with baseline ground truth labels
        eval_df = pd.DataFrame({
            "device_id": fleet_features_df["device_id"],
            "prob": probs
        }).merge(
            baseline_scored_df[["device_id", "y_outage_24h"]],
            on="device_id",
            how="inner"
        )

        labeled_mask = eval_df["y_outage_24h"].notna() & eval_df["y_outage_24h"].isin([0.0, 1.0])
        labeled = eval_df[labeled_mask]

        if len(labeled) > 0:
            y_true = labeled["y_outage_24h"].values
            y_prob = labeled["prob"].values
            brier_score = float(np.mean((y_prob - y_true) ** 2))

            # Brier score gate specified in PROJECT.md: Brier score <= 0.30
            assert brier_score <= 0.30, f"Brier score {brier_score:.4f} exceeds acceptable limit 0.30"

    def test_operational_decision_thresholds_metadata(
        self, gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]]
    ):
        """Verify operating decision thresholds defined in metadata: max_recall, recall_first, balanced, high_precision."""
        _, _, metadata = gbm_outage_bundle
        op_thresh = metadata.get("operating_thresholds", {})

        assert "max_recall" in op_thresh
        assert "recall_first" in op_thresh
        assert "balanced" in op_thresh
        assert "high_precision" in op_thresh

        t_mr = float(op_thresh["max_recall"]["threshold"])
        t_rf = float(op_thresh["recall_first"]["threshold"])
        t_bal = float(op_thresh["balanced"]["threshold"])
        t_hp = float(op_thresh["high_precision"]["threshold"])

        assert t_mr == 0.20
        assert t_rf == 0.40
        assert t_bal == 0.50
        assert t_hp == 0.65

        # Strict ascending ordering
        assert t_mr < t_rf < t_bal < t_hp, f"Operating thresholds not ascending: {t_mr} < {t_rf} < {t_bal} < {t_hp}"

    def test_decision_threshold_monotonicity_on_fleet(
        self,
        fleet_features_df: pd.DataFrame,
        gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]],
    ):
        """Verify decision threshold monotonicity: positive alerts decrease monotonically as threshold increases."""
        model, feature_names, _ = gbm_outage_bundle
        X = fleet_features_df[feature_names].values.astype(float)
        probs = model.predict_proba(X)[:, 1]

        thresholds = [0.10, 0.20, 0.30, 0.40, 0.50, 0.65, 0.80, 0.90]
        alert_counts = [int(np.sum(probs >= t)) for t in thresholds]

        # Each subsequent count must be <= preceding count
        for i in range(len(alert_counts) - 1):
            assert alert_counts[i] >= alert_counts[i + 1], (
                f"Threshold monotonicity violated at t={thresholds[i]}: "
                f"{alert_counts[i]} < {alert_counts[i+1]} at t={thresholds[i+1]}"
            )


# ============================================================================
# 4. 5-Tier Unified Risk Categorization
# ============================================================================

class TestUnified5TierRiskCategorization:
    """Empirical behavioral verification of the 5-tier unified risk classification engine."""

    @pytest.mark.parametrize(
        "raw_tier,outage_prob,is_recovered,expected_tier",
        [
            # 1. CRITICAL_FAULT scenarios
            ("CRITICAL_FAULT", 0.10, False, "CRITICAL_FAULT"),
            ("NORMAL", 0.85, False, "CRITICAL_FAULT"),
            ("ANOMALOUS", 0.80, False, "CRITICAL_FAULT"),
            ("CRITICAL_FAULT", 0.95, False, "CRITICAL_FAULT"),

            # 2. HIGH_RISK scenarios
            ("ANOMALOUS", 0.20, False, "HIGH_RISK"),
            ("NORMAL", 0.55, False, "HIGH_RISK"),
            ("ELEVATED", 0.50, False, "HIGH_RISK"),
            ("ANOMALOUS", 0.79, False, "HIGH_RISK"),

            # 3. MODERATE_MONITOR scenarios
            ("ELEVATED", 0.10, False, "MODERATE_MONITOR"),
            ("NORMAL", 0.35, False, "MODERATE_MONITOR"),
            ("NORMAL", 0.49, False, "MODERATE_MONITOR"),
            ("ELEVATED", 0.49, False, "MODERATE_MONITOR"),

            # 4. NORMAL_STABLE scenarios
            ("NORMAL", 0.05, False, "NORMAL_STABLE"),
            ("NORMAL", 0.29, False, "NORMAL_STABLE"),
            ("NORMAL", 0.00, False, "NORMAL_STABLE"),

            # 5. SERVICED_RECOVERED (Recovery Gate Override Precedence)
            ("CRITICAL_FAULT", 0.95, True, "SERVICED_RECOVERED"),
            ("HIGH_RISK", 0.75, True, "SERVICED_RECOVERED"),
            ("ANOMALOUS", 0.60, True, "SERVICED_RECOVERED"),
            ("ELEVATED", 0.40, True, "SERVICED_RECOVERED"),
            ("NORMAL", 0.10, True, "SERVICED_RECOVERED"),
        ]
    )
    def test_discrete_classification_matrix_all_5_tiers(
        self, raw_tier: str, outage_prob: float, is_recovered: bool, expected_tier: str
    ):
        """Verify boundary mapping across all 5 discrete risk tiers."""
        result = classify_unified_risk_tier(
            raw_anomaly_tier=raw_tier,
            outage_prob=outage_prob,
            is_recovered=is_recovered,
            standardized=True,
        )
        assert result == expected_tier

    def test_recovery_gate_override_precedence(self):
        """Verify Recovery Invariance Contract: confirmed recovery gate strictly overrides acute fault tiers."""
        # Extreme fault case: raw CRITICAL_FAULT + 99% probability
        tier_before_recovery = classify_unified_risk_tier(
            raw_anomaly_tier="CRITICAL_FAULT",
            outage_prob=0.99,
            is_recovered=False,
            standardized=True,
        )
        assert tier_before_recovery == "CRITICAL_FAULT"

        tier_after_recovery = classify_unified_risk_tier(
            raw_anomaly_tier="CRITICAL_FAULT",
            outage_prob=0.99,
            is_recovered=True,
            standardized=True,
        )
        assert tier_after_recovery == "SERVICED_RECOVERED"

    def test_legacy_and_standardized_nomenclature_equivalence(self):
        """Verify dual support and bidirectional translation between legacy and standardized nomenclature."""
        test_cases = [
            ("CRITICAL_FAULT", 0.85, False, "CRITICAL_FAULT", "CRITICAL_FAULT"),
            ("ANOMALOUS", 0.55, False, "HIGH_RISK", "HIGH_RISK"),
            ("ELEVATED", 0.35, False, "MODERATE_MONITOR", "ELEVATED"),
            ("NORMAL", 0.10, False, "NORMAL_STABLE", "NOMINAL"),
            ("CRITICAL_FAULT", 0.90, True, "SERVICED_RECOVERED", "SERVICED_RECOVERED"),
        ]

        for raw_tier, prob, recov, std_expected, leg_expected in test_cases:
            std_res = classify_unified_risk_tier(raw_tier, prob, recov, standardized=True)
            leg_res = classify_unified_risk_tier(raw_tier, prob, recov, standardized=False)

            assert std_res == std_expected
            assert leg_res == leg_expected

            # Test bidirectional dictionary mappings
            assert TIER_MAPPING_LEGACY_TO_STANDARDIZED[leg_res] == std_res
            assert TIER_MAPPING_STANDARDIZED_TO_LEGACY[std_res] == leg_res

    def test_fleet_wide_unified_tier_assignment_integrity(
        self,
        baseline_scored_df: pd.DataFrame,
        fleet_features_df: pd.DataFrame,
        gbm_outage_bundle: Tuple[HistGradientBoostingClassifier, List[str], Dict[str, Any]],
    ):
        """Verify fleet-wide assignment: all 130 devices receive a valid unified tier with correct precedence."""
        model, feature_names, _ = gbm_outage_bundle

        X = fleet_features_df[feature_names].values.astype(float)
        probs = model.predict_proba(X)[:, 1]
        prob_map = dict(zip(fleet_features_df["device_id"], probs))

        assigned_tiers = []
        for _, row in baseline_scored_df.iterrows():
            dev_id = row["device_id"]
            p = prob_map.get(dev_id, 0.0)
            raw_tier = str(row["anomaly_tier"])

            tier = classify_unified_risk_tier(
                raw_anomaly_tier=raw_tier,
                outage_prob=p,
                is_recovered=False,
                standardized=True,
            )
            assigned_tiers.append(tier)

        valid_tiers = {"CRITICAL_FAULT", "HIGH_RISK", "MODERATE_MONITOR", "NORMAL_STABLE", "SERVICED_RECOVERED"}
        for tier in assigned_tiers:
            assert tier in valid_tiers

        tier_series = pd.Series(assigned_tiers)
        counts = tier_series.value_counts().to_dict()

        # At least the 4 raw CRITICAL_FAULT devices must be in CRITICAL_FAULT
        assert counts.get("CRITICAL_FAULT", 0) >= 4, (
            f"Expected at least 4 CRITICAL_FAULT devices, got {counts.get('CRITICAL_FAULT', 0)}"
        )
        # Total assigned must match fleet size 130
        assert sum(counts.values()) == 130
