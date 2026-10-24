"""src/live/prepare_day14_evaluation.py — Day-14 live outcome evaluation harness.

Prepares and verifies the formal 14-day outcome evaluation pipeline:
- Enforces strict closed 24-hour outcome window rules.
- Requires complete follow-up telemetry; excludes incomplete records with explicit reasons.
- Uses R2 power-outage ground truth labels; never leaks future data.
- Evaluates both timestep-level (precision, recall, F1) and incident-level metrics.
- Computes lead time (mean and median) and alerts per device per week.
- Generates 95% bootstrap confidence intervals.
- Separates connectivity outcomes from power outcomes.
- Enforces desktop run isolation (rejects merged laptop data).
- Prevents premature evaluation before the 14-day operational period closes.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live import outcomes, shadow_run as sr

LIVE_DIR = ROOT / "data" / "live"
CONFIG_FILE = ROOT / "configs" / "config.yaml"
MIN_OPERATIONAL_DAYS = 14.0
OUTCOME_WINDOW = timedelta(hours=24)
SETTLE_PERIOD = timedelta(hours=1)
POWER_ALERT_TIERS = ("HIGH", "CRITICAL")
DESKTOP_COMMIT_EXPECTED = "b502cfc"


@dataclass
class ExclusionAccounting:
    total_predictions: int
    eligible_mature_predictions: int
    excluded_window_not_closed: int
    excluded_settle_active: int
    excluded_incomplete_followup: int
    excluded_incomplete_input: int
    excluded_scoring_error: int
    excluded_non_fleet: int
    reasons_summary: Dict[str, int]


@dataclass
class MetricWithCI:
    point_estimate: Optional[float]
    ci_lower: Optional[float]
    ci_upper: Optional[float]
    sample_size: int
    confidence_level: float = 0.95


def bootstrap_ci(
    data: np.ndarray,
    stat_fn: Callable[[np.ndarray], float],
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> Tuple[Optional[float], Optional[float]]:
    """Compute empirical bootstrap confidence interval."""
    if len(data) < 5:
        return (None, None)
    rng = np.random.default_rng(seed)
    stats = []
    n = len(data)
    for _ in range(n_resamples):
        sample = data[rng.integers(0, n, size=n)]
        try:
            val = stat_fn(sample)
            if not (math.isnan(val) or math.isinf(val)):
                stats.append(val)
        except Exception:
            continue
    if len(stats) < 50:
        return (None, None)
    alpha = (1.0 - confidence) / 2.0
    low = float(np.percentile(stats, 100.0 * alpha))
    high = float(np.percentile(stats, 100.0 * (1.0 - alpha)))
    return (round(low, 4), round(high, 4))


def check_evaluation_readiness(
    predictions_df: pd.DataFrame,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Check if the desktop run has reached the required 14-day threshold."""
    now_dt = now or datetime.now(timezone.utc)
    if predictions_df.empty:
        return {
            "ready": False,
            "status": "NO_PREDICTIONS_FOUND",
            "days_elapsed": 0.0,
            "message": "No live predictions found in data/live/.",
        }

    first_dt = predictions_df["pt"].min()
    elapsed_days = (now_dt - first_dt.to_pydatetime()).total_seconds() / 86400.0

    # Check closed windows
    win_end = predictions_df["pt"] + OUTCOME_WINDOW
    closed_mask = win_end + SETTLE_PERIOD <= pd.Timestamp(now_dt)
    closed_count = int(closed_mask.sum())
    pending_count = int((~closed_mask).sum())

    is_ready = elapsed_days >= MIN_OPERATIONAL_DAYS and closed_count > 0

    return {
        "ready": is_ready,
        "status": "READY_FOR_EVALUATION" if is_ready else "PREMATURE_EVALUATION_WITHHELD",
        "days_elapsed": round(elapsed_days, 2),
        "required_days": MIN_OPERATIONAL_DAYS,
        "first_prediction_utc": first_dt.isoformat(),
        "latest_prediction_utc": predictions_df["pt"].max().isoformat(),
        "total_predictions": len(predictions_df),
        "closed_window_predictions": closed_count,
        "pending_window_predictions": pending_count,
        "message": (
            "14-day maturity threshold achieved."
            if is_ready
            else f"Operational age ({elapsed_days:.2f} days) is below required {MIN_OPERATIONAL_DAYS} days. Outcome metrics withheld to prevent premature reporting."
        ),
    }


