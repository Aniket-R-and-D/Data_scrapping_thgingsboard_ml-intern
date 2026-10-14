"""src/tickets/renderer.py — Stage B deterministic operational ticket renderer.

The default ticket path. Every displayed identifier, number, unit, probability,
timestamp, rule ID, and severity label comes directly from a validated
TicketInput. The renderer never invents causes, hardware names, or diagnoses.

Section vocabulary (mutually exclusive, always labeled):
  OBSERVED      — directly supported by fresh (CURRENT) telemetry or the
                  validated model/policy fields.
  UNKNOWN       — unavailable, stale, or unsupported; stated explicitly.
  POSSIBLE CAUSE — hypothesis from the validated possible_causes list; never
                  a confirmed failure.
  INVESTIGATION — bounded suggested check from approved_investigation_actions;
                  never an automatic dispatch.

No dispatch, alarm creation, or external notification occurs here or anywhere
in this module: rendering is string construction only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from src.tickets.policy import (
    BATTERY_UNAVAILABLE_WORDING,
    ELECTRICAL_UNKNOWN_WORDING,
    ROOT_CAUSE_DISCLAIMER,
    STALE_STATES,
    format_probability,
)
from src.tickets.schema import (
    Measurement,
    TicketInput,
)

BATTERY_UNAVAILABLE_WORDING = BATTERY_UNAVAILABLE_WORDING
ELECTRICAL_UNKNOWN_WORDING = ELECTRICAL_UNKNOWN_WORDING
ROOT_CAUSE_DISCLAIMER = ROOT_CAUSE_DISCLAIMER


def format_probability(calibrated_probability: float) -> str:
    """Canonical probability formatter (re-exported from policy; single source)."""
    from src.tickets.policy import format_probability as _canonical

    return _canonical(calibrated_probability)


def canonical_payload_hash(ticket_input: TicketInput) -> str:
    """Stable SHA-256 over the canonical JSON form of the validated input."""
    payload = {
        "device_id": ticket_input.device_id,
        "device_name": ticket_input.device_name,
        "bank": ticket_input.bank,
        "prediction_timestamp_utc": ticket_input.prediction_timestamp_utc.isoformat(),
        "model_version": ticket_input.model_version,
        "policy_version": ticket_input.policy_version,
        "device_state": ticket_input.device_state,
        "measurements": [
            {
                "field": m.field,
                "value": m.value,
                "unit": m.unit,
                "observed_at": m.observed_at.isoformat() if m.observed_at else None,
                "freshness": m.freshness,
            }
            for m in sorted(ticket_input.measurements, key=lambda x: x.field)
        ],
        "calibrated_probability": ticket_input.calibrated_probability,
        "risk_tier": ticket_input.risk_tier,
        "triggered_rules": sorted(ticket_input.triggered_rules),
        "observed_facts": list(ticket_input.observed_facts),
        "possible_causes": list(ticket_input.possible_causes),
        "approved_investigation_actions": list(ticket_input.approved_investigation_actions),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_ticket_id(ticket_input: TicketInput) -> str:
    """Deterministic ticket identifier derived from the canonical payload hash."""
    return "TKT-" + canonical_payload_hash(ticket_input)[:12].upper()


def _format_measurement_line(m: Measurement) -> str:
    if m.freshness == "UNAVAILABLE":
        if m.field == "battery_voltage":
            return BATTERY_UNAVAILABLE_WORDING
        return (
            f"{m.field}: UNKNOWN (no usable observation available; "
            f"value, unit, and condition are unknown)."
        )
    stamp = m.observed_at.isoformat() if m.observed_at else "unknown time"
    if m.freshness == "STALE":
        return (
            f"{m.field}: last-known value {m.value} {m.unit}, observed at "
            f"{stamp} (STALE — last-known, not current)."
        )
    return f"{m.field}: {m.value} {m.unit}, observed at {stamp} (CURRENT)."


@dataclass(frozen=True)
class RenderedTicket:
    ticket_id: str
    payload_hash: str
    text: str


def render_ticket(ticket_input: TicketInput) -> RenderedTicket:
    """Render a validated TicketInput into a deterministic ticket string."""
    payload_hash = canonical_payload_hash(ticket_input)
    ticket_id = stable_ticket_id(ticket_input)
    is_connectivity = ticket_input.device_state in STALE_STATES

    lines: List[str] = []
    if is_connectivity:
        lines.append("[SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]")
    else:
        lines.append(f"[SYSTEM DIAGNOSTIC TICKET - {ticket_input.risk_tier} RISK]")
    lines.append(f"Ticket ID: {ticket_id}")
    lines.append(f"Branch Site: {ticket_input.device_name} ({ticket_input.bank})")
    lines.append(f"Device ID: {ticket_input.device_id}")
    lines.append(f"Device State: {ticket_input.device_state}")
    lines.append(f"Prediction Timestamp (UTC): {ticket_input.prediction_timestamp_utc.isoformat()}")
    lines.append(f"Model Version: {ticket_input.model_version}")
    lines.append(f"Policy Version: {ticket_input.policy_version}")
    lines.append("")

    # Probability: online tickets only, from the validated canonical field.
    if not is_connectivity and ticket_input.calibrated_probability is not None:
        lines.append(
            f"24-Hour Outage Probability: "
            f"{format_probability(ticket_input.calibrated_probability)} "
            f"(calibrated probability {ticket_input.calibrated_probability})"
        )
        lines.append(f"Assigned Risk Tier: {ticket_input.risk_tier}")
    elif is_connectivity:
        lines.append("24-Hour Outage Probability: NOT EVALUATED (connectivity ticket)")
        lines.append("Assigned Status: CONNECTIVITY_ISSUE")
    lines.append("")

    rules_str = ", ".join(sorted(ticket_input.triggered_rules)) if ticket_input.triggered_rules else "None"
    lines.append(f"Triggered Rules: {rules_str}")
    lines.append("")

    # OBSERVED
    lines.append("OBSERVED (directly supported by fresh telemetry or validated fields):")
    for fact in ticket_input.observed_facts:
        lines.append(f"- {fact}")
    for m in sorted(ticket_input.measurements, key=lambda x: x.field):
        # Battery-absent wording is fixed; never "no battery installed".
        lines.append(f"- {_format_measurement_line(m)}")
    lines.append("")

    # UNKNOWN
    unknowns: List[str] = []
    if is_connectivity:
        unknowns.append(ELECTRICAL_UNKNOWN_WORDING)
    batt = ticket_input.measurement("battery_voltage")
    if batt.freshness == "UNAVAILABLE":
        unknowns.append(
            "Battery presence and condition are unknown: absence of a "
            "battery-voltage sensor must not be read as absence of battery hardware."
        )
    stale_fields = [m.field for m in ticket_input.measurements if m.freshness == "STALE"]
    if stale_fields:
        unknowns.append(
            f"Stale measurements ({', '.join(sorted(stale_fields))}) are last-known "
            f"values only and must not be described as current conditions."
        )
    if unknowns:
        lines.append("UNKNOWN (unavailable, stale, or unsupported):")
        for u in unknowns:
            lines.append(f"- {u}")
        lines.append("")

    # POSSIBLE CAUSE (hypotheses only)
    if ticket_input.possible_causes:
        lines.append("POSSIBLE CAUSE (hypothesis only — not a confirmed failure):")
        for cause in ticket_input.possible_causes:
            lines.append(f"- {cause}")
        lines.append("")
    if not is_connectivity and ticket_input.device_state == "ONLINE_POWER_LOSS":
        lines.append(ROOT_CAUSE_DISCLAIMER)
        lines.append("")

    # INVESTIGATION (bounded suggested checks, not dispatch)
    lines.append("INVESTIGATION (suggested checks — not an automatic dispatch):")
    for action in ticket_input.approved_investigation_actions:
        lines.append(f"- {action}")
    lines.append("")
    lines.append("No automatic dispatch, alarm creation, or external notification "
                 "was performed for this ticket.")
    lines.append(f"Input Payload SHA-256: {payload_hash}")

    return RenderedTicket(ticket_id=ticket_id, payload_hash=payload_hash, text="\n".join(lines))


def render_connectivity_ticket_without_probability(ticket_input: TicketInput) -> RenderedTicket:
    """Explicit connectivity path: refuses any probability-bearing input."""
    if ticket_input.calibrated_probability is not None:
        raise ValueError(
            "Connectivity tickets must not carry a power-outage probability."
        )
    return render_ticket(ticket_input)


__all__ = [
    "BATTERY_UNAVAILABLE_WORDING",
    "ELECTRICAL_UNKNOWN_WORDING",
    "ROOT_CAUSE_DISCLAIMER",
    "RenderedTicket",
    "canonical_payload_hash",
    "format_probability",
    "render_connectivity_ticket_without_probability",
    "render_ticket",
    "stable_ticket_id",
]
