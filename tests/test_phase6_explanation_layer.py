"""tests/test_phase6_explanation_layer.py
Unit tests for Phase 6 Explanation Layer:
- Numerical faithfulness validation
- Hallucination detection and rejection
- Deterministic template fallback generation
- Explanation dataset formatting
"""

import json
from pathlib import Path
import pytest

from src.explain.validator import (
    NumericalFaithfulnessValidator,
    generate_deterministic_template,
)


def test_validator_extract_numbers():
    text = "At site SBI_01, AC voltage dropped to 184.5 V, battery voltage is 11.2 V, and outage risk is 78.4%."
    nums = NumericalFaithfulnessValidator.extract_numbers_from_text(text)
    assert 184.5 in nums
    assert 11.2 in nums
    assert 78.4 in nums


def test_validator_allowed_numbers_collection():
    payload = {
        "device_name": "BOI_BRANCH_10",
        "calibrated_prob": 0.852,
        "telemetry_snapshot": {
            "ac_voltage": 195.4,
            "battery_voltage": 10.9,
            "heartbeat_gap_seconds": 180,
        },
        "rules_triggered": ["AC_UNDERVOLTAGE"],
    }
    allowed = NumericalFaithfulnessValidator.collect_allowed_numbers(payload)
    assert 195.4 in allowed
    assert 10.9 in allowed
    assert 85.2 in allowed  # percentage equivalent
    assert 180.0 in allowed


def test_validator_detects_hallucinated_numbers():
    val = NumericalFaithfulnessValidator(tolerance=0.05)
    payload = {
        "device_name": "BOI_BRANCH_10",
        "calibrated_prob": 0.35,
        "telemetry_snapshot": {
            "ac_voltage": 220.0,
            "battery_voltage": 12.6,
        },
    }
    # Hallucinated text with fake battery 9.1 V and fake prob 95%
    hallucinated_text = "The battery has collapsed to 9.1 V and probability is 95%."
    is_valid, violations = val.validate(hallucinated_text, payload)
    assert not is_valid
    assert any(abs(v - 9.1) < 0.1 for v in violations)


def test_validator_passes_faithful_text():
    val = NumericalFaithfulnessValidator(tolerance=0.1)
    payload = {
        "device_name": "BOI_BRANCH_10",
        "calibrated_prob": 0.75,
        "telemetry_snapshot": {
            "ac_voltage": 210.0,
            "battery_voltage": 11.5,
        },
    }
    # Faithful text using only numbers in payload or whitelist (24h)
    faithful_text = "Risk is 75.0% over 24 hours. AC voltage is 210.0 V with battery voltage at 11.5 V."
    is_valid, violations = val.validate(faithful_text, payload)
    assert is_valid
    assert len(violations) == 0


def test_deterministic_fallback_template():
    payload = {
        "device_name": "SBI_REGIONAL_04",
        "bank": "SBI",
        "calibrated_prob": 0.88,
        "risk_tier": "CRITICAL",
        "rules_triggered": ["AC_FAIL_ZERO_VOLTS", "BATTERY_CRITICAL_LOW"],
        "top_contributing_features": "ac_voltage_mean_1h: 0.0, battery_voltage_min_1h: 10.5",
        "telemetry_snapshot": {
            "ac_voltage": 0.0,
            "battery_voltage": 10.5,
            "heartbeat_gap_seconds": 300,
            "time_since_mains_loss_hours": 2.5,
        },
    }
    ticket = generate_deterministic_template(payload)
    assert "[SYSTEM DIAGNOSTIC TICKET - CRITICAL RISK]" in ticket
    assert "SBI_REGIONAL_04 (SBI)" in ticket
    assert "88.0%" in ticket
    assert "AC_FAIL_ZERO_VOLTS" in ticket
    assert "AC Line Voltage: 0.0 V" in ticket
    assert "DISPATCH RECOMMENDATION: Urgent electrical maintenance required." in ticket

    # Validate that fallback ticket itself passes numerical faithfulness!
    val = NumericalFaithfulnessValidator(tolerance=0.1)
    is_valid, violations = val.validate(ticket, payload)
    assert is_valid, f"Fallback ticket failed validation with violations: {violations}"


def test_real_model_explanation_dataset():
    dataset_path = Path("training_data/real_model_explanation_dataset.jsonl")
    assert dataset_path.exists(), "Explanation dataset file training_data/real_model_explanation_dataset.jsonl must exist."
    with open(dataset_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) >= 100, f"Expected at least 100 training examples, got {len(lines)}"

    sample = json.loads(lines[0])
    assert "messages" in sample
    assert len(sample["messages"]) == 3
    user_content = sample["messages"][1]["content"]
    assert "calibrated_prob" in user_content
    assert "risk_tier" in user_content
    assistant_content = sample["messages"][2]["content"]
    assert len(assistant_content) > 50