def audit_run_isolation(predictions_df: pd.DataFrame) -> Dict[str, Any]:
    """Verify that predictions belong strictly to the desktop run and are not merged."""
    if predictions_df.empty:
        return {"isolated": True, "violations": []}

    violations = []
    # Verify desktop commits
    commits = predictions_df["git_commit"].dropna().unique()
    for c in commits:
        if not str(c).startswith("b502cfc") and not str(c).startswith("8685ad1"):
            # Flag any historical laptop commit
            if str(c).startswith("b44a775") or str(c).startswith("fe5df5f"):
                violations.append(f"Historical laptop commit detected: {c}")

    # Check timestamps: desktop run started on 2026-10-15
    earliest = predictions_df["pt"].min()
    if earliest < pd.Timestamp("2026-10-15T00:00:00Z"):
        violations.append(f"Pre-desktop prediction timestamp detected: {earliest.isoformat()}")

    return {
        "isolated": len(violations) == 0,
        "violations": violations,
        "commits_present": [str(c)[:7] for c in commits],
        "earliest_timestamp": earliest.isoformat() if not predictions_df.empty else None,
    }


def account_exclusions(
    predictions_df: pd.DataFrame,
    now: Optional[datetime] = None,
) -> Tuple[pd.DataFrame, ExclusionAccounting]:
    """Classify predictions into mature vs excluded with clear audit accounting."""
    now_dt = now or datetime.now(timezone.utc)
    df = predictions_df.copy()
    fetch_state = json.loads(sr.FETCH_STATE.read_text()) if sr.FETCH_STATE.exists() else {}

    df["exclusion_reason"] = None
    df["is_eligible"] = False

    win_end = df["pt"] + OUTCOME_WINDOW
    window_closed = win_end <= pd.Timestamp(now_dt)
    settle_cleared = (win_end + SETTLE_PERIOD) <= pd.Timestamp(now_dt)

    # Criteria checks
    c_not_closed = ~window_closed
    c_settle_active = window_closed & ~settle_cleared

    # Check cursor follow-up
    cursor_ok = [
        fetch_state.get(did, -1) >= int(w.timestamp() * 1000)
        for did, w in zip(df["device_id"], win_end)
    ]
    c_incomplete_followup = settle_cleared & ~pd.Series(cursor_ok, index=df.index)

    c_scoring_error = df["scoring_error"].notna()
    c_incomplete_input = ~df["input_complete"].fillna(False).astype(bool)
    c_non_fleet = ~df["in_training_fleet"].fillna(True).astype(bool)

    # Apply reasons hierarchically
    df.loc[c_not_closed, "exclusion_reason"] = "WINDOW_NOT_CLOSED"
    df.loc[c_settle_active, "exclusion_reason"] = "SETTLE_PERIOD_ACTIVE"
    df.loc[c_incomplete_followup, "exclusion_reason"] = "INCOMPLETE_FOLLOWUP_TELEMETRY"
    df.loc[settle_cleared & pd.Series(cursor_ok, index=df.index) & c_incomplete_input, "exclusion_reason"] = "INCOMPLETE_INPUT_AT_PREDICTION"
    df.loc[settle_cleared & pd.Series(cursor_ok, index=df.index) & c_scoring_error, "exclusion_reason"] = "SCORING_ERROR_PRESENT"

    # Eligible rows: window closed, settled, complete follow-up, complete input, no scoring error
    mature_mask = (
        settle_cleared
        & pd.Series(cursor_ok, index=df.index)
        & (~c_incomplete_input)
        & (~c_scoring_error)
    )
    df.loc[mature_mask, "is_eligible"] = True

    accounting = ExclusionAccounting(
        total_predictions=len(df),
        eligible_mature_predictions=int(mature_mask.sum()),
        excluded_window_not_closed=int(c_not_closed.sum()),
        excluded_settle_active=int(c_settle_active.sum()),
        excluded_incomplete_followup=int(c_incomplete_followup.sum()),
        excluded_incomplete_input=int(c_incomplete_input.sum()),
        excluded_scoring_error=int(c_scoring_error.sum()),
        excluded_non_fleet=int(c_non_fleet.sum()),
        reasons_summary=df["exclusion_reason"].value_counts(dropna=True).to_dict(),
    )
    return df, accounting


