"""src/tickets/causes.py — payload-supported POSSIBLE_CAUSE derivation.

Causes are hypotheses, but a hypothesis must not contradict the payload:
- Mains status comes only from the ticket's own measurements, state and rules.
- HEALTHY mains (current AC reading >= 50 V, no loss state/rule) never yields
  a mains-loss cause.
- UNKNOWN mains (AC stale or unavailable) yields an explicit UNKNOWN entry;
  a power interruption is neither asserted nor excluded.
- LOST mains (ONLINE_POWER_LOSS, RULE_AC_UNDER_50V, or current AC < 50 V)
  allows mains-loss causes; a current low battery adds the discharge cause.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

AC_OUTAGE_VOLTS = 50.0          # same boundary as src/features/state_machine.py
BATTERY_CRITICAL_VOLTS = 10.8   # configs/config.yaml outage_label.battery_critical_volts
BATTERY_SENTINEL_VOLTS = 0.5    # R1: readings <= 0.5 V mean an unequipped channel

HEALTHY, LOST, UNKNOWN = "HEALTHY", "LOST", "UNKNOWN"

CAUSE_LINK = "Gateway cellular link or SIM connectivity interruption (unconfirmed)."
CAUSE_SITE_POWER = "Site power interruption affecting the gateway supply (unconfirmed)."
CAUSE_MAINS_UNKNOWN = (
    "Mains status UNKNOWN: electrical telemetry is stale or unavailable, so a site power "
    "interruption is neither confirmed nor excluded."
)
CAUSE_FEEDER = "Upstream utility feeder outage (hypothesis only — unconfirmed)."
CAUSE_DB_TRIP = "Site distribution-board input trip (hypothesis only — unconfirmed)."
CAUSE_BATTERY_DISCHARGE = (
    "Mains loss with battery discharging below 10.8 V (hypothesis only — unconfirmed)."
)
CAUSE_ONGOING_LOSS = "Ongoing mains loss (hypothesis only — unconfirmed)."
CAUSE_AC_INSTABILITY = (
    "AC line voltage depression or historical instability signature (hypothesis only — unconfirmed)."
)
CAUSE_MEDIUM = "Mild AC voltage deviation on recent telemetry window (hypothesis only — unconfirmed)."
CAUSE_NONE = "No abnormal condition suspected; routine monitoring applies."


def _current(measurements: Sequence, field: str) -> Optional[float]:
    for m in measurements:
        if m.field == field and m.freshness == "CURRENT" and m.value is not None:
            return float(m.value)
    return None


def mains_status(device_state: str, measurements: Sequence, triggered_rules: Sequence[str]) -> str:
    ac = _current(measurements, "ac_voltage")
    if (device_state == "ONLINE_POWER_LOSS" or "RULE_AC_UNDER_50V" in triggered_rules
            or (ac is not None and ac < AC_OUTAGE_VOLTS)):
        return LOST
    return HEALTHY if ac is not None else UNKNOWN


def battery_low(measurements: Sequence) -> bool:
    b = _current(measurements, "battery_voltage")
    return b is not None and BATTERY_SENTINEL_VOLTS < b < BATTERY_CRITICAL_VOLTS


def derive_possible_causes(device_state: str, risk_tier: str, measurements: Sequence,
                           triggered_rules: Sequence[str]) -> List[str]:
    mains = mains_status(device_state, measurements, triggered_rules)
    if device_state in ("STALE_DATA", "OFFLINE_LONG"):
        return [CAUSE_LINK] + {HEALTHY: [], LOST: [CAUSE_SITE_POWER], UNKNOWN: [CAUSE_MAINS_UNKNOWN]}[mains]
    if device_state == "ONLINE_POWER_LOSS":
        return [CAUSE_FEEDER, CAUSE_DB_TRIP] + ([CAUSE_BATTERY_DISCHARGE] if battery_low(measurements) else [])
    if risk_tier in ("HIGH", "CRITICAL"):
        return {HEALTHY: [CAUSE_AC_INSTABILITY], LOST: [CAUSE_ONGOING_LOSS],
                UNKNOWN: [CAUSE_AC_INSTABILITY, CAUSE_MAINS_UNKNOWN]}[mains]
    if risk_tier == "MEDIUM":
        return [CAUSE_MEDIUM]
    return [CAUSE_NONE]
