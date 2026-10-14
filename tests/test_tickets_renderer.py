"""tests/test_tickets_renderer.py — Stage B deterministic renderer + LLM boundary tests.

Covers: valid ONLINE_OK / ONLINE_POWER_LOSS tickets, STALE_DATA / OFFLINE_LONG
connectivity routing, missing-battery wording, stale-measurement wording,
rejection paths, probability formatting consistency, no probability on
connectivity tickets, stable ticket_id / payload hash, and the experimental
LLM bridge (default OFF, commentary validated-or-discarded, audit logged).
"""

import re
from datetime import datetime, timezone

import pytest

from src.tickets.llm_bridge import (
    attach_optional_commentary,
    is_experimental_llm_enabled,
    set_experimental_llm_enabled,
    validate_commentary,
)
from src.tickets.renderer import (
    BATTERY_UNAVAILABLE_WORDING,
    canonical_payload_hash,
    format_probability,
    render_ticket,
    stable_ticket_id,
)
from src.tickets.schema import POLICY_VERSION, Measurement, TicketInput, TicketValidationError

TS = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
TS_AC = datetime(2026, 10, 13, 11, 55, 0, tzinfo=timezone.utc)
TS_OLD = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)


def meas(field, value, unit, observed_at, freshness):
    return Measurement(field=field, value=value, unit=unit,
                       observed_at=observed_at, freshness=freshness)


