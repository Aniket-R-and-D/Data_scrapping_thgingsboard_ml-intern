"""POSSIBLE_CAUSE entries must be payload-supported (src/tickets/causes.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.tickets.causes import CAUSE_MAINS_UNKNOWN, derive_possible_causes
from src.tickets.field_validator import LOSS_TERMS, validate_candidate
from src.tickets.policy import POLICY_VERSION
from src.tickets.renderer import render_ticket
from src.tickets.schema import Measurement, TicketInput

PRED = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
FRESH = PRED - timedelta(minutes=5)
OLD = PRED - timedelta(hours=3)


def _m(ac, ac_f, batt=None, batt_f="UNAVAILABLE", mains_h=None):
    def one(field, v, unit, fresh):
        if fresh == "UNAVAILABLE":
            return Measurement(field, None, None, None, "UNAVAILABLE")
        return Measurement(field, v, unit, FRESH if fresh == "CURRENT" else OLD, fresh)
    return [one("ac_voltage", ac, "V", ac_f), one("battery_voltage", batt, "V", batt_f),
            one("time_since_mains_loss", mains_h, "hours", "UNAVAILABLE" if mains_h is None else ac_f)]


def _has_loss_term(causes):
    return any(t in c.lower() for c in causes for t in LOSS_TERMS)


def test_healthy_mains_connectivity_issue_has_no_mains_loss_cause():
    causes = derive_possible_causes("STALE_DATA", "CONNECTIVITY_ISSUE", _m(229.0, "CURRENT"), [])
    assert not _has_loss_term(causes)
    assert not any("power interruption" in c.lower() for c in causes)


def test_mains_loss_with_low_battery_allows_mains_loss_cause():
    causes = derive_possible_causes(
        "ONLINE_POWER_LOSS", "LOW", _m(0.0, "CURRENT", batt=10.2, batt_f="CURRENT", mains_h=0.0),
        ["RULE_AC_UNDER_50V"])
    assert _has_loss_term(causes)
    assert any("battery discharging" in c for c in causes)


def test_unknown_or_stale_mains_is_marked_unknown_not_asserted():
    for meas in (_m(None, "UNAVAILABLE"), _m(231.0, "STALE")):
        causes = derive_possible_causes("OFFLINE_LONG", "CONNECTIVITY_ISSUE", meas, [])
        assert CAUSE_MAINS_UNKNOWN in causes
        assert not _has_loss_term([c for c in causes if c != CAUSE_MAINS_UNKNOWN])


def test_high_tier_healthy_mains_ticket_passes_validator():
    """Regression for the G2 dry-run failure: HIGH tier, mains healthy."""
    meas = _m(226.0, "CURRENT")
    causes = derive_possible_causes("ONLINE_OK", "HIGH", meas, [])
    assert not _has_loss_term(causes)
    ti = TicketInput.build(
        device_id="d1", device_name="SITE-1", bank="BOI", prediction_timestamp_utc=PRED,
        model_version="v2.0-remediated-lightgbm-clean", policy_version=POLICY_VERSION,
        device_state="ONLINE_OK", measurements=meas, calibrated_probability=0.08, risk_tier="HIGH",
        triggered_rules=[], observed_facts=["Gateway online in state ONLINE_OK; assigned risk tier is HIGH (8.0%)."],
        possible_causes=causes, approved_investigation_actions=["monitor-ac-voltage-6h"])
    res = validate_candidate(render_ticket(ti).text, ti)
    assert res.is_valid, res.violations
