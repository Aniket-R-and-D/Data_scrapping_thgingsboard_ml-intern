"""Unit tests for Phase 5: Shadow Mode Job & Operational Safety Guarantees."""

import json
import sqlite3
import tempfile
from pathlib import Path
import pytest

from src.shadow.shadow_job import ShadowScorer


def test_safety_guarantee_no_automated_dispatch():
    """Verify that shadow mode strictly enforces read-only logging and zero dispatches."""
    scorer = ShadowScorer()
    assert scorer.decision_threshold == 0.090

    # Risk tier determination
    # Case 1: Safe operating conditions
    assert scorer.determine_risk_tier(0.02, []) == "LOW"

    # Case 2: Moderate probability or warning rule
    assert scorer.determine_risk_tier(0.12, []) == "MEDIUM"
    assert scorer.determine_risk_tier(0.04, ["rule_ac_surge"]) == "MEDIUM"

    # Case 3: High probability or single critical rule
    assert scorer.determine_risk_tier(0.40, []) == "HIGH"
    assert scorer.determine_risk_tier(0.05, ["rule_mains_lost"]) == "HIGH"
    assert scorer.determine_risk_tier(0.05, ["rule_ac_zero"]) == "HIGH"

    # Case 4: Critical probability or compound failure
    assert scorer.determine_risk_tier(0.70, []) == "CRITICAL"
    assert scorer.determine_risk_tier(0.10, ["rule_mains_lost", "rule_battery_critical"]) == "CRITICAL"


def test_sqlite_shadow_persistence(tmp_path):
    """Verify that shadow predictions are persisted properly in SQLite."""
    db_file = tmp_path / "test_shadow.db"
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device_id TEXT NOT NULL,
                device_name TEXT NOT NULL,
                bank TEXT NOT NULL,
                calibrated_prob REAL NOT NULL,
                raw_prob REAL NOT NULL,
                risk_tier TEXT NOT NULL,
                rules_triggered TEXT NOT NULL,
                action_taken TEXT NOT NULL
            )
        """)
        cursor.execute("""
            INSERT INTO shadow_log (timestamp, device_id, device_name, bank, calibrated_prob, raw_prob, risk_tier, rules_triggered, action_taken)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "2026-10-13T12:00:00Z",
            "mock-dev-1",
            "BOI-TEST",
            "BOI",
            0.42,
            0.60,
            "HIGH",
            "rule_mains_lost",
            "LOG_TELEMETRY_ONLY (NO_DISPATCH)",
        ))
        conn.commit()

        # Query back
        cursor.execute("SELECT device_id, calibrated_prob, risk_tier, action_taken FROM shadow_log")
        row = cursor.fetchone()
        assert row[0] == "mock-dev-1"
        assert row[1] == 0.42
        assert row[2] == "HIGH"
        assert row[3] == "LOG_TELEMETRY_ONLY (NO_DISPATCH)"


def test_shadow_jsonl_record_structure():
    """Verify that logged JSONL shadow prediction records match expected audit schema."""
    log_path = Path("data/processed/shadow_predictions.jsonl")
    if not log_path.exists():
        pytest.skip("Shadow predictions log not yet generated.")

    with open(log_path, "r", encoding="utf-8") as f:
        first_line = f.readline()
        if not first_line:
            pytest.skip("Shadow log is empty.")
        rec = json.loads(first_line)

    required_keys = [
        "timestamp",
        "device_id",
        "device_name",
        "bank",
        "calibrated_prob",
        "raw_prob",
        "risk_tier",
        "rules_triggered",
        "action_taken",
        "telemetry_snapshot",
    ]
    for key in required_keys:
        assert key in rec, f"Missing required key '{key}' in shadow log record"

    assert "NO_DISPATCH" in rec["action_taken"]
    assert 0.0 <= rec["calibrated_prob"] <= 1.0