def online_ok_input(**overrides):
    kw = dict(
        device_id="388f1380-31ad-11f1-9c6f-b71bb2771567",
        device_name="BOI-BALLYBAZAR",
        bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION,
        device_state="ONLINE_OK",
        measurements=[
            meas("ac_voltage", 228.5, "V", TS_AC, "CURRENT"),
            meas("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
            meas("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
        ],
        calibrated_probability=0.0452,
        risk_tier="MEDIUM",
        triggered_rules=["RULE_MAINS_LOSS_RECENT"],
        observed_facts=["AC line voltage within nominal band at last fresh observation."],
        possible_causes=["Early mains instability consistent with recent rule trigger."],
        approved_investigation_actions=["monitor-ac-voltage-6h"],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


def power_loss_input(**overrides):
    kw = dict(
        device_id="f0a8f490-d5a5-11f0-81e4-bd8a9470eef2",
        device_name="BOI-BHADRESWAR",
        bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION,
        device_state="ONLINE_POWER_LOSS",
        measurements=[
            meas("ac_voltage", 0.0, "V", TS_AC, "CURRENT"),
            meas("battery_voltage", 11.9, "V", TS_AC, "CURRENT"),
            meas("time_since_mains_loss", 3.5, "hours", TS_AC, "CURRENT"),
        ],
        calibrated_probability=0.1645,
        risk_tier="CRITICAL",
        triggered_rules=["RULE_AC_UNDER_50V"],
        observed_facts=["AC bus at 0.0 V while gateway actively reporting; mains loss ongoing for 3.5 hours."],
        possible_causes=["Utility feeder interruption upstream of the site.",
                         "Site-side breaker trip (unconfirmed)."],
        approved_investigation_actions=[
            "inspect-distribution-board-and-breakers",
            "verify-mains-contactor-position",
        ],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


def stale_input(**overrides):
    kw = dict(
        device_id="dcf3f8c0-d021-11f0-9dd1-0f70161fd1f6",
        device_name="BOI-CHIMTHANA",
        bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION,
        device_state="STALE_DATA",
        measurements=[
            meas("ac_voltage", 220.0, "V", TS_OLD, "STALE"),
            meas("battery_voltage", None, None, None, "UNAVAILABLE"),
            meas("time_since_mains_loss", 168.0, "hours", TS_OLD, "STALE"),
        ],
        calibrated_probability=None,
        risk_tier="CONNECTIVITY_ISSUE",
        triggered_rules=[],
        observed_facts=["No fresh telemetry for over 60 minutes; gateway heartbeat stale."],
        possible_causes=["Cellular link degradation.",
                         "Site power interruption affecting the gateway (unconfirmed)."],
        approved_investigation_actions=[
            "inspect-gateway-power-and-wiring",
            "check-sim-and-network-signal",
        ],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


# --- Online tickets -------------------------------------------------------

def test_valid_online_ok_ticket():
    ticket = render_ticket(online_ok_input())
    assert "[SYSTEM DIAGNOSTIC TICKET - MEDIUM RISK]" in ticket.text
    assert "BOI-BALLYBAZAR (BOI)" in ticket.text
    assert "388f1380-31ad-11f1-9c6f-b71bb2771567" in ticket.text
    assert "24-Hour Outage Probability: 4.5%" in ticket.text
    assert "RULE_MAINS_LOSS_RECENT" in ticket.text
    assert "228.5 V" in ticket.text and "13.8 V" in ticket.text
    assert "OBSERVED" in ticket.text and "POSSIBLE CAUSE" in ticket.text
    assert "INVESTIGATION" in ticket.text
    assert "v2.0-remediated-lightgbm-clean" in ticket.text
    assert POLICY_VERSION in ticket.text


def test_valid_online_power_loss_ticket_no_confirmed_cause():
    ticket = render_ticket(power_loss_input())
    assert "[SYSTEM DIAGNOSTIC TICKET - CRITICAL RISK]" in ticket.text
    assert "16.5%" in ticket.text or "16.4%" in ticket.text  # 0.1645 canonical
    assert "Root cause is not confirmed by this ticket." in ticket.text
    assert "hypothesis" in ticket.text.lower()
    # Must not claim a confirmed failure.
    assert "confirmed failure" not in ticket.text.lower().replace(
        "not a confirmed failure", "").replace("not confirmed", "")


def test_probability_formatting_consistency():
    assert format_probability(0.0452) == "4.5%"
    assert format_probability(0.0) == "0.0%"
    assert format_probability(1.0) == "100.0%"
    ticket = render_ticket(online_ok_input(
        calibrated_probability=0.0985,
        risk_tier="HIGH",
        triggered_rules=["RULE_AC_UNDER_50V", "RULE_MAINS_LOSS_RECENT"],
    ))
    assert "24-Hour Outage Probability: 9.8%" in ticket.text
    assert "(calibrated probability 0.0985)" in ticket.text


# --- Connectivity routing -------------------------------------------------

def test_stale_data_routes_to_connectivity_ticket():
    ticket = render_ticket(stale_input())
    assert "[SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]" in ticket.text
    assert "NOT EVALUATED" in ticket.text
    assert "Electrical condition is unknown" in ticket.text


def test_offline_long_routes_to_connectivity_ticket():
    ticket = render_ticket(stale_input(
        device_state="OFFLINE_LONG",
        device_name="BOI-RANIHAT",
    ))
    assert "[SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]" in ticket.text
    assert "OFFLINE_LONG" in ticket.text


def test_no_power_probability_on_connectivity_tickets():
    for state in ("STALE_DATA", "OFFLINE_LONG"):
        text = render_ticket(stale_input(device_state=state)).text
        assert not re.search(r"Outage Probability:\s*\d", text), state
        assert "NOT EVALUATED (connectivity ticket)" in text


# --- Missing / stale sensor wording ---------------------------------------

def test_missing_battery_sensor_wording():
    text = render_ticket(stale_input()).text
    assert BATTERY_UNAVAILABLE_WORDING in text
    assert "no battery installed" not in text.lower()
    assert "Battery presence and condition are unknown" in text


def test_missing_battery_on_online_ticket():
    ticket = render_ticket(online_ok_input(
        device_name="SBI KUMHRAULI",
        bank="SBI",
        measurements=[
            meas("ac_voltage", 0.0, "V", TS_AC, "CURRENT"),
            meas("battery_voltage", None, None, None, "UNAVAILABLE"),
            meas("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
        ],
        observed_facts=["AC bus at 0.0 V while gateway actively reporting."],
    ))
    assert BATTERY_UNAVAILABLE_WORDING in ticket.text
    assert "no battery installed" not in ticket.text.lower()


def test_stale_measurement_wording():
    text = render_ticket(stale_input()).text
    assert "last-known value 220.0 V" in text
    assert TS_OLD.isoformat() in text
    assert "not current" in text.lower()
    assert "must not be described as current" in text.lower()


# --- Rejection paths (schema enforced before rendering) --------------------

def test_invalid_probability_rejection():
    with pytest.raises(TicketValidationError):
        online_ok_input(calibrated_probability=2.0)


def test_invalid_tier_rejection():
    with pytest.raises(TicketValidationError):
        online_ok_input(risk_tier="SEVERE")


def test_unknown_rule_rejection():
    with pytest.raises(TicketValidationError):
        online_ok_input(triggered_rules=["RULE_BREAKER_TRIPPED"])


def test_wrong_device_identity_rejection():
    with pytest.raises(TicketValidationError):
        online_ok_input(device_name="")


def test_wrong_unit_rejection():
    with pytest.raises(TicketValidationError):
        meas("ac_voltage", 230.0, "A", TS_AC, "CURRENT")


# --- Traceability ----------------------------------------------------------

def test_stable_ticket_id_and_payload_hash():
    first = render_ticket(online_ok_input())
    second = render_ticket(online_ok_input())
    assert first.ticket_id == second.ticket_id
    assert first.payload_hash == second.payload_hash
    assert first.ticket_id.startswith("TKT-")
    assert len(first.payload_hash) == 64
    assert first.payload_hash in first.text

    other = render_ticket(online_ok_input(device_name="BOI-ISLAMPUR"))
    assert other.ticket_id != first.ticket_id
    assert other.payload_hash != first.payload_hash
    assert canonical_payload_hash(online_ok_input()) == first.payload_hash
    assert stable_ticket_id(online_ok_input()) == first.ticket_id


# --- Experimental LLM bridge ----------------------------------------------

@pytest.fixture()
def llm_flag_restore():
    previous = is_experimental_llm_enabled()
    yield
    set_experimental_llm_enabled(previous)


def test_llm_flag_default_off(llm_flag_restore):
    set_experimental_llm_enabled(False)
    assert is_experimental_llm_enabled() is False
    text = render_ticket(online_ok_input()).text
    final, audit = attach_optional_commentary(
        text, online_ok_input(), generate_fn=lambda p: "Anything.")
    assert final == text
    assert audit.decision == "llm-disabled"
    assert audit.commentary_included is False


def test_llm_invalid_commentary_discarded_ticket_retained(llm_flag_restore):
    set_experimental_llm_enabled(True)
    ticket = render_ticket(online_ok_input())
    final, audit = attach_optional_commentary(
        ticket.text, online_ok_input(),
        generate_fn=lambda p: (
            "The outage probability is 10.0% and RULE_BREAKER_TRIPPED. "
            "Dispatch immediately!"),
    )
    assert final == ticket.text  # deterministic ticket retained verbatim
    assert audit.decision == "commentary-discarded"
    assert audit.commentary_included is False
    assert len(audit.violations) >= 2  # probability + rule + dispatch order
    assert audit.raw_output is not None


def test_llm_valid_commentary_appended_and_marked(llm_flag_restore):
    set_experimental_llm_enabled(True)
    ticket = render_ticket(online_ok_input())
    final, audit = attach_optional_commentary(
        ticket.text, online_ok_input(),
        generate_fn=lambda p: (
            "Readers may note the 4.5% probability matches the ticket header; "
            "consider the listed monitor-ac-voltage-6h check first."),
    )
    assert audit.decision == "commentary-kept"
    assert "OPTIONAL MODEL COMMENTARY" in final
    assert "unverified" in final.lower()
    assert ticket.text in final  # authoritative fields untouched


def test_llm_probability_on_connectivity_ticket_rejected(llm_flag_restore):
    set_experimental_llm_enabled(True)
    ticket = render_ticket(stale_input())
    _, audit = attach_optional_commentary(
        ticket.text, stale_input(),
        generate_fn=lambda p: "Outage probability: 72.4%. Urgent power outage!",
    )
    assert audit.decision == "commentary-discarded"
    assert any("connectivity" in v.lower() for v in audit.violations)


def test_validate_commentary_unit():
    valid = validate_commentary("Steady reading, nothing further.", online_ok_input())
    assert valid == []
    assert validate_commentary("", online_ok_input()) != []
