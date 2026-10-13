"""tests/test_phase_r5_claims.py
Unit tests for Phase R5: Claim-Level Faithfulness Validator & State-Based Routing.
"""

import pytest
from src.explain.validator import ComprehensiveFaithfulnessValidator, generate_deterministic_template


@pytest.fixture
def validator():
    return ComprehensiveFaithfulnessValidator(tolerance=0.05)


@pytest.fixture
def sample_online_payload():
    return {
        "device_id": "boi-001",
        "device_name": "BOI-KALYAN",
        "bank": "BOI",
        "device_state": "ONLINE_OK",
        "is_battery_sensor_present": True,
        "calibrated_prob": 0.045,
        "risk_tier": "LOW",
        "rules_triggered": [],
        "top_contributing_features": "normal baseline",
        "telemetry_snapshot": {
            "ac_voltage": 230.5,
            "battery_voltage": 12.6,
            "time_since_mains_loss_hours": 0.0,
        },
    }


@pytest.fixture
def sample_stale_payload():
    return {
        "device_id": "boi-002",
        "device_name": "BOI-VASHI",
        "bank": "BOI",
        "device_state": "STALE_DATA",
        "is_battery_sensor_present": True,
        "calibrated_prob": 0.005,
        "risk_tier": "CONNECTIVITY_ISSUE",
        "rules_triggered": ["RULE_COMMUNICATION_HEARTBEAT_LOSS"],
        "top_contributing_features": "heartbeat drop",
        "telemetry_snapshot": {
            "ac_voltage": "N/A",
            "battery_voltage": "N/A",
            "time_since_mains_loss_hours": 0.0,
        },
    }


def test_valid_online_template_passes(validator, sample_online_payload):
    ticket = generate_deterministic_template(sample_online_payload)
    is_valid, violations = validator.validate(ticket, sample_online_payload)
    assert is_valid is True, f"Valid template failed validation: {violations}"
    assert len(violations) == 0


def test_stale_data_routes_to_connectivity_ticket(validator, sample_stale_payload):
    ticket = generate_deterministic_template(sample_stale_payload)
    assert "[SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]" in ticket
    assert "Investigate gateway, SIM/network, power at the site" in ticket
    assert "24-Hour Outage Probability" not in ticket

    is_valid, violations = validator.validate(ticket, sample_stale_payload)
    assert is_valid is True, f"Connectivity ticket failed validation: {violations}"


def test_stale_data_rejects_urgent_power_outage_claim(validator, sample_stale_payload):
    bad_ticket = (
        "[SYSTEM DIAGNOSTIC TICKET - CRITICAL RISK]\n"
        "Branch Site: BOI-VASHI (BOI)\n"
        "24-Hour Outage Probability: 85.0%\n"
        "Urgent power outage in progress! Immediate electrical technician dispatch required."
    )
    is_valid, violations = validator.validate(bad_ticket, sample_stale_payload)
    assert is_valid is False
    assert any("State routing violation" in v for v in violations)


def test_rejects_mains_loss_claim_when_mains_normal(validator, sample_online_payload):
    bad_text = (
        "[SYSTEM DIAGNOSTIC TICKET - LOW RISK]\n"
        "Branch Site: BOI-KALYAN (BOI)\n"
        "24-Hour Outage Probability: 4.5%\n"
        "Critical mains failure and grid power loss detected across site utility distribution board."
    )
    is_valid, violations = validator.validate(bad_text, sample_online_payload)
    assert is_valid is False
    assert any("Mains claim violation" in v for v in violations)


def test_rejects_battery_claim_on_site_without_battery_sensor(validator, sample_online_payload):
    payload = dict(sample_online_payload)
    payload["is_battery_sensor_present"] = False
    payload["bank"] = "SBI"

    bad_text = (
        "[SYSTEM DIAGNOSTIC TICKET - LOW RISK]\n"
        "Branch Site: BOI-KALYAN (SBI)\n"
        "24-Hour Outage Probability: 4.5%\n"
        "Observed battery voltage: 12.6 V with battery degraded state."
    )
    is_valid, violations = validator.validate(bad_text, payload)
    assert is_valid is False
    assert any("Battery sensor violation" in v for v in violations)


def test_rejects_hallucinated_rule(validator, sample_online_payload):
    bad_text = (
        "[SYSTEM DIAGNOSTIC TICKET - LOW RISK]\n"
        "Branch Site: BOI-KALYAN (BOI)\n"
        "24-Hour Outage Probability: 4.5%\n"
        "Triggered violations: RULE_CRITICAL_BATTERY_LOW and RULE_RAPID_BATTERY_DISCHARGE."
    )
    is_valid, violations = validator.validate(bad_text, sample_online_payload)
    assert is_valid is False
    assert any("Rule hallucination" in v for v in violations)


def test_rejects_risk_tier_header_mismatch(validator, sample_online_payload):
    bad_text = (
        "[SYSTEM DIAGNOSTIC TICKET - CRITICAL RISK]\n"
        "Branch Site: BOI-KALYAN (BOI)\n"
        "24-Hour Outage Probability: 4.5%\n"
        "Normal operations."
    )
    is_valid, violations = validator.validate(bad_text, sample_online_payload)
    assert is_valid is False
    assert any("Risk tier mismatch" in v for v in violations)


def test_rejects_numerical_hallucination(validator, sample_online_payload):
    bad_text = (
        "[SYSTEM DIAGNOSTIC TICKET - LOW RISK]\n"
        "Branch Site: BOI-KALYAN (BOI)\n"
        "24-Hour Outage Probability: 4.5%\n"
        "AC Line Voltage: 999.85 V."
    )
    is_valid, violations = validator.validate(bad_text, sample_online_payload)
    assert is_valid is False
    assert any("Ungrounded numerical figures" in v for v in violations)
