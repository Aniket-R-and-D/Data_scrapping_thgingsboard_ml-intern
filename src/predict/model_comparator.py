"""src/predict/model_comparator.py
Machine Learning Model Comparison, Benchmarking & Statistical Evaluation Engine.

Adheres strictly to ml-best-practices:
1. Strict Featurization Ordering: Enforces chronological splitting BEFORE fitting
   any preprocessor, scaler, or imputation transformer.
2. Multi-Model Benchmarking:
   - Naive Majority/Prior Baseline
   - Regularized Linear Baseline (Logistic Regression + L2)
   - Tree Ensemble Baseline (Balanced Random Forest)
   - Gradient Boosting Champion (HistGradientBoostingClassifier)
   - Calibrated Predictor (Platt Scaling via CalibratedClassifierCV)
3. Statistical Rigor: Computes 95% Bootstrap Confidence Intervals (250 resamples)
   for business-critical metrics (PR-AUC, ROC-AUC, Brier score, Recall@0.40, Precision@0.40).
4. Slice-Based Error Analysis: Evaluates demographic/tenant equity across bank
   cohorts (BOI, SBI, BOB, Canara) to ensure consistent performance.
5. Permutation Feature Importance: Extracts top predictive drivers on holdout data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger("model_comparator")


@dataclass
class MetricWithCI:
    metric_name: str
    point_estimate: float
    ci_lower: float
    ci_upper: float


@dataclass
class ModelEvaluationResult:
    model_name: str
    model_family: str
    train_duration_seconds: float
    roc_auc: MetricWithCI
    pr_auc: MetricWithCI
    brier_score: MetricWithCI
    recall_at_040: float
    precision_at_040: float
    confusion_matrix_at_040: List[List[int]]
    slice_metrics: Dict[str, Dict[str, float]]
    top_feature_importances: Dict[str, float]


def bootstrap_ci(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    metric_fn: Callable[[np.ndarray, np.ndarray], float],
    n_bootstraps: int = 250,
    confidence_level: float = 0.95,
    seed: int = 42,
) -> Tuple[float, float, float]:
    """Calculate point estimate and empirical bootstrap confidence intervals."""
    point_estimate = float(metric_fn(y_true, y_proba))
    rng = np.random.default_rng(seed)
    n = len(y_true)
    boot_scores: List[float] = []

    for _ in range(n_bootstraps):
        idx = rng.choice(n, size=n, replace=True)
        # Ensure bootstrap resample has at least one positive and one negative sample
        if len(np.unique(y_true[idx])) < 2:
            continue
        try:
            score = float(metric_fn(y_true[idx], y_proba[idx]))
            boot_scores.append(score)
        except Exception:
            continue

    if not boot_scores:
        return point_estimate, point_estimate, point_estimate

    alpha = (1.0 - confidence_level) / 2.0
    lower = float(np.percentile(boot_scores, 100 * alpha))
    upper = float(np.percentile(boot_scores, 100 * (1.0 - alpha)))
    return round(point_estimate, 4), round(lower, 4), round(upper, 4)


def extract_bank_cohort(device_id_or_name: str) -> str:
    """Extract bank tenant code from branch name or device id."""
    name_upper = str(device_id_or_name).upper()
    if "BOI" in name_upper:
        return "BOI"
    elif "SBI" in name_upper:
        return "SBI"
    elif "BOB" in name_upper:
        return "BOB"
    elif "CANARA" in name_upper:
        return "CANARA"
    return "OTHER"


def compute_slice_metrics(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    cohort_labels: List[str],
) -> Dict[str, Dict[str, float]]:
    """Compute slice-based performance across bank cohorts."""
    cohort_series = pd.Series(cohort_labels)
    unique_cohorts = cohort_series.unique()
    slice_results: Dict[str, Dict[str, float]] = {}

    for cohort in unique_cohorts:
        mask = (cohort_series == cohort).to_numpy()
        sub_y_true = y_true[mask]
        sub_y_proba = y_proba[mask]

        if len(sub_y_true) < 5 or len(np.unique(sub_y_true)) < 2:
            slice_results[cohort] = {
                "samples": int(len(sub_y_true)),
                "positive_rate": round(float(np.mean(sub_y_true)), 3) if len(sub_y_true) > 0 else 0.0,
                "note": "Insufficient samples/positives for full slice metrics",
            }
            continue

        try:
            r_auc = round(float(roc_auc_score(sub_y_true, sub_y_proba)), 4)
            p_auc = round(float(average_precision_score(sub_y_true, sub_y_proba)), 4)
            brier = round(float(brier_score_loss(sub_y_true, sub_y_proba)), 4)
            y_pred = (sub_y_proba >= 0.40).astype(int)
            rec = round(float(recall_score(sub_y_true, y_pred, zero_division=0)), 4)
            prec = round(float(precision_score(sub_y_true, y_pred, zero_division=0)), 4)

            slice_results[cohort] = {
                "samples": int(len(sub_y_true)),
                "positive_rate": round(float(np.mean(sub_y_true)), 3),
                "roc_auc": r_auc,
                "pr_auc": p_auc,
                "brier_score": brier,
                "recall_at_040": rec,
                "precision_at_040": prec,
            }
        except Exception as e:
            slice_results[cohort] = {"samples": int(len(sub_y_true)), "error": str(e)}

    return slice_results


class NaiveMajorityBaseline:
    """Predicts empirical training class distribution."""
    def __init__(self):
        self.prior_ = 0.5

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.prior_ = float(np.mean(y))
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        probas = np.full((len(X), 2), 0.5)
        probas[:, 1] = self.prior_
        probas[:, 0] = 1.0 - self.prior_
        return probas


def build_candidate_models() -> Dict[str, Tuple[str, Any]]:
    """Define diverse candidate model architectures for comparison."""
    return {
        "naive_baseline": (
            "Naive Majority",
            NaiveMajorityBaseline(),
        ),
        "logistic_l2_baseline": (
            "Linear Baseline",
            Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(C=0.1, class_weight="balanced", max_iter=500, random_state=42)),
            ]),
        ),
        "random_forest": (
            "Tree Ensemble",
            Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("clf", RandomForestClassifier(n_estimators=100, max_depth=10, min_samples_leaf=10, class_weight="balanced", random_state=42, n_jobs=-1)),
            ]),
        ),
        "hist_gradient_boosting": (
            "Gradient Boosting",
            HistGradientBoostingClassifier(
                max_iter=300,
                learning_rate=0.06,
                max_leaf_nodes=63,
                min_samples_leaf=50,
                l2_regularization=1.0,
                class_weight="balanced",
                random_state=42,
            ),
        ),
    }


def compare_models(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    feature_cols: List[str],
    target_col: str = "y",
    cohort_col: Optional[str] = "device_name",
    n_bootstraps: int = 250,
) -> Dict[str, Any]:
    """Execute complete ML best practices model comparison on chronological splits."""
    import time

    # Featurization ordering: extract raw matrices from fixed chronological splits
    X_train = train_df[feature_cols].to_numpy(dtype=np.float64)
    y_train = train_df[target_col].to_numpy(dtype=int)
    X_val = val_df[feature_cols].to_numpy(dtype=np.float64)
    y_val = val_df[target_col].to_numpy(dtype=int)

    # Determine bank cohorts for slice analysis
    if cohort_col and cohort_col in val_df.columns:
        cohort_labels = [extract_bank_cohort(str(x)) for x in val_df[cohort_col]]
    elif "device_id" in val_df.columns:
        cohort_labels = [extract_bank_cohort(str(x)) for x in val_df["device_id"]]
    else:
        cohort_labels = ["FLEET"] * len(val_df)

    candidates = build_candidate_models()
    evaluation_records: Dict[str, Dict[str, Any]] = {}
    best_model_name: Optional[str] = None
    best_pr_auc: float = -1.0

    for model_key, (family, estimator) in candidates.items():
        logger.info(f"Training and evaluating candidate: {family} ({model_key})...")
        t0 = time.time()
        estimator.fit(X_train, y_train)
        duration = round(time.time() - t0, 2)

        # Generate validation probabilities
        val_proba = estimator.predict_proba(X_val)[:, 1]

        # Compute Point Estimates with 95% Bootstrap CIs
        roc_pt, roc_l, roc_u = bootstrap_ci(y_val, val_proba, roc_auc_score, n_bootstraps=n_bootstraps)
        pr_pt, pr_l, pr_u = bootstrap_ci(y_val, val_proba, average_precision_score, n_bootstraps=n_bootstraps)
        br_pt, br_l, br_u = bootstrap_ci(y_val, val_proba, brier_score_loss, n_bootstraps=n_bootstraps)

        # Operational metrics at 0.40 threshold
        val_preds = (val_proba >= 0.40).astype(int)
        rec_040 = round(float(recall_score(y_val, val_preds, zero_division=0)), 4)
        prec_040 = round(float(precision_score(y_val, val_preds, zero_division=0)), 4)
        cm = confusion_matrix(y_val, val_preds).tolist()

        # Slice-based evaluation
        slices = compute_slice_metrics(y_val, val_proba, cohort_labels)

        # Top permutation feature importances for top models
        feat_importances = {}
        if model_key in ("hist_gradient_boosting", "random_forest"):
            try:
                # Run sample permutation importance
                perm_res = permutation_importance(estimator, X_val[:500], y_val[:500], n_repeats=3, random_state=42)
                top_idx = np.argsort(perm_res.importances_mean)[-10:][::-1]
                for idx in top_idx:
                    feat_importances[feature_cols[idx]] = round(float(perm_res.importances_mean[idx]), 4)
            except Exception as e:
                logger.debug(f"Permutation importance skipped: {e}")

        result_obj = ModelEvaluationResult(
            model_name=model_key,
            model_family=family,
            train_duration_seconds=duration,
            roc_auc=MetricWithCI("ROC-AUC", roc_pt, roc_l, roc_u),
            pr_auc=MetricWithCI("PR-AUC", pr_pt, pr_l, pr_u),
            brier_score=MetricWithCI("Brier Score", br_pt, br_l, br_u),
            recall_at_040=rec_040,
            precision_at_040=prec_040,
            confusion_matrix_at_040=cm,
            slice_metrics=slices,
            top_feature_importances=feat_importances,
        )

        evaluation_records[model_key] = asdict(result_obj)

        if pr_pt > best_pr_auc:
            best_pr_auc = pr_pt
            best_model_name = model_key

    # Also test probability calibration on the champion GBDT
    gbdt_est = candidates["hist_gradient_boosting"][1]
    logger.info("Evaluating Platt-Calibrated GBDT via Isotonic/Sigmoid calibration...")
    try:
        # Split half of val for calibration, half for testing
        calib_split = len(val_df) // 2
        calib_val_df = val_df.iloc[:calib_split]
        test_val_df = val_df.iloc[calib_split:]

        X_calib = calib_val_df[feature_cols].to_numpy(dtype=np.float64)
        y_calib = calib_val_df[target_col].to_numpy(dtype=int)
        X_test = test_val_df[feature_cols].to_numpy(dtype=np.float64)
        y_test = test_val_df[target_col].to_numpy(dtype=int)

        calibrator = CalibratedClassifierCV(estimator=gbdt_est, method="sigmoid", cv="prefit")
        calibrator.fit(X_calib, y_calib)
        calibrated_proba = calibrator.predict_proba(X_test)[:, 1]
        raw_proba = gbdt_est.predict_proba(X_test)[:, 1]

        raw_brier = round(float(brier_score_loss(y_test, raw_proba)), 4)
        calib_brier = round(float(brier_score_loss(y_test, calibrated_proba)), 4)
        evaluation_records["calibration_study"] = {
            "raw_brier_score": raw_brier,
            "calibrated_brier_score": calib_brier,
            "brier_improvement_pct": round(((raw_brier - calib_brier) / raw_brier) * 100, 2) if raw_brier > 0 else 0.0,
        }
    except Exception as ex:
        logger.warning(f"Could not complete calibration study: {ex}")

    summary_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "train_samples": len(train_df),
        "validation_samples": len(val_df),
        "num_features": len(feature_cols),
        "best_performing_model": best_model_name,
        "best_pr_auc": best_pr_auc,
        "models": evaluation_records,
    }

    return summary_report


def save_benchmark_report(
    summary_report: Dict[str, Any],
    output_path: Optional[Path] = None,
) -> Path:
    """Save model benchmark report to JSON artifact."""
    if output_path is None:
        root_dir = Path(__file__).resolve().parents[2]
        output_path = root_dir / "reports/model_comparison_benchmark.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary_report, f, indent=2)

    logger.info(f"Saved model comparison benchmark report to {output_path}")
    return output_path
