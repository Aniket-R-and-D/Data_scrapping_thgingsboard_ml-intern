"""tests/test_phase_r4_consistency.py
Unit tests for Phase R4 Mathematical Consistency & Shadow Safeguards.
"""

import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
REPORT_FILE = ROOT / "reports/phase_r4_shadow_remediation.json"
LIVE_LOG_JSONL = ROOT / "data/processed/live_shadow_predictions.jsonl"


def test_shadow_mathematical_consistency():
    assert REPORT_FILE.exists(), "phase_r4_shadow_remediation.json must exist"
    with open(REPORT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    exec_audit = data["execution_audit"]
    total_cycles = exec_audit["total_cycles_evaluated"]

    cm = data["confusion_matrix_summary"]
    cm_sum = cm["TN"] + cm["FP"] + cm["FN"] + cm["TP"]
    assert cm_sum == total_cycles, f"CM sum ({cm_sum}) != Total cycles ({total_cycles})"

    tier_sum = sum(v["cycle_count"] for v in data["risk_tier_distribution"]["tier_summary"].values())
    assert tier_sum == total_cycles, f"Tier sum ({tier_sum}) != Total cycles ({total_cycles})"

    actual_outages_cm = cm["TP"] + cm["FN"]
    actual_outages_tier = sum(v["realized_outages"] for v in data["risk_tier_distribution"]["tier_summary"].values())
    assert actual_outages_cm == actual_outages_tier, "Total outages must match across confusion matrix and tier table"


def test_high_plus_critical_volume_under_10_percent():
    with open(REPORT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    tier_dist = data["risk_tier_distribution"]
    high_crit_pct = tier_dist["high_plus_critical_volume_pct"]
    assert high_crit_pct < 10.0, f"Combined HIGH+CRITICAL volume ({high_crit_pct}%) must be < 10%"
    assert tier_dist["volume_constraint_under_10_pct_passed"] is True


def test_live_shadow_daemon_safety():
    assert LIVE_LOG_JSONL.exists(), "Live shadow log must exist"
    with open(LIVE_LOG_JSONL, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) > 0

    # Verify run_type and read-only schema
    sample = json.loads(lines[0])
    assert sample["run_type"] == "LIVE_SHADOW"
    assert sample["model_version"] == "v2.0-remediated-lightgbm-clean"
    assert "calibrated_prob" in sample
    assert "risk_tier" in sample
