"""src/tickets/policy.py — Stage C authoritative ticket policy (single source of truth).

All renderer, schema, and validator code must import these rules from this
module. No hardcoded duplicate thresholds elsewhere.

Policy version is reported in every ticket and test artifact.
"""

from __future__ import annotations

from datetime import timedelta

POLICY_VERSION = "ticket-policy-v1"

# --- Device states and risk tiers ------------------------------------------
DEVICE_STATES = ("ONLINE_OK", "ONLINE_POWER_LOSS", "STALE_DATA", "OFFLINE_LONG")
STALE_STATES = ("STALE_DATA", "OFFLINE_LONG")

RISK_TIERS = ("LOW", "MEDIUM", "HIGH", "CRITICAL", "CONNECTIVITY_ISSUE")

FRESHNESS_VALUES = ("CURRENT", "STALE", "UNAVAILABLE")

# --- Tier-to-probability bands ----------------------------------------------
# Mirrors the canonical assignment in src/shadow/run_shadow_remediation.py
# (assign_risk_tier): CRITICAL p >= 0.15; HIGH 0.05 <= p < 0.15;
# MEDIUM 0.02 <= p < 0.05; LOW p < 0.02. STALE/OFFLINE stations always yield
# CONNECTIVITY_ISSUE; an AC surge rule forces CRITICAL regardless of p.
TIER_PROBABILITY_BANDS = {
    "LOW": (0.0, 0.02),
    "MEDIUM": (0.02, 0.05),
    "HIGH": (0.05, 0.15),
    "CRITICAL": (0.15, 1.0),
}
SURGE_RULE_FORCES_CRITICAL = "RULE_AC_SURGE_OVER_250V"


def tier_band_for_probability(calibrated_probability: float) -> str:
    """Canonical tier band for a probability (surge exception handled by caller)."""
    p = float(calibrated_probability)
    for tier, (lo, hi) in TIER_PROBABILITY_BANDS.items():
        upper_ok = p <= hi if tier == "CRITICAL" else p < hi
        if p >= lo and upper_ok:
            return tier
    raise ValueError(f"Probability {calibrated_probability!r} outside [0, 1].")


# --- Measurements: allowed units and field aliases --------------------------
MEASUREMENT_FIELDS = {
    "ac_voltage": {"units": ("V",)},
    "battery_voltage": {"units": ("V",)},
    "time_since_mains_loss": {"units": ("hours", "h")},
    "heartbeat_gap": {"units": ("seconds", "s")},
}
REQUIRED_MEASUREMENT_FIELDS = ("ac_voltage", "battery_voltage", "time_since_mains_loss")

# Human-readable aliases accepted by the field validator when locating a
# measurement line (supports paraphrased tickets).
FIELD_ALIASES = {
    "ac_voltage": ("ac_voltage", "ac voltage", "ac line voltage"),
    "battery_voltage": ("battery_voltage", "battery voltage", "battery bus voltage"),
    "time_since_mains_loss": (
        "time_since_mains_loss",
        "time since mains loss",
        "mains loss duration",
        "mains-loss duration",
    ),
    "heartbeat_gap": ("heartbeat_gap", "heartbeat gap"),
}

# --- Freshness thresholds ----------------------------------------------------
# CURRENT means observed within the same window that keeps a device ONLINE
# (telemetry age <= 60 minutes, matching the STALE_DATA state boundary).
FRESHNESS_CURRENT_MAX_AGE = timedelta(minutes=60)

# --- Canonical probability formatting ----------------------------------------
PROBABILITY_DISPLAY_DECIMALS = 1
# Displayed percentages must match the canonical value within this tolerance
# (percentage points). A drift beyond it (e.g. 9.9% shown for 9.8%) is rejected.
PROBABILITY_DISPLAY_TOLERANCE_PCT = 0.05
# Displayed measurement values must match payload values within this tolerance
# (documented rounding window for one-decimal display).
VALUE_DISPLAY_TOLERANCE = 0.05


