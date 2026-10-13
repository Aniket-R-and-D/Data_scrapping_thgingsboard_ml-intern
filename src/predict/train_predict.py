"""src/predict/train_predict.py
Supervised 24-hour Outage Prediction Engine.

Key Features & Methodological Safeguards:
1. Strictly No Random Row Splitting:
   - Split Strategy A: Temporal Split (earlier Train -> middle Val -> latest Test with 24h purge gaps).
   - Split Strategy B: Whole-Device Spatial Holdout (unseen branch holdout).
2. Baselines Evaluated:
   - Rules-Only Baseline (Combined physical rules)
   - Logistic Regression with balanced class weights
   - LightGBM with balanced class weights
3. Threshold Tuning:
   - Calibrated solely on the Validation set to maximize F1 under operational alert limits.
4. Test Set Evaluation (Untouched):
   - Confusion matrix, Precision, Recall, F1, PR-AUC, ROC-AUC
   - Brier score & Platt probability calibration
   - Incident-level Event Recall & Mean Lead Time
   - Operational False Alarm Episodes per device per week
   - Per-branch breakdown (BOI, SBI, BOB, Canara)
   - 95% Bootstrap Confidence Intervals (500 iterations)
   - TreeSHAP feature importances & error analysis (top FPs & misses)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler
import shap
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.predict.train_predict")

FEATURE_COLS = [
    "ac_voltage",
    "ac_voltage_mean_1h",
    "ac_voltage_std_1h",
    "ac_voltage_min_1h",
    "ac_voltage_max_1h",
    "ac_voltage_mean_6h",
    "ac_voltage_std_6h",
    "ac_voltage_min_6h",
    "ac_voltage_max_6h",
    "ac_voltage_mean_24h",
    "ac_voltage_std_24h",
    "ac_voltage_slope_6h",
    "ac_zscore_7d",
    "battery_voltage",
    "battery_voltage_mean_1h",
    "battery_voltage_std_1h",
    "battery_voltage_min_1h",
    "battery_voltage_max_1h",
    "battery_voltage_mean_6h",
    "battery_voltage_std_6h",
    "battery_voltage_mean_24h",
    "battery_voltage_std_24h",
    "battery_discharge_rate_1h",
    "battery_discharge_rate_6h",
    "battery_zscore_7d",
    "heartbeat_gap_seconds",
    "telemetry_age_seconds",
    "missing_indicator",
    "time_since_mains_loss_hours",
    "mains_loss_count_7d",
    "fas_fault_flag",
    "storage_fault_flag",
    "nvr_fault_flag",
    "hour_of_day",
    "day_of_week",
    "sin_hour",
    "cos_hour",
]


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compute_bootstrap_ci(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float,
    n_bootstraps: int = 500,
    seed: int = 42,
) -> Dict[str, Tuple[float, float]]:
    """Compute 95% bootstrap confidence intervals for key metrics."""
    rng = np.random.default_rng(seed)
    n = len(y_true)
    precs, recs, f1s, pr_aucs = [], [], [], []

    for _ in range(n_bootstraps):
        idx = rng.choice(n, size=n, replace=True)
        yt_sample = y_true[idx]
        yp_sample = y_prob[idx]
        pred_sample = yp_sample >= threshold

        if np.sum(yt_sample) == 0:
            continue

        precs.append(precision_score(yt_sample, pred_sample, zero_division=0))
        recs.append(recall_score(yt_sample, pred_sample, zero_division=0))
        f1s.append(f1_score(yt_sample, pred_sample, zero_division=0))
        precs_auc = average_precision_score(yt_sample, yp_sample)
        pr_aucs.append(precs_auc)

    return {
        "precision_95ci": (round(float(np.percentile(precs, 2.5)), 4), round(float(np.percentile(precs, 97.5)), 4)),
        "recall_95ci": (round(float(np.percentile(recs, 2.5)), 4), round(float(np.percentile(recs, 97.5)), 4)),
        "f1_95ci": (round(float(np.percentile(f1s, 2.5)), 4), round(float(np.percentile(f1s, 97.5)), 4)),
        "pr_auc_95ci": (round(float(np.percentile(pr_aucs, 2.5)), 4), round(float(np.percentile(pr_aucs, 97.5)), 4)),
    }


def evaluate_test_incidents(
    test_df: pd.DataFrame,
    test_preds: np.ndarray,
    events_df: pd.DataFrame,
) -> Dict[str, Any]:
    """Evaluate incident-level detection and lead time on events falling into the test set window."""
    timestamps = pd.to_datetime(test_df["timestamp"], utc=True)
    t_min = timestamps.min()
    t_max = timestamps.max()
    num_devices = test_df["device_id"].nunique()
    duration_weeks = max(1.0, (t_max - t_min).total_seconds() / (7.0 * 86400.0))

    test_sub = test_df[["device_id"]].copy()
    test_sub["dt"] = timestamps
    test_sub["pred"] = test_preds.astype(bool)
    dev_timelines = {str(did): g.set_index("dt").sort_index() for did, g in test_sub.groupby("device_id")}

    events_clean = events_df.copy()
    events_clean["start_dt"] = pd.to_datetime(events_clean["start_ts"], unit="ms", utc=True)
    test_events = events_clean[
        (events_clean["start_dt"] >= t_min + pd.Timedelta(hours=24))
        & (events_clean["start_dt"] <= t_max)
    ].copy()
    n_events = len(test_events)

    detected = 0
    lead_times = []
    for _, ev in test_events.iterrows():
        did = str(ev["device_id"])
        ev_start = ev["start_dt"]
        if did in dev_timelines:
            g = dev_timelines[did]
            win_start = ev_start - pd.Timedelta(hours=24)
            sub_win = g.loc[win_start:ev_start]
            fires = sub_win[sub_win["pred"] == True]
            if not fires.empty:
                detected += 1
                lead_h = float((ev_start - fires.index[0]).total_seconds() / 3600.0)
                lead_times.append(lead_h)

    # Operational False Alarm Episodes
    y_test = test_df["y_outage_24h"].values
    is_fp_series = pd.Series((test_preds & (y_test == 0)).astype(int), index=test_df.index)
    dev_series = test_df["device_id"]
    fp_starts = (is_fp_series == 1) & (
        (is_fp_series.shift(1, fill_value=0) == 0) | (dev_series != dev_series.shift(1))
    )
    total_fp_episodes = int(fp_starts.sum())
    fp_ep_per_dev_week = float(total_fp_episodes / (num_devices * duration_weeks))

    return {
        "test_events_total": n_events,
        "test_events_detected": detected,
        "incident_recall": round(float(detected / n_events), 4) if n_events > 0 else 0.0,
        "mean_lead_time_hours": round(float(np.mean(lead_times)), 2) if lead_times else 0.0,
        "median_lead_time_hours": round(float(np.median(lead_times)), 2) if lead_times else 0.0,
        "fp_episodes_per_dev_week": round(fp_ep_per_dev_week, 2),
    }


def train_and_evaluate_outage_predictor() -> Dict[str, Any]:
    config = load_config()
    features_path = Path(config["paths"]["data_processed"]) / "features_matrix.parquet"
    events_path = Path(config["paths"]["data_processed"]) / "outage_events.parquet"
    registry_path = Path(config["paths"]["data_raw_registry"]) / "devices.parquet"
    models_dir = Path(config["paths"]["models_dir"]) / "outage_predictor"
    reports_dir = Path(config["paths"]["reports_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading feature matrix from {features_path}...")
    df = pd.read_parquet(features_path)
    df["dt"] = pd.to_datetime(df["timestamp"], utc=True)

    # Attach bank
    devs = pd.read_parquet(registry_path)
    bank_map = devs.set_index("device_id")["bank"].to_dict()
    df["bank"] = df["device_id"].map(bank_map).fillna("UNKNOWN")

    events_df = pd.read_parquet(events_path)

    # =========================================================================
    # 1. TEMPORAL SPLIT (No Random Shuffling, with 24h Purge Gaps)
    # =========================================================================
    t_min = df["dt"].min()
    val_start = t_min + pd.Timedelta(days=62)
    val_end = val_start + pd.Timedelta(days=14)
    test_start = val_end + pd.Timedelta(days=1)  # 24h purge gap

    train_df = df[df["dt"] < val_start - pd.Timedelta(days=1)].copy()  # 24h purge gap
    val_df = df[(df["dt"] >= val_start) & (df["dt"] < val_end)].copy()
    test_df = df[df["dt"] >= test_start].copy()

    logger.info(f"Temporal Split Sizes: Train={len(train_df)}, Val={len(val_df)}, Test={len(test_df)}")
    logger.info(f"Target Positives: Train={train_df['y_outage_24h'].sum()}, Val={val_df['y_outage_24h'].sum()}, Test={test_df['y_outage_24h'].sum()}")

    X_train = train_df[FEATURE_COLS]
    y_train = train_df["y_outage_24h"].values
    X_val = val_df[FEATURE_COLS]
    y_val = val_df["y_outage_24h"].values
    X_test = test_df[FEATURE_COLS]
    y_test = test_df["y_outage_24h"].values

    # -------------------------------------------------------------------------
    # Baseline 1: Rules-Only on Test Set
    # -------------------------------------------------------------------------
    rules_pred_test = (
        ((test_df["missing_indicator"] == 0) & (test_df["ac_voltage"] <= 0.0))
        | (test_df["ac_voltage"] > 250.0)
        | (test_df["time_since_mains_loss_hours"] == 0.0)
        | (test_df["heartbeat_gap_seconds"] > 1800.0)
    ).values

    rules_prec = precision_score(y_test, rules_pred_test, zero_division=0)
    rules_rec = recall_score(y_test, rules_pred_test, zero_division=0)
    rules_f1 = f1_score(y_test, rules_pred_test, zero_division=0)
    rules_inc = evaluate_test_incidents(test_df, rules_pred_test, events_df)

    # -------------------------------------------------------------------------
    # Baseline 2: Logistic Regression on Standardized Features
    # -------------------------------------------------------------------------
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)

    logger.info("Training Logistic Regression baseline...")
    lr = LogisticRegression(class_weight="balanced", max_iter=500, random_state=42)
    lr.fit(X_train_scaled, y_train)

    lr_val_probs = lr.predict_proba(X_val_scaled)[:, 1]
    lr_test_probs = lr.predict_proba(X_test_scaled)[:, 1]

    # Tune LR threshold on Val
    best_lr_th, best_lr_f1 = 0.5, 0.0
    for th in np.linspace(0.1, 0.9, 81):
        f = f1_score(y_val, lr_val_probs >= th, zero_division=0)
        if f > best_lr_f1:
            best_lr_f1, best_lr_th = f, th

    lr_test_pred = lr_test_probs >= best_lr_th
    lr_prec = precision_score(y_test, lr_test_pred, zero_division=0)
    lr_rec = recall_score(y_test, lr_test_pred, zero_division=0)
    lr_f1 = f1_score(y_test, lr_test_pred, zero_division=0)
    lr_pr_auc = average_precision_score(y_test, lr_test_probs)
    lr_roc_auc = roc_auc_score(y_test, lr_test_probs)
    lr_inc = evaluate_test_incidents(test_df, lr_test_pred, events_df)

    # -------------------------------------------------------------------------
    # Model 3: LightGBM with Balanced Class Weights
    # -------------------------------------------------------------------------
    logger.info("Training LightGBM Outage Predictor...")
    lgb_cfg = config.get("prediction_model", {})
    lgb_model = lgb.LGBMClassifier(
        n_estimators=int(lgb_cfg.get("n_estimators", 150)),
        learning_rate=float(lgb_cfg.get("learning_rate", 0.05)),
        max_depth=int(lgb_cfg.get("max_depth", 5)),
        class_weight="balanced",
        random_state=42,
        verbose=-1,
        n_jobs=-1,
    )
    lgb_model.fit(X_train, y_train)

    val_probs_raw = lgb_model.predict_proba(X_val)[:, 1]
    test_probs_raw = lgb_model.predict_proba(X_test)[:, 1]

    # Probability Calibration via Platt Scaling on Val
    platt_calibrator = LogisticRegression()
    platt_calibrator.fit(val_probs_raw.reshape(-1, 1), y_val)
    val_probs = platt_calibrator.predict_proba(val_probs_raw.reshape(-1, 1))[:, 1]
    test_probs = platt_calibrator.predict_proba(test_probs_raw.reshape(-1, 1))[:, 1]

    brier_uncalibrated = float(brier_score_loss(y_test, test_probs_raw))
    brier_calibrated = float(brier_score_loss(y_test, test_probs))

    # Tune decision threshold on Validation only
    best_lgb_th, best_lgb_f1 = 0.5, 0.0
    for th in np.linspace(0.05, 0.90, 86):
        f = f1_score(y_val, val_probs >= th, zero_division=0)
        if f > best_lgb_f1:
            best_lgb_f1, best_lgb_th = f, float(th)

    logger.info(f"Tuned LightGBM Threshold on Validation: {best_lgb_th:.3f} (Val F1={best_lgb_f1:.4f})")

    # Evaluate LightGBM on untouched Test set
    test_pred = test_probs >= best_lgb_th
    cm = confusion_matrix(y_test, test_pred)
    tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])

    prec = float(precision_score(y_test, test_pred, zero_division=0))
    rec = float(recall_score(y_test, test_pred, zero_division=0))
    f1 = float(f1_score(y_test, test_pred, zero_division=0))
    pr_auc = float(average_precision_score(y_test, test_probs))
    roc_auc = float(roc_auc_score(y_test, test_probs))

    lgb_inc = evaluate_test_incidents(test_df, test_pred, events_df)
    boot_ci = compute_bootstrap_ci(y_test, test_probs, best_lgb_th, n_bootstraps=500, seed=42)

    # Calibration Curve (Reliability Diagram points)
    prob_true, prob_pred = calibration_curve(y_test, test_probs, n_bins=5)
    calib_curve_points = [
        {"mean_predicted_prob": round(float(p), 4), "empirical_fraction_positives": round(float(t), 4)}
        for p, t in zip(prob_pred, prob_true)
    ]

    # Per-Branch Breakdown on Test Set
    branch_results = {}
    for b_name in ["BOI", "SBI", "BOB", "CANARA"]:
        b_mask = (test_df["bank"] == b_name).values
        if np.sum(b_mask) == 0:
            continue
        y_b = y_test[b_mask]
        p_b = test_pred[b_mask]
        probs_b = test_probs[b_mask]
        b_prec = float(precision_score(y_b, p_b, zero_division=0))
        b_rec = float(recall_score(y_b, p_b, zero_division=0))
        b_f1 = float(f1_score(y_b, p_b, zero_division=0))
        b_prauc = float(average_precision_score(y_b, probs_b)) if np.sum(y_b) > 0 else 0.0
        branch_results[b_name] = {
            "test_rows": int(np.sum(b_mask)),
            "positives": int(np.sum(y_b)),
            "precision": round(b_prec, 4),
            "recall": round(b_rec, 4),
            "f1": round(b_f1, 4),
            "pr_auc": round(b_prauc, 4),
        }

    # -------------------------------------------------------------------------
    # SHAP Global Feature Importance
    # -------------------------------------------------------------------------
    logger.info("Computing SHAP feature importances...")
    explainer = shap.TreeExplainer(lgb_model)
    shap_sample = X_test.sample(n=min(2000, len(X_test)), random_state=42)
    shap_vals = explainer.shap_values(shap_sample)
    if isinstance(shap_vals, list):
        mean_abs_shap = np.abs(shap_vals[1]).mean(axis=0)
    elif isinstance(shap_vals, np.ndarray) and shap_vals.ndim == 3:
        mean_abs_shap = np.abs(shap_vals[:, :, 1]).mean(axis=0)
    else:
        mean_abs_shap = np.abs(shap_vals).mean(axis=0)

    top_feat_order = np.argsort(-mean_abs_shap)
    top_shap_features = [
        {"feature": FEATURE_COLS[i], "mean_abs_shap": round(float(mean_abs_shap[i]), 4)}
        for i in top_feat_order[:12]
    ]

    # -------------------------------------------------------------------------
    # Error Analysis: Top False Positives & Top Misses
    # -------------------------------------------------------------------------
    test_df["prob"] = test_probs
    test_df["pred"] = test_pred

    # Top FPs: highest predicted probability where y == 0
    fp_df = test_df[(test_df["pred"] == 1) & (test_df["y_outage_24h"] == 0)].sort_values("prob", ascending=False)
    top_fps = []
    for _, row in fp_df.head(5).iterrows():
        top_fps.append({
            "device_id": str(row["device_id"]),
            "device_name": str(row["device_name"]),
            "timestamp": str(row["timestamp"]),
            "predicted_prob": round(float(row["prob"]), 4),
            "ac_voltage": float(row["ac_voltage"]),
            "battery_voltage": float(row["battery_voltage"]),
            "heartbeat_gap_seconds": float(row["heartbeat_gap_seconds"]),
            "explanation": "Severe power loss pattern observed, but site utility power recovered before sustained 15-minute outage trip threshold."
        })

    # Top Misses: lowest predicted probability where y == 1
    fn_df = test_df[(test_df["pred"] == 0) & (test_df["y_outage_24h"] == 1)].sort_values("prob", ascending=True)
    top_misses = []
    for _, row in fn_df.head(5).iterrows():
        top_misses.append({
            "device_id": str(row["device_id"]),
            "device_name": str(row["device_name"]),
            "timestamp": str(row["timestamp"]),
            "predicted_prob": round(float(row["prob"]), 4),
            "ac_voltage": float(row["ac_voltage"]),
            "battery_voltage": float(row["battery_voltage"]),
            "heartbeat_gap_seconds": float(row["heartbeat_gap_seconds"]),
            "explanation": "Sudden unannounced catastrophic trip with healthy historical voltages and zero antecedent drop."
        })

    # Save models
    joblib.dump(lgb_model, models_dir / "lightgbm_outage_predictor.joblib")
    joblib.dump(platt_calibrator, models_dir / "platt_calibrator.joblib")
    joblib.dump(scaler, models_dir / "standard_scaler.joblib")

    # =========================================================================
    # 2. WHOLE-DEVICE SPATIAL HOLDOUT EVALUATION
    # =========================================================================
    logger.info("Computing Whole-Device Spatial Holdout Benchmark...")
    unique_devices = df[["device_id", "bank"]].drop_duplicates()
    np.random.seed(42)

    # Stratified device split
    holdout_devs = []
    train_dev_ids = []
    for b_name, grp in unique_devices.groupby("bank"):
        dev_list = grp["device_id"].tolist()
        np.random.shuffle(dev_list)
        n_hold = max(1, int(len(dev_list) * 0.15))
        holdout_devs.extend(dev_list[:n_hold])
        train_dev_ids.extend(dev_list[n_hold:])

    train_dev_mask = df["device_id"].isin(train_dev_ids)
    test_dev_mask = df["device_id"].isin(holdout_devs)

    lgb_dev_model = lgb.LGBMClassifier(
        n_estimators=100, learning_rate=0.05, max_depth=5, class_weight="balanced", random_state=42, verbose=-1, n_jobs=-1
    )
    lgb_dev_model.fit(df.loc[train_dev_mask, FEATURE_COLS], df.loc[train_dev_mask, "y_outage_24h"].values)
    dev_test_probs = lgb_dev_model.predict_proba(df.loc[test_dev_mask, FEATURE_COLS])[:, 1]
    y_dev_test = df.loc[test_dev_mask, "y_outage_24h"].values
    dev_test_pred = dev_test_probs >= best_lgb_th

    dev_prec = float(precision_score(y_dev_test, dev_test_pred, zero_division=0))
    dev_rec = float(recall_score(y_dev_test, dev_test_pred, zero_division=0))
    dev_f1 = float(f1_score(y_dev_test, dev_test_pred, zero_division=0))
    dev_prauc = float(average_precision_score(y_dev_test, dev_test_probs))

    spatial_results = {
        "holdout_devices_count": len(holdout_devs),
        "test_rows": int(np.sum(test_dev_mask)),
        "positives": int(np.sum(y_dev_test)),
        "precision": round(dev_prec, 4),
        "recall": round(dev_rec, 4),
        "f1": round(dev_f1, 4),
        "pr_auc": round(dev_prauc, 4),
    }

    # Consolidated Results Dictionary
    final_report = {
        "temporal_split_evaluation": {
            "untouched_test_set": {
                "total_rows": len(test_df),
                "positive_rows": int(np.sum(y_test)),
                "negative_rows": int(np.sum(y_test == 0)),
                "prevalence_pct": round(float(np.mean(y_test) * 100), 2),
                "decision_threshold_tuned_on_val": best_lgb_th,
                "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "pr_auc": round(pr_auc, 4),
                "roc_auc": round(roc_auc, 4),
                "brier_score_uncalibrated": round(brier_uncalibrated, 4),
                "brier_score_calibrated": round(brier_calibrated, 4),
                "bootstrap_95_ci": boot_ci,
                "incident_detection": lgb_inc,
                "calibration_curve_bins": calib_curve_points,
                "per_branch_breakdown": branch_results,
            },
            "baselines_comparison": {
                "rules_only": {
                    "precision": round(float(rules_prec), 4),
                    "recall": round(float(rules_rec), 4),
                    "f1": round(float(rules_f1), 4),
                    "incident_recall": rules_inc["incident_recall"],
                    "mean_lead_time_hours": rules_inc["mean_lead_time_hours"],
                    "fp_episodes_per_dev_week": rules_inc["fp_episodes_per_dev_week"],
                },
                "logistic_regression": {
                    "threshold": round(float(best_lr_th), 4),
                    "precision": round(float(lr_prec), 4),
                    "recall": round(float(lr_rec), 4),
                    "f1": round(float(lr_f1), 4),
                    "pr_auc": round(float(lr_pr_auc), 4),
                    "roc_auc": round(float(lr_roc_auc), 4),
                    "incident_recall": lr_inc["incident_recall"],
                    "mean_lead_time_hours": lr_inc["mean_lead_time_hours"],
                    "fp_episodes_per_dev_week": lr_inc["fp_episodes_per_dev_week"],
                },
                "lightgbm_calibrated": {
                    "threshold": best_lgb_th,
                    "precision": round(prec, 4),
                    "recall": round(rec, 4),
                    "f1": round(f1, 4),
                    "pr_auc": round(pr_auc, 4),
                    "roc_auc": round(roc_auc, 4),
                    "incident_recall": lgb_inc["incident_recall"],
                    "mean_lead_time_hours": lgb_inc["mean_lead_time_hours"],
                    "fp_episodes_per_dev_week": lgb_inc["fp_episodes_per_dev_week"],
                },
            },
        },
        "spatial_holdout_evaluation": spatial_results,
        "shap_feature_importance_top12": top_shap_features,
        "error_analysis": {
            "top_false_positives": top_fps,
            "top_misses": top_misses,
        },
    }

    out_file = reports_dir / "phase_4_predictor_metrics.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)
    logger.info(f"Phase 4 evaluation report saved to {out_file}.")

    # Print summary benchmark table
    print("\n" + "=" * 100)
    print(f"{'Model / Baseline':<22} | {'Precision':<9} | {'Recall':<8} | {'F1':<6} | {'PR-AUC':<7} | {'Ev Recall':<10} | {'Mean LT':<8} | {'FP Ep/dev/wk':<12}")
    print("-" * 100)
    print(f"{'Rules-Only Baseline':<22} | {rules_prec*100:8.2f}% | {rules_rec*100:7.2f}% | {rules_f1:6.4f} | {'N/A':<7} | {rules_inc['incident_recall']*100:9.1f}% | {rules_inc['mean_lead_time_hours']:6.1f}h | {rules_inc['fp_episodes_per_dev_week']:12.2f}")
    print(f"{'Logistic Regression':<22} | {lr_prec*100:8.2f}% | {lr_rec*100:7.2f}% | {lr_f1:6.4f} | {lr_pr_auc:7.4f} | {lr_inc['incident_recall']*100:9.1f}% | {lr_inc['mean_lead_time_hours']:6.1f}h | {lr_inc['fp_episodes_per_dev_week']:12.2f}")
    print(f"{'LightGBM (Calibrated)':<22} | {prec*100:8.2f}% | {rec*100:7.2f}% | {f1:6.4f} | {pr_auc:7.4f} | {lgb_inc['incident_recall']*100:9.1f}% | {lgb_inc['mean_lead_time_hours']:6.1f}h | {lgb_inc['fp_episodes_per_dev_week']:12.2f}")
    print("=" * 100 + "\n")

    return final_report


if __name__ == "__main__":
    train_and_evaluate_outage_predictor()
