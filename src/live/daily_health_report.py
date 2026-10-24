"""src/live/daily_health_report.py — Operational daily health reporting.

Generates daily operational health reports for the desktop live shadow run.
STRICT SAFETY RULE: Operational only. MUST NOT include precision, recall,
F1, lead time, or any outcome-based model performance metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIVE_DIR = ROOT / "data" / "live"
DEFAULT_CYCLES_FILE = LIVE_DIR / "cycles.jsonl"
DEFAULT_PREDICTIONS_FILE = LIVE_DIR / "shadow_predictions.jsonl"
REPORTS_DIR = ROOT / "reports"

FORBIDDEN_OUTCOME_KEYS = {
    "precision",
    "recall",
    "f1",
    "f1_score",
    "lead_time",
    "true_positives",
    "false_positives",
    "false_negatives",
    "accuracy",
    "roc_auc",
    "pr_auc",
}


def load_cycles(cycles_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = cycles_file or DEFAULT_CYCLES_FILE
    if not p.exists():
        return []
    cycles = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    cycles.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return cycles


def load_predictions(predictions_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = predictions_file or DEFAULT_PREDICTIONS_FILE
    if not p.exists():
        return []
    records = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _get_cycle_date(cycle: Dict[str, Any]) -> str:
    ts = cycle.get("cycle_id") or cycle.get("started") or cycle.get("finished") or ""
    return ts[:10] if len(ts) >= 10 else "UNKNOWN"


def _get_prediction_date(pred: Dict[str, Any]) -> str:
    ts = pred.get("prediction_time") or pred.get("cycle_id") or ""
    return ts[:10] if len(ts) >= 10 else "UNKNOWN"


def compute_daily_health(
    date_str: str,
    cycles: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute operational daily health for a given date (YYYY-MM-DD)."""
    day_cycles = [c for c in cycles if _get_cycle_date(c) == date_str]
    day_preds = [p for p in predictions if _get_prediction_date(p) == date_str]

    # Cycle statistics
    cycles_attempted = len(day_cycles)
    status_counts: Dict[str, int] = {}
    fetch_failures = 0
    alarm_fetch_failures = 0
    scoring_errors = 0

    for c in day_cycles:
        st = c.get("status", "UNKNOWN")
        status_counts[st] = status_counts.get(st, 0) + 1
        fetch_failures += int(c.get("fetch_failures", 0))
        alarm_fetch_failures += int(c.get("alarm_fetch_failures", 0))
        scoring_errors += int(c.get("scoring_errors", 0))

    cycles_succeeded = status_counts.get("OK", 0)
    cycles_degraded = status_counts.get("DEGRADED", 0)

    # Missed slots calculation
    scored_ids = sorted({
        c["cycle_id"] for c in day_cycles
        if c.get("status") in ("OK", "DEGRADED") and "cycle_id" in c
    })
    missed_slots: List[str] = []
    restarts_or_gaps: List[str] = []

    if scored_ids:
        first_dt = pd.to_datetime(scored_ids[0], utc=True)
        last_dt = pd.to_datetime(scored_ids[-1], utc=True)
        # Expected slots between first and last scored cycle of the day
        expected_slots = pd.date_range(first_dt, last_dt, freq="15min")
        scored_set = {pd.to_datetime(s, utc=True).isoformat() for s in scored_ids}
        for slot in expected_slots:
            slot_iso = slot.isoformat()
            if slot_iso not in scored_set:
                missed_slots.append(slot_iso)
                restarts_or_gaps.append(f"Missing 15-min scheduled slot at {slot_iso}")

    # Check non-OK statuses as gaps/restarts
    for c in day_cycles:
        st = c.get("status", "UNKNOWN")
        if st not in ("OK", "DEGRADED"):
            restarts_or_gaps.append(f"Cycle {c.get('cycle_id', 'unknown')} recorded status {st}")

    # Prediction level metrics
    devices_scored: Set[str] = set()
    llm_used_count = 0
    validator_failures_by_commit: Dict[str, int] = {}
    validator_failures_total = 0
    connectivity_alert_counts = 0
    power_alert_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    freshness_bins = {
        "<=1h": 0,
        "1-6h": 0,
        "6-24h": 0,
        "1-7d": 0,
        "7-30d": 0,
        ">30d": 0,
        "never_seen": 0,
    }

    for p in day_preds:
        did = p.get("device_id")
        if did:
            devices_scored.add(did)

        if p.get("llm_used", False):
            llm_used_count += 1

        if p.get("validator_valid") is False:
            validator_failures_total += 1
            commit = str(p.get("git_commit", "unknown"))[:7]
            validator_failures_by_commit[commit] = (
                validator_failures_by_commit.get(commit, 0) + 1
            )

        tier = p.get("risk_tier")
        if tier == "CONNECTIVITY_ISSUE":
            connectivity_alert_counts += 1
        elif tier in power_alert_counts:
            power_alert_counts[tier] += 1

        # Freshness
        last_ts = p.get("last_telemetry_ts")
        age_sec = p.get("telemetry_age_seconds")
        if last_ts is None or age_sec is None:
            freshness_bins["never_seen"] += 1
        else:
            try:
                age_h = float(age_sec) / 3600.0
                if age_h <= 1.0:
                    freshness_bins["<=1h"] += 1
                elif age_h <= 6.0:
                    freshness_bins["1-6h"] += 1
                elif age_h <= 24.0:
                    freshness_bins["6-24h"] += 1
                elif age_h <= 168.0:
                    freshness_bins["1-7d"] += 1
                elif age_h <= 720.0:
                    freshness_bins["7-30d"] += 1
                else:
                    freshness_bins[">30d"] += 1
            except (ValueError, TypeError):
                freshness_bins["never_seen"] += 1

    report: Dict[str, Any] = {
        "report_date": date_str,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "cycles_attempted": cycles_attempted,
        "cycles_succeeded": cycles_succeeded,
        "cycles_degraded": cycles_degraded,
        "cycle_status_counts": status_counts,
        "missed_slots_count": len(missed_slots),
        "missed_slots": missed_slots,
        "fetch_failures": fetch_failures,
        "alarm_fetch_failures": alarm_fetch_failures,
        "scoring_errors": scoring_errors,
        "validator_failures_total": validator_failures_total,
        "validator_failures_by_commit": validator_failures_by_commit,
        "devices_scored": len(devices_scored),
        "llm_used": llm_used_count,
        "data_freshness_distribution": freshness_bins,
        "connectivity_alert_counts": connectivity_alert_counts,
        "power_alert_counts": power_alert_counts,
        "restarts_or_gaps": restarts_or_gaps,
        "action_taken": "LOG_ONLY_NO_DISPATCH",
    }

    # Strict safety invariant check
    for key in FORBIDDEN_OUTCOME_KEYS:
        if key in report:
            raise ValueError(f"Forbidden outcome metric {key!r} in operational daily health report")

    return report