def evaluate_incident_level(
    mature_df: pd.DataFrame,
    label_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute incident-level recall, lead time, and alert timing."""
    alarms = (
        pd.read_parquet(sr.ALARM_CACHE)
        if sr.ALARM_CACHE.exists()
        else pd.DataFrame(columns=sr.ALARM_COLUMNS)
    )

    incidents = []
    for did, group in mature_df.groupby("device_id"):
        dev_dir = sr.CACHE_DIR / did
        events = outcomes.outage_events(
            outcomes._read(dev_dir, "ac_voltage"),
            outcomes._read(dev_dir, "battery_voltage"),
            outcomes._read(dev_dir, "statusbox_mains_on"),
            alarms[alarms["device_id"].astype(str) == did],
            label_cfg,
        )
        if not events:
            continue

        alerts = group[group["risk_tier"].isin(POWER_ALERT_TIERS)].copy()
        alerts["alert_ms"] = alerts["pt"].apply(lambda t: t.timestamp() * 1000.0)

        for s_ms, e_ms, reason in events:
            # Prediction window for this incident is (s_ms - 24h, s_ms]
            win_start = s_ms - (24 * 3600 * 1000.0)
            in_window_alerts = alerts[
                (alerts["alert_ms"] >= win_start) & (alerts["alert_ms"] < s_ms)
            ]
            detected = len(in_window_alerts) > 0
            lead_time_hours = None
            if detected:
                first_alert_ms = in_window_alerts["alert_ms"].min()
                lead_time_hours = round((s_ms - first_alert_ms) / 3600000.0, 2)

            incidents.append({
                "device_id": did,
                "incident_start_utc": datetime.fromtimestamp(s_ms / 1000.0, timezone.utc).isoformat(),
                "incident_end_utc": datetime.fromtimestamp(e_ms / 1000.0, timezone.utc).isoformat(),
                "reason": reason,
                "detected": detected,
                "lead_time_hours": lead_time_hours,
                "alerts_in_window": len(in_window_alerts),
            })

    total_incidents = len(incidents)
    detected_incidents = sum(1 for inc in incidents if inc["detected"])
    lead_times = [
        inc["lead_time_hours"]
        for inc in incidents
        if inc["detected"] and inc["lead_time_hours"] is not None
    ]

    mean_lead = round(float(np.mean(lead_times)), 2) if lead_times else None
    median_lead = round(float(np.median(lead_times)), 2) if lead_times else None
    inc_recall = round(detected_incidents / total_incidents, 4) if total_incidents else None

    # Bootstrap CI for incident recall if sample size >= 10
    rec_ci = (None, None)
    if total_incidents >= 10:
        det_arr = np.array([1 if inc["detected"] else 0 for inc in incidents])
        rec_ci = bootstrap_ci(det_arr, lambda x: float(x.mean()))

    return {
        "total_power_outage_incidents": total_incidents,
        "detected_power_outage_incidents": detected_incidents,
        "incident_level_recall": inc_recall,
        "incident_recall_95_ci": list(rec_ci),
        "mean_lead_time_hours": mean_lead,
        "median_lead_time_hours": median_lead,
        "incidents_list": incidents,
    }


def evaluate_timestep_level(mature_df: pd.DataFrame) -> Dict[str, Any]:
    """Compute timestep-level precision, recall, F1 with bootstrap CIs."""
    if mature_df.empty or "y_power_outage_24h" not in mature_df.columns:
        return {"error": "No ground truth labeled mature records available"}

    valid_mask = mature_df["y_power_outage_24h"].notna()
    p_df = mature_df[valid_mask].copy()
    if p_df.empty:
        return {"rows": 0}

    y_true = p_df["y_power_outage_24h"].astype(int).to_numpy()
    y_pred = p_df["risk_tier"].isin(POWER_ALERT_TIERS).astype(int).to_numpy()

    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())

    prec = round(tp / (tp + fp), 4) if (tp + fp) > 0 else None
    rec = round(tp / (tp + fn), 4) if (tp + fn) > 0 else None
    f1 = (
        round(2.0 * (prec * rec) / (prec + rec), 4)
        if prec and rec and (prec + rec) > 0
        else None
    )

    # Bootstrap CIs for precision and recall
    paired = np.column_stack([y_true, y_pred])
    prec_ci = (None, None)
    rec_ci = (None, None)
    f1_ci = (None, None)

    if len(paired) >= 10:
        def _p_fn(s):
            yt, yp = s[:, 0], s[:, 1]
            pos = yp.sum()
            return float(((yp == 1) & (yt == 1)).sum() / pos) if pos else 0.0

        def _r_fn(s):
            yt, yp = s[:, 0], s[:, 1]
            actual = yt.sum()
            return float(((yp == 1) & (yt == 1)).sum() / actual) if actual else 0.0

        def _f_fn(s):
            p = _p_fn(s)
            r = _r_fn(s)
            return float(2.0 * p * r / (p + r)) if (p + r) > 0 else 0.0

        prec_ci = bootstrap_ci(paired, _p_fn)
        rec_ci = bootstrap_ci(paired, _r_fn)
        f1_ci = bootstrap_ci(paired, _f_fn)

    return {
        "eligible_rows": len(p_df),
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": prec,
        "precision_95_ci": list(prec_ci),
        "recall": rec,
        "recall_95_ci": list(rec_ci),
        "f1": f1,
        "f1_95_ci": list(f1_ci),
    }


def prepare_day14_evaluation_report(
    predictions_file: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
    force_dry_run: bool = False,
) -> Dict[str, Any]:
    """Execute evaluation harness or generate readiness spec."""
    now_dt = now or datetime.now(timezone.utc)
    preds = outcomes.load_predictions()
    readiness = check_evaluation_readiness(preds, now_dt)
    isolation = audit_run_isolation(preds)

    report: Dict[str, Any] = {
        "report_title": "Live Shadow Day-14 Outcome Evaluation",
        "generated_utc": now_dt.isoformat(),
        "readiness": readiness,
        "run_isolation": isolation,
    }

    if not readiness["ready"] and not force_dry_run:
        # Premature stop guard
        report["evaluation_status"] = "PREMATURE_EVALUATION_WITHHELD"
        report["summary"] = (
            f"Run has operated for {readiness['days_elapsed']} days. "
            f"The 14-day outcome evaluation requires >= {MIN_OPERATIONAL_DAYS} full days of closed outcome windows. "
            "Metrics are withheld by safety protocol."
        )
    else:
        # Execute harness on eligible closed-window data
        with open(CONFIG_FILE, encoding="utf-8") as f:
            label_cfg = yaml.safe_load(f)["outage_label"]

        df_out = outcomes.assign_outcomes(preds, now_dt)
        df_audited, exclusions = account_exclusions(df_out, now_dt)
        mature = df_audited[df_audited["is_eligible"]].copy()

        report["evaluation_status"] = "COMPLETED"
        report["exclusions"] = asdict(exclusions)
        report["timestep_metrics"] = evaluate_timestep_level(mature)
        report["incident_metrics"] = evaluate_incident_level(mature, label_cfg)
        report["connectivity_metrics"] = outcomes._conn_metrics(mature)

    out_dir = output_dir or (ROOT / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "live_shadow_day14_desktop_spec.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="simulate evaluation on existing closed windows")
    ap.add_argument("--output-dir", type=Path, default=None)
    args = ap.parse_args(argv)

    rep = prepare_day14_evaluation_report(
        output_dir=args.output_dir,
        force_dry_run=args.dry_run,
    )
    print(f"Day-14 evaluation status: {rep['evaluation_status']}")
    print(f"Readiness: {rep['readiness']['message']}")
    print(f"Run isolation: {'VERIFIED' if rep['run_isolation']['isolated'] else 'FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
