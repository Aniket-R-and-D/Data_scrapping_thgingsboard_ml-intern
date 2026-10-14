"""tests/test_field_level_validator.py — Stage C adversarial + paraphrase suite.

Every case records: input payload, candidate output, expected decision, actual
decision, failure reason, and whether it guards false acceptance (FA) or false
rejection (FR). No model inference. No fabricated human labels.
"""

from datetime import datetime, timezone

import pytest

from src.tickets.field_validator import validate_candidate
from src.tickets.renderer import canonical_payload_hash, render_ticket, stable_ticket_id
from src.tickets.schema import POLICY_VERSION, Measurement, TicketInput

TS = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
TS_AC = datetime(2026, 10, 13, 11, 55, 0, tzinfo=timezone.utc)
TS_OLD = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)


def m(field, value, unit, observed_at, freshness):
    return Measurement(field=field, value=value, unit=unit,
                       observed_at=observed_at, freshness=freshness)


def healthy_input(**overrides):
    kw = dict(
        device_id="388f1380-31ad-11f1-9c6f-b71bb2771567",
        device_name="BOI-BALLYBAZAR", bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION, device_state="ONLINE_OK",
        measurements=[
            m("ac_voltage", 228.5, "V", TS_AC, "CURRENT"),
            m("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
            m("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
        ],
        calibrated_probability=0.008, risk_tier="LOW",
        triggered_rules=[],
        observed_facts=["No mains loss observed; telemetry fresh at last poll."],
        possible_causes=["No fault suspected; routine monitoring applies."],
        approved_investigation_actions=["continue-automated-telemetry-polling"],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


def loss_input(**overrides):
    kw = dict(
        device_id="f0a8f490-d5a5-11f0-81e4-bd8a9470eef2",
        device_name="BOI-BHADRESWAR", bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION, device_state="ONLINE_POWER_LOSS",
        measurements=[
            m("ac_voltage", 0.0, "V", TS_AC, "CURRENT"),
            m("battery_voltage", 11.9, "V", TS_AC, "CURRENT"),
            m("time_since_mains_loss", 3.5, "hours", TS_AC, "CURRENT"),
        ],
        calibrated_probability=0.1645, risk_tier="CRITICAL",
        triggered_rules=["RULE_AC_UNDER_50V"],
        observed_facts=["AC bus at 0.0 V while gateway actively reporting."],
        possible_causes=["Utility feeder interruption upstream of the site."],
        approved_investigation_actions=["inspect-distribution-board-and-breakers"],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


def stale_input(**overrides):
    kw = dict(
        device_id="dcf3f8c0-d021-11f0-9dd1-0f70161fd1f6",
        device_name="BOI-CHIMTHANA", bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION, device_state="STALE_DATA",
        measurements=[
            m("ac_voltage", 220.0, "V", TS_OLD, "STALE"),
            m("battery_voltage", None, None, None, "UNAVAILABLE"),
            m("time_since_mains_loss", 168.0, "hours", TS_OLD, "STALE"),
        ],
        calibrated_probability=None, risk_tier="CONNECTIVITY_ISSUE",
        triggered_rules=[],
        observed_facts=["No fresh telemetry for over 60 minutes."],
        possible_causes=["Cellular link degradation."],
        approved_investigation_actions=["inspect-gateway-power-and-wiring"],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


def nobatt_input(**overrides):
    kw = dict(
        device_id="1b016000-859d-11f0-b8ab-b38af83f6c9c",
        device_name="SBI KUMHRAULI", bank="SBI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION, device_state="ONLINE_OK",
        measurements=[
            m("ac_voltage", 0.0, "V", TS_AC, "CURRENT"),
            m("battery_voltage", None, None, None, "UNAVAILABLE"),
            m("time_since_mains_loss", 0.5, "hours", TS_AC, "CURRENT"),
        ],
        calibrated_probability=0.0452, risk_tier="MEDIUM",
        triggered_rules=["RULE_AC_UNDER_50V", "RULE_MAINS_LOSS_RECENT"],
        observed_facts=["AC bus at 0.0 V while gateway actively reporting."],
        possible_causes=["Utility feeder interruption upstream of the site."],
        approved_investigation_actions=["monitor-ac-voltage-6h"],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


def voltage_210_input(**overrides):
    kw = dict(
        device_id="aa46cc10-9d3e-11f1-8f2a-2b7c9d44e5f1",
        device_name="BOI-CHANDANNAGAR", bank="BOI",
        prediction_timestamp_utc=TS,
        model_version="v2.0-remediated-lightgbm-clean",
        policy_version=POLICY_VERSION, device_state="ONLINE_OK",
        measurements=[
            m("ac_voltage", 210.0, "V", TS_AC, "CURRENT"),
            m("battery_voltage", 13.5, "V", TS_AC, "CURRENT"),
            m("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
        ],
        calibrated_probability=0.005, risk_tier="LOW",
        triggered_rules=[],
        observed_facts=["AC line voltage steady at last fresh observation."],
        possible_causes=["No fault suspected; routine monitoring applies."],
        approved_investigation_actions=["continue-automated-telemetry-polling"],
    )
    kw.update(overrides)
    return TicketInput.build(**kw)


def rendered(payload):
    return render_ticket(payload).text


CASES = []


def case(case_id, kind, payload, candidate, expected, reason):
    CASES.append({
        "case_id": case_id, "kind": kind, "payload": payload,
        "candidate": candidate, "expected": expected, "reason": reason,
    })


# --- False-acceptance guards (expected: reject) ------------------------------
_p = loss_input()
case("FA-01-swapped-values", "FA", _p,
     rendered(_p).replace("ac_voltage: 0.0 V", "ac_voltage: 11.9 V")
                  .replace("battery_voltage: 11.9 V", "battery_voltage: 0.0 V"),
     False, "AC and battery values swapped between fields.")

_p = healthy_input()
case("FA-02-wrong-units", "FA", _p,
     rendered(_p).replace("ac_voltage: 228.5 V", "ac_voltage: 228.5 A")
                  .replace("battery_voltage: 13.8 V", "battery_voltage: 13.8 kW"),
     False, "Voltage fields shown in current/power units.")

_p = healthy_input()
case("FA-03-percent-scale", "FA", _p,
     rendered(_p).replace("0.8%", "0.008%"),
     False, "Decimal probability shown as percent (0.008 rendered as 0.008%).")

_p = loss_input()
case("FA-04-negated-loss-during-loss", "FA", _p,
     rendered(_p) + "\nNo mains loss detected at this site; grid supply verified normal.",
     False, "Power loss denied during an observed power-loss condition.")

_p = healthy_input()
case("FA-05-breaker-diagnosis", "FA", _p,
     rendered(_p) + "\nOBSERVED: Circuit breaker tripped at the mains panel; inverter IGBT bridge fried.",
     False, "Unsupported circuit-breaker/inverter failure diagnosis without evidence fields.")

_p = nobatt_input()
case("FA-06-sensor-as-hardware", "FA", _p,
     rendered(_p).replace(
         "Battery voltage unavailable: no configured battery-voltage sensor. "
         "Battery presence and condition are unknown.",
         "No battery installed at this site."),
     False, "Missing battery sensor described as missing battery hardware.")

_p = stale_input()
case("FA-07-stale-as-current", "FA", _p,
     rendered(_p).replace(
         "ac_voltage: last-known value 220.0 V, observed at 2026-10-12T09:00:00+00:00 "
         "(STALE \u2014 last-known, not current).",
         "ac_voltage: 220.0 V, observed at 2026-10-12T09:00:00+00:00 "
         "(CURRENT - real-time measurement right now)."),
     False, "Stale last-known value described as current real-time data.")

_p = healthy_input()
case("FA-08-wrong-identity", "FA", _p,
     rendered(_p).replace("BOI-BALLYBAZAR (BOI)", "SBI-AHMEDABAD (SBI)"),
     False, "Wrong device name and bank.")

_p = healthy_input()
case("FA-09-emergency-on-low", "FA", _p,
     rendered(_p) + "\nIMMEDIATE EMERGENCY DISPATCH! Mobilize right now! Extreme danger!",
     False, "Emergency/escalation language on a LOW-risk ticket.")

_p = healthy_input()
_t = rendered(_p)
_t = "\n".join(line for line in _t.splitlines()
               if not line.startswith(("OBSERVED", "INVESTIGATION"))
               and line != "No automatic dispatch, alarm creation, or external notification was performed for this ticket."
               and not line.startswith("Input Payload SHA-256:"))
case("FA-10-missing-sections", "FA", _p, _t,
     False, "Mandatory OBSERVED/INVESTIGATION sections and traceability fields removed.")

# --- False-rejection guards (expected: accept) --------------------------------
_p = healthy_input()
case("FR-01-truthful-negation", "FR", _p,
     rendered(_p) + "\nThere is no mains loss and the site is not offline.",
     True, "Truthful negations on a healthy site must not trip claim checks.")

_p = voltage_210_input()
case("FR-02-attached-units", "FR", _p,
     rendered(_p).replace("ac_voltage: 210.0 V,", "AC voltage = 210V,"),
     True, "Attached-unit voltage (210V) must bind to payload 210.0 V.")

_p = healthy_input(calibrated_probability=0.008)
_p53 = healthy_input(
    measurements=[
        m("ac_voltage", 228.53, "V", TS_AC, "CURRENT"),
        m("battery_voltage", 13.8, "V", TS_AC, "CURRENT"),
        m("time_since_mains_loss", 0.0, "hours", TS_AC, "CURRENT"),
    ])
case("FR-03-rounding-tolerance", "FR", _p53,
     rendered(_p53).replace("ac_voltage: 228.53 V", "ac_voltage: 228.5 V"),
     True, "One-decimal display within documented tolerance must be accepted.")

_p = nobatt_input()
case("FR-04-valid-unknown-battery", "FR", _p,
     rendered(_p),
     True, "Canonical missing-sensor wording must be accepted.")

_p = stale_input()
case("FR-05-valid-connectivity", "FR", _p,
     rendered(_p),
     True, "Valid connectivity ticket without a probability must be accepted.")


@pytest.mark.parametrize("c", CASES, ids=[c["case_id"] for c in CASES])
def test_adversarial_matrix(c):
    result = validate_candidate(c["candidate"], c["payload"])
    assert result.is_valid is c["expected"], (
        f"{c['case_id']} ({c['kind']}): expected valid={c['expected']}, "
        f"got valid={result.is_valid}. Reason: {c['reason']}. "
        f"Violations: {result.violations}"
    )


def adversarial_summary():
    """Recompute the matrix for reporting: (FA count, FR count) of errors."""
    fa_errors = fr_errors = 0
    for c in CASES:
        actual = validate_candidate(c["candidate"], c["payload"]).is_valid
        if actual != c["expected"]:
            if c["kind"] == "FA":
                fa_errors += 1
            else:
                fr_errors += 1
    return fa_errors, fr_errors


def test_no_false_acceptance_no_false_rejection():
    fa_errors, fr_errors = adversarial_summary()
    assert fa_errors == 0, f"{fa_errors} false acceptances"
    assert fr_errors == 0, f"{fr_errors} false rejections"


def test_renderer_outputs_validate_clean():
    for payload in (healthy_input(), loss_input(), stale_input(),
                    nobatt_input(), voltage_210_input()):
        result = validate_candidate(rendered(payload), payload)
        assert result.is_valid, result.violations


# --- Paraphrase robustness (10 variants, all expected valid) -------------------
def _paraphrase_base():
    payload = healthy_input()
    digest = canonical_payload_hash(payload)
    tid = stable_ticket_id(payload)
    return payload, digest, tid


def _paraphrase_template(body_measurements, extra_fact, order=("obs", "cause", "inv")):
    payload, digest, tid = _paraphrase_base()
    sections = {
        "obs": ("OBSERVED (directly supported by fresh telemetry or validated fields):\n"
                f"- {extra_fact}\n{body_measurements}"),
        "cause": ("POSSIBLE CAUSE (hypothesis only \u2014 not a confirmed failure):\n"
                  "- No fault suspected; routine monitoring applies."),
        "inv": ("INVESTIGATION (suggested checks \u2014 not an automatic dispatch):\n"
                "- continue-automated-telemetry-polling"),
    }
    ordered = "\n\n".join(sections[k] for k in order)
    return payload, (
        "[SYSTEM DIAGNOSTIC TICKET - LOW RISK]\n"
        f"Ticket ID: {tid}\n"
        "Branch Site: BOI-BALLYBAZAR (BOI)\n"
        "Device ID: 388f1380-31ad-11f1-9c6f-b71bb2771567\n"
        "Device State: ONLINE_OK\n"
        "Prediction Timestamp (UTC): 2026-10-13T12:00:00+00:00\n"
        "Model Version: v2.0-remediated-lightgbm-clean\n"
        "Policy Version: ticket-policy-v1\n\n"
        "24-Hour Outage Probability: 0.8% (calibrated probability 0.008)\n"
        "Assigned Risk Tier: LOW\n\n"
        "Triggered Rules: None\n\n"
        f"{ordered}\n\n"
        "No automatic dispatch, alarm creation, or external notification was performed for this ticket.\n"
        f"Input Payload SHA-256: {digest}"
    )


MEAS_STD = ("- ac_voltage: 228.5 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).\n"
            "- battery_voltage: 13.8 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).\n"
            "- time_since_mains_loss: 0.0 hours, observed at 2026-10-13T11:55:00+00:00 (CURRENT).")


def paraphrase_variants():
    payload, digest, tid = _paraphrase_base()
    variants = []
    # P-01: reordered sections (investigation first).
    p, t = _paraphrase_template(MEAS_STD, "Telemetry reviewed at last poll; all readings nominal.",
                                order=("inv", "obs", "cause"))
    variants.append(("P-01-reordered-sections", p, t))
    # P-02: investigate synonyms in facts.
    p, t = _paraphrase_template(MEAS_STD, "Readings examined and checked at last poll; mains steady.")
    variants.append(("P-02-investigate-synonyms", p, t))
    # P-03: alias measurement labels.
    meas_alias = ("- AC line voltage reads 228.5 V as of 2026-10-13T11:55:00+00:00 (CURRENT).\n"
                  "- Battery voltage is 13.8 V as of 2026-10-13T11:55:00+00:00 (CURRENT).\n"
                  "- Time since mains loss: 0.0 hours as of 2026-10-13T11:55:00+00:00 (CURRENT).")
    p, t = _paraphrase_template(meas_alias, "No mains loss observed; telemetry fresh at last poll.")
    variants.append(("P-03-alias-labels", p, t))
    # P-04: compact single-line measurements.
    meas_compact = ("- ac_voltage 228.5 V @ 2026-10-13T11:55:00+00:00 (CURRENT); "
                    "battery_voltage 13.8 V @ 2026-10-13T11:55:00+00:00 (CURRENT); "
                    "time_since_mains_loss 0.0 hours @ 2026-10-13T11:55:00+00:00 (CURRENT).")
    p, t = _paraphrase_template(meas_compact, "All three channels nominal at last poll.")
    variants.append(("P-04-compact-lines", p, t))
    # P-05: extra benign sentence.
    p, t = _paraphrase_template(MEAS_STD, "No mains loss observed; telemetry fresh at last poll. "
                                          "Next poll scheduled normally.")
    variants.append(("P-05-extra-benign-sentence", p, t))
    # P-06: header fields reordered.
    p, t = _paraphrase_template(MEAS_STD, "No mains loss observed; telemetry fresh at last poll.")
    t = t.replace("Ticket ID: " + tid + "\nBranch Site: BOI-BALLYBAZAR (BOI)",
                  "Branch Site: BOI-BALLYBAZAR (BOI)\nTicket ID: " + tid)
    variants.append(("P-06-reordered-header", p, t))
    # P-07: alternative cause phrasing.
    p, t = _paraphrase_template(MEAS_STD, "Telemetry looks steady tonight.")
    t = t.replace("No fault suspected; routine monitoring applies.",
                  "Nothing suspected at present; keep routine watch.")
    variants.append(("P-07-cause-rephrase", p, t))
    # P-08: equals-sign measurement style.
    meas_eq = ("- ac_voltage = 228.5 V (observed 2026-10-13T11:55:00+00:00, CURRENT).\n"
               "- battery_voltage = 13.8 V (observed 2026-10-13T11:55:00+00:00, CURRENT).\n"
               "- time_since_mains_loss = 0.0 hours (observed 2026-10-13T11:55:00+00:00, CURRENT).")
    p, t = _paraphrase_template(meas_eq, "No mains loss observed; telemetry fresh at last poll.")
    variants.append(("P-08-equals-style", p, t))
    # P-09: investigation section expanded with benign note.
    p, t = _paraphrase_template(MEAS_STD, "No mains loss observed; telemetry fresh at last poll.")
    t = t.replace("- continue-automated-telemetry-polling",
                  "- continue-automated-telemetry-polling\n  (suggested check only)")
    variants.append(("P-09-investigation-note", p, t))
    # P-10: full reword with aliases and reorder.
    meas_mix = ("- AC voltage: 228.5 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).\n"
                "- Battery bus voltage: 13.8 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).\n"
                "- Mains loss duration: 0.0 hours, observed at 2026-10-13T11:55:00+00:00 (CURRENT).")
    p, t = _paraphrase_template(meas_mix, "Evening review complete; readings steady.",
                                order=("obs", "inv", "cause"))
    variants.append(("P-10-mixed-reword", p, t))
    return variants


@pytest.mark.parametrize("variant_id,payload,text",
                         [(v[0], v[1], v[2]) for v in paraphrase_variants()],
                         ids=[v[0] for v in paraphrase_variants()])
def test_paraphrase_variants_accepted(variant_id, payload, text):
    result = validate_candidate(text, payload)
    assert result.is_valid, f"{variant_id}: {result.violations}"
