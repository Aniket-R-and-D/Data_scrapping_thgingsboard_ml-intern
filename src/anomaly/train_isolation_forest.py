"""src/anomaly/train_isolation_forest.py
Unsupervised Anomaly Detection engine using Isolation Forest.

Key implementation principles:
1. Trained per bank group (BOI, SBI, BOB, CANARA) on normal periods only (y_outage_24h == 0).
2. Uses causal window features only.
3. Outputs continuous anomaly score (-decision_function) and top contributing features.
4. Evaluates precision, recall, false alarms per device per week, and lead time.
5. Saves serialized models to models/isolation_forest/ and scored matrix to data/processed/.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import IsolationForest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.anomaly.isolation_forest")

FEATURE_COLS = [
    "ac_voltage",
    "ac_voltage_mean_1h",
    "ac_voltage_std_1h",
    "ac_voltage_mean_6h",
    "ac_voltage_std_6h",
    "ac_voltage_slope_6h",
    "ac_zscore_7d",
    "battery_voltage",
    "battery_voltage_mean_1h",
    "battery_voltage_std_1h",
    "battery_voltage_mean_6h",
    "battery_discharge_rate_1h",
    "battery_discharge_rate_6h",
    "battery_zscore_7d",
    "heartbeat_gap_seconds",
    "telemetry_age_seconds",
    "missing_indicator",
    "time_since_mains_loss_hours",
    "mains_loss_count_7d",
    "fas_fault_flag",
]


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def explain_anomalies_vectorized(
    X: np.ndarray,
    feature_names: List[str],
    means: np.ndarray,
    stds: np.ndarray,
    top_k: int = 3,
) -> List[List[Tuple[str, float]]]:
    """Calculate top contributing features per sample via standardized distance from normal centroid."""
    z_scores = np.abs(X - means) / (stds + 1e-5)
    top_indices = np.argsort(-z_scores, axis=1)[:, :top_k]

    explanations = []
    for i in range(len(X)):
        sample_top = [
            (feature_names[idx], round(float(z_scores[i, idx]), 2))
            for idx in top_indices[i]
        ]
        explanations.append(sample_top)
    return explanations


def train_and_score_isolation_forest() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    config = load_config()
    features_path = Path(config["paths"]["data_processed"]) / "features_matrix.parquet"
    events_path = Path(config["paths"]["data_processed"]) / "outage_events.parquet"
    registry_path = Path(config["paths"]["data_raw_registry"]) / "devices.parquet"
    models_dir = Path(config["paths"]["models_dir"]) / "isolation_forest"
    reports_dir = Path(config["paths"]["reports_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading features from {features_path}...")
    df = pd.read_parquet(features_path)

    # Attach bank metadata
    devs = pd.read_parquet(registry_path)
    bank_map = devs.set_index("device_id")["bank"].to_dict()
    df["bank"] = df["device_id"].map(bank_map).fillna("UNKNOWN")

    iso_cfg = config.get("anomaly_model", {})
    contamination = float(iso_cfg.get("contamination", 0.05))
    n_estimators = int(iso_cfg.get("n_estimators", 100))
    random_state = int(iso_cfg.get("random_state", 42))

    df["anomaly_score"] = 0.0
    group_stats: Dict[str, Dict[str, Any]] = {}

    target_banks = config.get("fleet", {}).get("target_banks", ["BOI", "SBI", "BOB", "CANARA"])

    for bank in target_banks:
        mask = df["bank"] == bank
        sub_df = df[mask]
        if sub_df.empty:
            continue

        normal_mask = sub_df["y_outage_24h"] == 0
        train_normal = sub_df[normal_mask][FEATURE_COLS]

        # Capped subsampling for fast, deterministic training
        if len(train_normal) > 100000:
            train_X = train_normal.sample(n=100000, random_state=random_state)
        else:
            train_X = train_normal

        logger.info(f"Training Isolation Forest for bank {bank} on {len(train_X)} normal samples...")
        iso_model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=random_state,
            n_jobs=-1,
        )
        iso_model.fit(train_X)

        # Save model
        model_path = models_dir / f"isolation_forest_{bank}.joblib"
        joblib.dump(iso_model, model_path)

        # Continuous anomaly score (higher = more anomalous)
        # decision_function outputs negative values for anomalies, so -decision_function is positive for anomalies
        scores = -iso_model.decision_function(sub_df[FEATURE_COLS])
        df.loc[mask, "anomaly_score"] = scores

        # Calculate normal baseline centroids for explanation
        means = train_X.mean().values
        stds = train_X.std().values
        group_stats[bank] = {
            "means": means,
            "stds": stds,
            "threshold_95": float(np.percentile(scores[normal_mask.values], 95)),
            "threshold_98": float(np.percentile(scores[normal_mask.values], 98)),
            "threshold_99": float(np.percentile(scores[normal_mask.values], 99)),
        }

    logger.info("Computing top feature contributions for high-anomaly rows...")
    # Generate explanations for top 5% anomalous rows
    top_thresh = df["anomaly_score"].quantile(0.95)
    flagged_mask = df["anomaly_score"] >= top_thresh
    df["top_contributing_features"] = ""

    for bank, stats in group_stats.items():
        bank_flagged = (df["bank"] == bank) & flagged_mask
        if bank_flagged.sum() > 0:
            X_flagged = df.loc[bank_flagged, FEATURE_COLS].values
            expls = explain_anomalies_vectorized(
                X_flagged, FEATURE_COLS, stats["means"], stats["stds"], top_k=3
            )
            df.loc[bank_flagged, "top_contributing_features"] = [
                "; ".join([f"{k} (z={v})" for k, v in item]) for item in expls
            ]

    # Save scored matrix
    scores_out = Path(config["paths"]["data_processed"]) / "anomaly_scores.parquet"
    out_cols = [
        "device_id",
        "device_name",
        "bank",
        "timestamp",
        "y_outage_24h",
        "anomaly_score",
        "top_contributing_features",
    ] + FEATURE_COLS
    df[out_cols].to_parquet(scores_out, index=False)
    logger.info(f"Saved scored matrix to {scores_out}.")

    # Evaluate across thresholds
    events_df = pd.read_parquet(events_path)
    eval_metrics = evaluate_isolation_forest_metrics(df, events_df)

    metrics_out = reports_dir / "phase_3_isolation_forest_metrics.json"
    with open(metrics_out, "w", encoding="utf-8") as f:
        json.dump(eval_metrics, f, indent=2)
    logger.info(f"Isolation Forest metrics saved to {metrics_out}.")

    return df, eval_metrics


def evaluate_isolation_forest_metrics(
    df: pd.DataFrame,
    events_df: pd.DataFrame,
) -> Dict[str, Any]:
    """Evaluate Isolation Forest across standard decision thresholds."""
    y = df["y_outage_24h"].values
    scores = df["anomaly_score"].values
    num_devices = df["device_id"].nunique()

    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    t_min = timestamps.min()
    t_max = timestamps.max()
    duration_weeks = max(1.0, (t_max - t_min).total_seconds() / (7.0 * 86400.0))

    # Pre-index device timelines
    df_eval = df[["device_id", "anomaly_score"]].copy()
    df_eval["dt"] = timestamps
    dev_timelines = {str(did): g.set_index("dt").sort_index() for did, g in df_eval.groupby("device_id")}

    events_clean = events_df.copy()
    events_clean["start_dt"] = pd.to_datetime(events_clean["start_ts"], unit="ms", utc=True)
    valid_events = events_clean[
        (events_clean["start_dt"] >= t_min + pd.Timedelta(hours=24))
        & (events_clean["start_dt"] <= t_max)
    ].copy()
    n_valid_events = len(valid_events)

    normal_scores = scores[y == 0]
    threshold_specs = [
        ("q90", float(np.percentile(normal_scores, 90))),
        ("q95", float(np.percentile(normal_scores, 95))),
        ("q98", float(np.percentile(normal_scores, 98))),
        ("q99", float(np.percentile(normal_scores, 99))),
    ]

    results: Dict[str, Any] = {}

    print("\n" + "=" * 92)
    print(f"{'Threshold':<16} | {'Cutoff':<8} | {'Precision':<9} | {'Recall':<8} | {'F1':<6} | {'Ev Recall':<10} | {'Mean LT':<8} | {'FP Ep/dev/wk':<12}")
    print("-" * 92)

    for tag, cutoff in threshold_specs:
        pred = scores >= cutoff

        tp = int(np.sum(pred & (y == 1)))
        fp = int(np.sum(pred & (y == 0)))
        fn = int(np.sum((~pred) & (y == 1)))
        tn = int(np.sum((~pred) & (y == 0)))

        prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = float(2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

        # Operational FP episodes
        is_fp_series = pd.Series((pred & (y == 0)).astype(int), index=df.index)
        dev_series = df["device_id"]
        fp_starts = (is_fp_series == 1) & (
            (is_fp_series.shift(1, fill_value=0) == 0) | (dev_series != dev_series.shift(1))
        )
        total_fp_episodes = int(fp_starts.sum())
        fp_episodes_per_dev_week = float(total_fp_episodes / (num_devices * duration_weeks))
        fp_steps_per_dev_week = float(fp / (num_devices * duration_weeks))

        # Incident recall & lead time
        detected_events = 0
        lead_times_hours: List[float] = []

        for _, ev in valid_events.iterrows():
            did = str(ev["device_id"])
            ev_start = ev["start_dt"]
            if did in dev_timelines:
                dev_tl = dev_timelines[did]
                win_start = ev_start - pd.Timedelta(hours=24)
                sub_win = dev_tl.loc[win_start:ev_start]
                fires = sub_win[sub_win["anomaly_score"] >= cutoff]
                if not fires.empty:
                    detected_events += 1
                    lead_h = float((ev_start - fires.index[0]).total_seconds() / 3600.0)
                    lead_times_hours.append(lead_h)

        event_recall = float(detected_events / n_valid_events) if n_valid_events > 0 else 0.0
        mean_lead_time_h = float(np.mean(lead_times_hours)) if lead_times_hours else 0.0
        median_lead_time_h = float(np.median(lead_times_hours)) if lead_times_hours else 0.0

        results[tag] = {
            "threshold_cutoff": round(cutoff, 4),
            "sample_metrics": {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "fire_rate": round(float(np.mean(pred)), 4),
            },
            "operational_metrics": {
                "fp_steps_per_dev_week": round(fp_steps_per_dev_week, 2),
                "total_fp_episodes": total_fp_episodes,
                "fp_episodes_per_dev_week": round(fp_episodes_per_dev_week, 2),
            },
            "incident_metrics": {
                "total_events_in_range": n_valid_events,
                "detected_events": detected_events,
                "event_recall": round(event_recall, 4),
                "mean_lead_time_hours": round(mean_lead_time_h, 2),
                "median_lead_time_hours": round(median_lead_time_h, 2),
            },
        }

        print(
            f"{tag:<16} | "
            f"{cutoff:8.4f} | "
            f"{prec*100:8.2f}% | "
            f"{rec*100:7.2f}% | "
            f"{f1:6.4f} | "
            f"{event_recall*100:9.1f}% | "
            f"{mean_lead_time_h:6.1f}h | "
            f"{fp_episodes_per_dev_week:12.2f}"
        )

    print("=" * 92 + "\n")
    return results


if __name__ == "__main__":
    train_and_score_isolation_forest()
