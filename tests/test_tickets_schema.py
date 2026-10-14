"""tests/test_tickets_schema.py — Stage B input-contract validation tests.

No model inference. No fabricated human labels. All fixtures are realistic
values drawn from the existing telemetry/shadow schema
(device/state/tier/rule vocabularies as produced by run_shadow_remediation.py).
"""

from datetime import datetime, timezone
import pytest

from src.tickets.schema import (
    ALLOWED_INVESTIGATION_ACTIONS,
    KNOWN_RULE_IDS,
    POLICY_VERSION,
    Measurement,
    TicketInput,
    TicketValidationError,
)

TS = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
TS_AC = datetime(2026, 10, 13, 11, 55, 0, tzinfo=timezone.utc)


def meas(field="ac_voltage", value=228.5, unit="V", observed_at=TS_AC, freshness="CURRENT"):
    return Measurement(field=field, value=value, unit=unit,
                       observed_at=observed_at, freshness=freshness)


def base_measurements():
    return [
        meas("ac_voltage", 228.5, "V", TS_AC, "CURRENT"),
        meas("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
        meas("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
    ]


def base_kwargs(**overrides):
    kw = dict(
        device_id="388f1380-31ad-11f1-9c6f-b71bb2771567",
        device_name="BOI-BALLYBAZAR",
        bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION,
        device_state="ONLINE_OK",
        measurements=base_measurements(),
        calibrated_probability=0.0452,
        risk_tier="MEDIUM",
        triggered_rules=["RULE_MAINS_LOSS_RECENT"],
        observed_facts=["AC line voltage within nominal band at last fresh observation."],
        possible_causes=["Early mains instability consistent with recent rule trigger."],
        approved_investigation_actions=["monitor-ac-voltage-6h"],
    )
    kw.update(overrides)
    return kw


def test_valid_online_input_builds():
    ticket = TicketInput.build(**base_kwargs())
    assert ticket.device_name == "BOI-BALLYBAZAR"
    assert ticket.policy_version == POLICY_VERSION
    assert ticket.calibrated_probability == pytest.approx(0.0452)


def test_invalid_probability_above_one_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(calibrated_probability=1.5))


def test_invalid_probability_negative_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(calibrated_probability=-0.01))


def test_invalid_probability_nan_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(calibrated_probability=float("nan")))


def test_invalid_probability_string_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(calibrated_probability="4.5%"))


def test_probability_boundary_values_accepted():
    TicketInput.build(**base_kwargs(calibrated_probability=0.0, risk_tier="LOW"))
    TicketInput.build(**base_kwargs(calibrated_probability=1.0, risk_tier="CRITICAL"))


def test_tier_band_mismatch_rejected():
    # 0.0452 sits in the MEDIUM band [0.02, 0.05); HIGH is inconsistent.
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(calibrated_probability=0.0452, risk_tier="HIGH"))
    # Surge rule is the documented exception: CRITICAL at any probability.
    TicketInput.build(**base_kwargs(
        calibrated_probability=0.01,
        risk_tier="CRITICAL",
        triggered_rules=["RULE_AC_SURGE_OVER_250V"],
    ))


def test_freshness_age_mismatch_rejected():
    stale_ts = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(measurements=[
            meas("ac_voltage", 228.5, "V", stale_ts, "CURRENT"),
            meas("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
            meas("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
        ]))
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(measurements=[
            meas("ac_voltage", 228.5, "V", TS_AC, "STALE"),
            meas("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
            meas("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
        ]))


def test_invalid_tier_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(risk_tier="URGENT"))


def test_invalid_device_state_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(device_state="OFFLINE_BRIEF"))


def test_unknown_rule_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(triggered_rules=["RULE_INVERTER_IGBT_FRIED"]))


def test_legacy_validator_rule_names_rejected():
    # The old validator's rule vocabulary was never produced by the pipeline.
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(triggered_rules=["RULE_MAINS_FAILURE_WITH_DEGRADATION"]))


def test_known_rule_ids_accepted():
    assert set(KNOWN_RULE_IDS) == {
        "RULE_AC_SURGE_OVER_250V", "RULE_AC_UNDER_50V", "RULE_MAINS_LOSS_RECENT",
    }
    TicketInput.build(**base_kwargs(triggered_rules=sorted(KNOWN_RULE_IDS)))


def test_empty_device_identity_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(device_name="   "))
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(device_id=""))
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(bank=""))


def test_wrong_unit_rejected():
    with pytest.raises(TicketValidationError):
        meas("ac_voltage", 230.0, "A", TS_AC, "CURRENT")
    with pytest.raises(TicketValidationError):
        meas("battery_voltage", 12.0, "kW", TS_AC, "CURRENT")
    with pytest.raises(TicketValidationError):
        meas("time_since_mains_loss", 2.0, "V", TS_AC, "CURRENT")


def test_unknown_measurement_field_rejected():
    with pytest.raises(TicketValidationError):
        meas("acoustic_sensor", 14.0, "V", TS_AC, "CURRENT")


def test_unavailable_with_value_rejected():
    with pytest.raises(TicketValidationError):
        meas("battery_voltage", 12.4, "V", TS_AC, "UNAVAILABLE")


def test_current_without_observed_at_rejected():
    with pytest.raises(TicketValidationError):
        meas("ac_voltage", 220.0, "V", None, "CURRENT")


def test_naive_datetime_rejected():
    with pytest.raises(TicketValidationError):
        meas("ac_voltage", 220.0, "V",
             datetime(2026, 10, 13, 11, 55, 0), "CURRENT")


def test_missing_required_measurement_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(measurements=[
            meas("ac_voltage", 228.5, "V", TS_AC, "CURRENT"),
            meas("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
        ]))


def test_policy_version_mismatch_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(policy_version="ticket-policy-v0"))


def test_connectivity_with_probability_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(
            device_state="STALE_DATA",
            risk_tier="CONNECTIVITY_ISSUE",
            calibrated_probability=0.05,
        ))


def test_connectivity_wrong_tier_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(
            device_state="OFFLINE_LONG",
            risk_tier="HIGH",
            calibrated_probability=None,
        ))


def test_online_with_connectivity_tier_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(
            device_state="ONLINE_OK",
            risk_tier="CONNECTIVITY_ISSUE",
        ))


def test_online_with_null_probability_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(calibrated_probability=None))


def test_disallowed_investigation_action_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(
            approved_investigation_actions=["dispatch-technician-immediately"]))


def test_empty_observed_facts_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(observed_facts=[]))


def test_duplicate_measurement_fields_rejected():
    with pytest.raises(TicketValidationError):
        TicketInput.build(**base_kwargs(measurements=[
            meas("ac_voltage", 228.5, "V", TS_AC, "CURRENT"),
            meas("ac_voltage", 229.0, "V", TS_AC, "CURRENT"),
            meas("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
            meas("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
        ]))


def test_allowed_actions_vocabulary_nonempty():
    assert len(ALLOWED_INVESTIGATION_ACTIONS) > 0
