"""tests/test_daily_health_report.py

Tests for daily health report generation, operational metrics,
and the strict absence of forbidden outcome metrics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.live.daily_health_report import (
    FORBIDDEN_OUTCOME_KEYS,
    compute_daily_health,
    format_daily_health_markdown,
    generate_daily_health_report,
    load_cycles,
    load_predictions,
)


def test_load_and_compute_daily_health(tmp_path: Path):
    cycles_file = tmp_path / "cycles.jsonl"
    pred_file = tmp_path / "shadow_predictions.jsonl"

    cycles = [
        {
            "cycle_id": "2026-10-15T10:00:00+00:00",
            "started": "2026-10-15T10:00:00+00:00",
            "finished": "2026-10-15T10:00:15+00:00",
            "status": "OK",
            "devices_attempted": 158,
            "devices_scored": 158,
            "fetch_failures": 0,
            "alarm_fetch_failures": 0,
            "scoring_errors": 0,
            "llm_calls": 0,
        },
        {
            "cycle_id": "2026-10-15T10:15:00+00:00",
            "started": "2026-10-15T10:15:00+00:00",
            "finished": "2026-10-15T10:15:15+00:00",
            "status": "OK",
            "devices_attempted": 158,
            "devices_scored": 158,
            "fetch_failures": 0,
            "alarm_fetch_failures": 0,
            "scoring_errors": 0,
            "llm_calls": 0,
        },
    ]
    with open(cycles_file, "w", encoding="utf-8") as f:
        for c in cycles:
            f.write(json.dumps(c) + "\n")

    predictions = [
        {
            "cycle_id": "2026-10-15T10:00:00+00:00",
            "prediction_time": "2026-10-15T10:00:05+00:00",
            "device_id": f"dev_{i}",
            "device_name": f"Device_{i}",
            "bank": "BANK_A",
            "risk_tier": "CONNECTIVITY_ISSUE" if i < 10 else "LOW",
            "device_state": "STALE_DATA" if i < 10 else "ONLINE_OK",
            "connectivity_alert": 1 if i < 10 else 0,
            "calibrated_prob": 0.001,
            "telemetry_age_seconds": 3600 if i < 10 else 120,
            "llm_used": False,
            "validator_valid": True,
            "git_commit": "b502cfc",
            "scoring_error": None,
        }
        for i in range(20)
    ]
    # Add one HIGH power risk prediction
    predictions.append({
        "cycle_id": "2026-10-15T10:00:00+00:00",
        "prediction_time": "2026-10-15T10:00:05+00:00",
        "device_id": "dev_power",
        "device_name": "Device_Power",
        "bank": "BANK_B",
        "risk_tier": "HIGH",
        "device_state": "ONLINE_OK",
        "connectivity_alert": 0,
        "calibrated_prob": 0.15,
        "telemetry_age_seconds": 60,
        "llm_used": False,
        "validator_valid": True,
        "git_commit": "b502cfc",
        "scoring_error": None,
    })

    with open(pred_file, "w", encoding="utf-8") as f:
        for p in predictions:
            f.write(json.dumps(p) + "\n")

    loaded_cycles = load_cycles(cycles_file)
    loaded_preds = load_predictions(pred_file)

    assert len(loaded_cycles) == 2
    assert len(loaded_preds) == 21

    health = compute_daily_health("2026-10-15", loaded_cycles, loaded_preds)

    assert health["report_date"] == "2026-10-15"
    assert health["cycles_attempted"] == 2
    assert health["cycles_succeeded"] == 2
    assert health["devices_scored"] == 21
    assert health["llm_used"] == 0
    assert health["connectivity_alert_counts"] == 10
    assert health["power_alert_counts"]["HIGH"] == 1
    assert health["power_alert_counts"]["CRITICAL"] == 0


def test_forbidden_outcome_keys_enforcement():
    """Verify that compute_daily_health strictly contains no outcome-based metrics."""
    health = compute_daily_health("2026-10-15", [], [])
    flattened_keys = set(health.keys())
    for v in health.values():
        if isinstance(v, dict):
            flattened_keys.update(v.keys())

    for forbidden in FORBIDDEN_OUTCOME_KEYS:
        assert forbidden not in flattened_keys, f"Forbidden key '{forbidden}' found in daily health summary!"


def test_generate_daily_health_report_output(tmp_path: Path):
    cycles_file = tmp_path / "cycles.jsonl"
    pred_file = tmp_path / "shadow_predictions.jsonl"
    reports_out = tmp_path / "reports"

    with open(cycles_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "cycle_id": "2026-10-15T11:00:00+00:00",
            "status": "OK",
            "devices_attempted": 158,
            "devices_scored": 158,
            "llm_calls": 0,
        }) + "\n")

    with open(pred_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "cycle_id": "2026-10-15T11:00:00+00:00",
            "prediction_time": "2026-10-15T11:00:05+00:00",
            "device_id": "dev_1",
            "risk_tier": "LOW",
            "device_state": "ONLINE_OK",
            "connectivity_alert": 0,
            "calibrated_prob": 0.001,
            "telemetry_age_seconds": 60,
            "llm_used": False,
            "validator_valid": True,
            "git_commit": "b502cfc",
        }) + "\n")

    health = generate_daily_health_report(
        date_str="2026-10-15",
        output_dir=reports_out,
        cycles_file=cycles_file,
        predictions_file=pred_file,
    )

    out_md = reports_out / "daily_health_2026-10-15.md"
    assert out_md.is_file()
    content = out_md.read_text(encoding="utf-8")

    assert "Live Shadow Daily Health Report — 2026-10-15" in content
    assert "LLM disabled (`llm_used = 0`)" in content

    # Invariant: Output markdown must never mention outcome performance metrics
    lower_content = content.lower()
    assert "precision:" not in lower_content
    assert "recall:" not in lower_content
    assert "f1:" not in lower_content