def format_daily_health_markdown(report: Dict[str, Any]) -> str:
    """Format the daily health report as markdown."""
    lines = [
        f"# Live Shadow Daily Health Report — {report['report_date']}",
        "",
        f"**Generated (UTC):** {report['generated_utc']}  ",
        "**Scope:** Operational monitoring only (strictly no outcome or validation metrics)  ",
        "**Safety Enforcement:** `LOG_ONLY_NO_DISPATCH`, LLM disabled (`llm_used = 0`)  ",
        "",
        "## 1. Cycle & Scheduler Integrity",
        "",
        f"- **Cycles Attempted:** {report['cycles_attempted']}",
        f"- **Cycles Succeeded (OK):** {report['cycles_succeeded']}",
        f"- **Cycles Degraded:** {report['cycles_degraded']}",
        f"- **Cycle Status Breakdown:** `{report['cycle_status_counts']}`",
        f"- **Missed Slot Count:** {report['missed_slots_count']}",
    ]

    if report["missed_slots"]:
        lines.append("- **Missed Slots:**")
        for s in report["missed_slots"]:
            lines.append(f"  - `{s}`")

    if report["restarts_or_gaps"]:
        lines.append("- **Restarts / Gaps Recorded:**")
        for g in report["restarts_or_gaps"]:
            lines.append(f"  - {g}")
    else:
        lines.append("- **Restarts / Gaps Recorded:** None (continuous scheduled execution)")

    lines.extend([
        "",
        "## 2. Ingestion & Scoring Health",
        "",
        f"- **Fetch Failures:** {report['fetch_failures']}",
        f"- **Alarm-Fetch Failures:** {report['alarm_fetch_failures']}",
        f"- **Scoring Errors:** {report['scoring_errors']}",
        f"- **Unique Devices Scored:** {report['devices_scored']}",
        f"- **Validator Failures Total:** {report['validator_failures_total']}",
        f"- **Validator Failures by Git Commit:** `{report['validator_failures_by_commit']}`",
        f"- **LLM Usage Count:** {report['llm_used']} (Expected: 0)",
        "",
        "## 3. Data Freshness Distribution",
        "",
        "| Age Category | Row Count |",
        "| :--- | :--- |",
    ])
    for k, v in report["data_freshness_distribution"].items():
        lines.append(f"| `{k}` | {v} |")

    lines.extend([
        "",
        "## 4. Alert Volume Profile (Raw 15-Minute Level)",
        "",
        f"- **Connectivity Alert Rows:** {report['connectivity_alert_counts']}",
        f"- **Power Alert Rows by Tier:** `{report['power_alert_counts']}`",
        "",
        "> [!NOTE]",
        "> Outcome metrics (precision, recall, F1, lead time) are withheld by design. "
        "Validation metrics are strictly reserved for the 14-day outcome evaluation.",
    ])

    return "\n".join(lines) + "\n"


def generate_daily_health_report(
    date_str: Optional[str] = None,
    cycles_file: Optional[Path] = None,
    predictions_file: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate daily health report dictionary and write markdown + json to reports."""
    cycles = load_cycles(cycles_file)
    preds = load_predictions(predictions_file)

    if not date_str:
        if cycles:
            date_str = _get_cycle_date(cycles[-1])
        elif preds:
            date_str = _get_prediction_date(preds[-1])
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    report = compute_daily_health(date_str, cycles, preds)

    out_dir = output_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"daily_health_{date_str}.json"
    md_path = out_dir / f"daily_health_{date_str}.md"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(format_daily_health_markdown(report), encoding="utf-8")

    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="date to report (YYYY-MM-DD), default latest in logs")
    ap.add_argument("--output-dir", type=Path, default=None, help="directory for reports")
    ap.add_argument("--json", action="store_true", help="output JSON to stdout")
    args = ap.parse_args(argv)

    rep = generate_daily_health_report(
        date_str=args.date,
        output_dir=args.output_dir,
    )
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"Daily health report generated for {rep['report_date']}:")
        print(f"  Cycles attempted: {rep['cycles_attempted']}, Succeeded: {rep['cycles_succeeded']}")
        print(f"  Devices scored: {rep['devices_scored']}, LLM used: {rep['llm_used']}")
        print(f"  Reports written to reports/daily_health_{rep['report_date']}.{{md,json}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
