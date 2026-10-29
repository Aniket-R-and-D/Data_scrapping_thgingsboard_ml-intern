"""src/features/drift_monitor.py
Real-Time Telemetry & Model Input Feature Drift Monitoring Engine.

Computes Population Stability Index (PSI) and Wasserstein Distance across
incoming 15-minute live telemetry batches versus historical fleet baselines,
detecting sensor degradation, seasonal grid shifts, and distribution drift.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance

logger = logging.getLogger("drift_monitor")

DEFAULT_REPORT_PATH = Path("reports/telemetry_drift_report.json")

# Drift Categorization Thresholds (Industry Standard)
# PSI < 0.10: Stable / No significant change
# 0.10 <= PSI < 0.25: Moderate drift / Monitor closely
# PSI >= 0.25: Critical drift / Retraining or sensor calibration required
PSI_MODERATE_THRESHOLD = 0.10
PSI_CRITICAL_THRESHOLD = 0.25


def compute_psi(
    baseline: np.ndarray,
    target: np.ndarray,
    num_bins: Optional[int] = None,
    epsilon: float = 1e-4
) -> float:
    """Compute Population Stability Index (PSI) between baseline and target distributions.
    
    Formula:
        PSI = sum((P_i - Q_i) * ln(P_i / Q_i))
    where P_i is the baseline bin proportion and Q_i is the target bin proportion.
    """
    # Clean arrays
    b_clean = np.asarray(baseline, dtype=float)
    t_clean = np.asarray(target, dtype=float)
    b_clean = b_clean[np.isfinite(b_clean)]
    t_clean = t_clean[np.isfinite(t_clean)]

    if len(b_clean) == 0 or len(t_clean) == 0:
        return 0.0

    if num_bins is None:
        # Adapt bins to sample size to avoid binomial sampling noise on smaller cohorts
        num_bins = min(10, max(5, int(len(b_clean) / 40)))

    # Handle zero variance in baseline
    b_min, b_max = np.min(b_clean), np.max(b_clean)
    if np.isclose(b_min, b_max):
        t_min, t_max = np.min(t_clean), np.max(t_clean)
        if np.isclose(b_min, t_min) and np.isclose(b_max, t_max):
            return 0.0
        return 1.0  # Distribution completely shifted off constant

    # Determine quantile bin edges from baseline spanning (-inf, inf)
    quantiles = np.linspace(0, 100, num_bins + 1)
    bin_edges = np.percentile(b_clean, quantiles)
    bin_edges = np.unique(bin_edges)

    if len(bin_edges) < 2:
        return 0.0

    # Expand boundaries to negative and positive infinity to catch out-of-bounds shifted data
    bin_edges[0] = -np.inf
    bin_edges[-1] = np.inf

    # Calculate frequency counts
    b_counts, _ = np.histogram(b_clean, bins=bin_edges)
    t_counts, _ = np.histogram(t_clean, bins=bin_edges)

    # Normalize to proportions with epsilon smoothing
    p = (b_counts / len(b_clean)) + epsilon
    q = (t_counts / len(t_clean)) + epsilon

    # Re-normalize after epsilon addition
    p = p / np.sum(p)
    q = q / np.sum(q)

    # Compute PSI
    psi_val = np.sum((p - q) * np.log(p / q))
    return float(np.clip(psi_val, 0.0, 10.0))


def compute_wasserstein(baseline: np.ndarray, target: np.ndarray) -> float:
    """Compute standardized Wasserstein Distance (Earth Mover's Distance)."""
    b_clean = np.asarray(baseline, dtype=float)
    t_clean = np.asarray(target, dtype=float)
    b_clean = b_clean[np.isfinite(b_clean)]
    t_clean = t_clean[np.isfinite(t_clean)]

    if len(b_clean) == 0 or len(t_clean) == 0:
        return 0.0

    w_dist = float(wasserstein_distance(b_clean, t_clean))
    b_std = float(np.std(b_clean))
    if b_std > 1e-6:
        return float(round(w_dist / b_std, 4))
    return float(round(w_dist, 4))


def evaluate_feature_drift(
    baseline_df: pd.DataFrame,
    target_df: pd.DataFrame,
    feature_cols: Optional[List[str]] = None,
    max_features: int = 50
) -> Dict[str, Any]:
    """Evaluate drift metrics across common numerical features.
    
    Prioritizes key physical operational signals: AC voltage, DC battery voltage,
    load current, temperature, anomaly scores, and outage probabilities.
    """
    # Identify eligible numerical columns present in both frames
    common_cols = [c for c in baseline_df.columns if c in target_df.columns]
    num_cols = [
        c for c in common_cols
        if pd.api.types.is_numeric_dtype(baseline_df[c]) and pd.api.types.is_numeric_dtype(target_df[c])
        and c not in ("device_id", "timestamp", "anchor_ts")
    ]

    if feature_cols:
        candidate_cols = [c for c in feature_cols if c in num_cols]
    else:
        # Prioritize key operational signals
        key_patterns = [
            "battery_voltage", "ac_voltage", "load_current", "temperature",
            "cpu", "cctvUptime", "anomaly_score", "outage_prob", "risk_prob"
        ]
        priority_cols = []
        other_cols = []
        for c in num_cols:
            if any(p.lower() in c.lower() for p in key_patterns):
                priority_cols.append(c)
            else:
                other_cols.append(c)
        candidate_cols = (priority_cols + other_cols)[:max_features]

    results: Dict[str, Any] = {}
    stable_count = 0
    moderate_count = 0
    critical_count = 0
    critical_features: List[str] = []
    psi_scores: List[float] = []

    for col in candidate_cols:
        b_vals = baseline_df[col].dropna().values
        t_vals = target_df[col].dropna().values

        if len(b_vals) < 5 or len(t_vals) < 5:
            continue

        psi = compute_psi(b_vals, t_vals)
        w_dist = compute_wasserstein(b_vals, t_vals)
        psi_scores.append(psi)

        if psi >= PSI_CRITICAL_THRESHOLD:
            status = "CRITICAL_DRIFT"
            critical_count += 1
            critical_features.append(col)
        elif psi >= PSI_MODERATE_THRESHOLD:
            status = "MODERATE_DRIFT"
            moderate_count += 1
        else:
            status = "STABLE"
            stable_count += 1

        results[col] = {
            "psi": round(psi, 4),
            "wasserstein_std": round(w_dist, 4),
            "status": status,
            "baseline_mean": float(round(np.mean(b_vals), 4)),
            "baseline_std": float(round(np.std(b_vals), 4)),
            "target_mean": float(round(np.mean(t_vals), 4)),
            "target_std": float(round(np.std(t_vals), 4)),
        }

    # Determine overall status
    total_evaluated = len(results)
    if critical_count > 0 or (total_evaluated > 0 and (moderate_count / total_evaluated) > 0.25):
        overall_status = "CRITICAL_DRIFT"
    elif moderate_count > 0:
        overall_status = "MODERATE_DRIFT"
    else:
        overall_status = "STABLE"

    mean_psi = float(round(np.mean(psi_scores), 4)) if psi_scores else 0.0
    max_psi_feature = max(results.items(), key=lambda x: x[1]["psi"])[0] if results else None
    max_psi_val = results[max_psi_feature]["psi"] if max_psi_feature else 0.0

    summary = {
        "overall_status": overall_status,
        "total_features_evaluated": total_evaluated,
        "stable_count": stable_count,
        "moderate_drift_count": moderate_count,
        "critical_drift_count": critical_count,
        "critical_features": critical_features,
        "mean_psi": mean_psi,
        "max_psi_feature": max_psi_feature,
        "max_psi": max_psi_val,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "feature_details": results
    }

    return summary


def save_drift_report(
    drift_result: Dict[str, Any],
    output_path: Optional[Path] = None
) -> Path:
    """Save drift evaluation report to JSON file."""
    path = output_path or DEFAULT_REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(drift_result, f, indent=2)
    logger.info(f"Saved telemetry drift report to {path} (Overall: {drift_result.get('overall_status')})")
    return path


def evaluate_drift(
    baseline_df: Optional[pd.DataFrame] = None,
    target_df: Optional[pd.DataFrame] = None,
    save_report: bool = True
) -> Dict[str, Any]:
    """Top-level convenience evaluator called by autonomous cloud runners."""
    root = Path(__file__).resolve().parents[2]
    fallback_feats = root / "models/latest_fleet_features.parquet"

    if baseline_df is None:
        baseline_df = pd.read_parquet(fallback_feats) if fallback_feats.exists() else pd.DataFrame()
    if target_df is None:
        target_df = baseline_df

    res = evaluate_feature_drift(baseline_df, target_df)
    if save_report:
        save_drift_report(res, root / "reports/telemetry_drift_report.json")
    return res


def publish_drift_metrics(drift_result: Dict[str, Any]) -> bool:
    """Publish CloudWatch drift metrics to NovaEdge/TelemetryDrift namespace."""
    try:
        import boto3
        from botocore.config import Config
        cfg = Config(region_name="ap-south-1", retries={"max_attempts": 2})
        cw = boto3.client("cloudwatch", config=cfg)

        status_code_map = {"STABLE": 0, "MODERATE_DRIFT": 1, "CRITICAL_DRIFT": 2}
        status_num = status_code_map.get(drift_result.get("overall_status", "STABLE"), 0)

        metric_data = [
            {
                "MetricName": "OverallDriftSeverity",
                "Value": status_num,
                "Unit": "None"
            },
            {
                "MetricName": "MeanPSI",
                "Value": float(drift_result.get("mean_psi", 0.0)),
                "Unit": "None"
            },
            {
                "MetricName": "CriticalDriftFeatureCount",
                "Value": int(drift_result.get("critical_drift_count", 0)),
                "Unit": "Count"
            },
            {
                "MetricName": "ModerateDriftFeatureCount",
                "Value": int(drift_result.get("moderate_drift_count", 0)),
                "Unit": "Count"
            }
        ]

        cw.put_metric_data(Namespace="NovaEdge/TelemetryDrift", MetricData=metric_data)
        logger.info("Published telemetry drift metrics to CloudWatch NovaEdge/TelemetryDrift")
        return True
    except Exception as e:
        logger.debug(f"CloudWatch drift metric publishing skipped: {e}")
        return False