def format_probability(calibrated_probability: float) -> str:
    """Single canonical probability formatter: percent, one decimal.

    All ticket text (renderer and any experimental commentary) must derive
    displayed percentages from this function, never from inline formatting.
    """
    return f"{float(calibrated_probability) * 100.0:.{PROBABILITY_DISPLAY_DECIMALS}f}%"


# --- Canonical rule IDs and required evidence ---------------------------------
KNOWN_RULE_IDS = frozenset({
    "RULE_AC_SURGE_OVER_250V",
    "RULE_AC_UNDER_50V",
    "RULE_MAINS_LOSS_RECENT",
})

# Each triggered rule must be supported by at least one present (non-UNAVAILABLE)
# measurement from its evidence set; otherwise the ticket is rejected.
RULE_EVIDENCE_FIELDS = {
    "RULE_AC_SURGE_OVER_250V": ("ac_voltage",),
    "RULE_AC_UNDER_50V": ("ac_voltage",),
    "RULE_MAINS_LOSS_RECENT": ("time_since_mains_loss",),
}

# --- Hardware-failure claim patterns ------------------------------------------
# The typed payload carries no hardware-evidence fields (no breaker, inverter,
# IGBT, fiber, or transformer sensors exist in the telemetry schema), so any
# such failure claim outside a labeled POSSIBLE CAUSE hypothesis is rejected.
HARDWARE_FAILURE_PATTERNS = (
    r"circuit breaker (tripped|open|failed|fault)",
    r"breaker (tripped|open|failed|fault)",
    r"inverter \S+(.{0,20}\b(failed|fried|fault|dead|burnt))",
    r"inverter (failed|fried|fault|dead|burnt)",
    r"\bigbt\b.{0,20}\b(failed|fried|fault|dead|bridge)",
    r"fiber (switch|cable|link)?.{0,20}\b(failed|failure|fault|down|cut)",
    r"transformer (failed|fault|blown)",
    r"\bups\b.{0,20}\b(failed|dead|fault)",
)

# --- Urgency language policy ----------------------------------------------------
# Phrases forbidden per tier (case-insensitive substring match).
FORBIDDEN_URGENCY_BY_TIER = {
    "LOW": (
        "emergency", "extreme danger", "imminent blackout", "urgent emergency",
        "mobilize", "immediate dispatch", "dispatch immediately", "right now",
    ),
    "MEDIUM": ("extreme danger", "imminent blackout"),
    "HIGH": (),
    "CRITICAL": (),
    "CONNECTIVITY_ISSUE": (
        "urgent power outage", "power outage in progress", "imminent blackout",
    ),
}

# --- Bounded investigation-action vocabulary --------------------------------------
ALLOWED_INVESTIGATION_ACTIONS = frozenset({
    "inspect-gateway-power-and-wiring",
    "check-sim-and-network-signal",
    "verify-site-power-at-premises",
    "monitor-ac-voltage-6h",
    "schedule-battery-load-test-offpeak",
    "inspect-distribution-board-and-breakers",
    "verify-mains-contactor-position",
    "check-inverter-and-ups-status",
    "continue-automated-telemetry-polling",
})

# --- Required ticket sections and mandatory fields ----------------------------------
REQUIRED_SECTION_MARKERS = ("OBSERVED", "INVESTIGATION")
NO_DISPATCH_FOOTER = "No automatic dispatch"
PAYLOAD_HASH_MARKER = "Input Payload SHA-256:"
TICKET_ID_MARKER = "Ticket ID:"

# Canonical missing-sensor wording (exact string required when a battery-voltage
# sensor is absent; "no battery installed" style claims are rejected).
BATTERY_UNAVAILABLE_WORDING = (
    "Battery voltage unavailable: no configured battery-voltage sensor. "
    "Battery presence and condition are unknown."
)

ELECTRICAL_UNKNOWN_WORDING = (
    "Electrical condition is unknown: no fresh telemetry is available during "
    "this communication blackout, so no statement about utility power, mains "
    "state, or battery condition can be made."
)

ROOT_CAUSE_DISCLAIMER = (
    "Root cause is not confirmed by this ticket. Listed causes are hypotheses "
    "only (see POSSIBLE CAUSE); confirmation requires on-site investigation."
)
