"""src/tickets/field_validator.py — Stage C field-level ticket validator.

Replaces flat numeric-membership checks with typed field-value-unit
validation: every measurement, probability, identity string, rule ID, claim,
and section is checked as a relationship against the validated TicketInput,
not as set membership.

Design notes (honest limits, see Stage C report):
- This validator checks *grounding relationships* (right value, right unit,
  right field, right polarity, required presence). It does not understand free
  prose semantics beyond its pattern rules; unrestricted-prose validation
  remains unreliable, which is why the deterministic renderer stays the
  production default.
- Claim-polarity checks are negation-aware (a small preceding-negation window)
  to avoid the legacy false rejection of truthful negations.
- Hardware-failure claims are rejected outside labeled POSSIBLE CAUSE
  hypotheses because the typed payload carries no hardware-evidence fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from src.tickets.policy import (
    BATTERY_UNAVAILABLE_WORDING,
    FIELD_ALIASES,
    FORBIDDEN_URGENCY_BY_TIER,
    HARDWARE_FAILURE_PATTERNS,
    MEASUREMENT_FIELDS,
    NO_DISPATCH_FOOTER,
    PAYLOAD_HASH_MARKER,
    PROBABILITY_DISPLAY_TOLERANCE_PCT,
    REQUIRED_SECTION_MARKERS,
    RULE_EVIDENCE_FIELDS,
    STALE_STATES,
    SURGE_RULE_FORCES_CRITICAL,
    TICKET_ID_MARKER,
    VALUE_DISPLAY_TOLERANCE,
    format_probability,
    tier_band_for_probability,
)
from src.tickets.schema import Measurement, TicketInput

NEGATION_WORDS = ("no", "not", "n't", "never", "without", "none", "zero", "neither", "nor")

LOSS_TERMS = (
    "mains loss", "mains failure", "mains disconnected", "mains outage",
    "grid loss", "grid failure", "grid down", "utility power loss",
    "utility power failure", "power outage",
)
NORMAL_TERMS = (
    "mains normal", "mains healthy", "mains online", "mains restored",
    "grid normal", "grid power normal",
)
OFFLINE_TERMS = (
    "device offline", "gateway offline", "site is offline", "site offline",
    "communication blackout", "connectivity loss", "fully unreachable",
)

# Telemetry-label lines carry numbers/keywords as data, not claims; scrub them
# before claim-polarity scanning (measurement binding checks them separately).
LABEL_LINE_PATTERNS = (
    r"time_since_mains_loss\s*:",
    r"time since mains loss\s*:",
    r"mains[- ]loss duration\s*:",
    r"heartbeat_gap\s*:",
    r"heartbeat gap\s*:",
)

MISSING_HARDWARE_PATTERNS = (
    r"\bno battery (installed|present|hardware)\b",
    r"\bbattery (is )?not installed\b",
    r"\bwithout (a )?battery\b",
    r"\bmissing battery\b",
    r"\bno battery\b.{0,10}\b(installed|present|hardware|equipped)\b",
)

NUMBER_PATTERN = r"([-+]?(?:\d+(?:\.\d+)?|\.\d+))(?:[a-zA-Z%]+)?"


@dataclass
class FieldValidationResult:
    is_valid: bool
    violations: List[str] = field(default_factory=list)


def _sentences(text: str) -> List[str]:
    parts = re.split(r"[.\n;]+", text)
    return [p.strip() for p in parts if p.strip()]


def _scrub_label_lines(text: str) -> str:
    kept = []
    for line in text.splitlines():
        low = line.lower()
        if any(re.search(pat, low) for pat in LABEL_LINE_PATTERNS):
            continue
        kept.append(line)
    return "\n".join(kept)


def _claim_polarity(sentence: str, terms: Tuple[str, ...]) -> Optional[str]:
    """Return 'asserted' / 'negated' if any term appears, else None.

    A term preceded within 4 tokens by a negation word counts as negated
    ("no mains loss" negates the loss claim).
    """
    low = sentence.lower()
    for term in terms:
        idx = low.find(term)
        while idx != -1:
            prefix = low[:idx]
            tokens = re.findall(r"[a-z']+", prefix)[-4:]
            if any(t in NEGATION_WORDS for t in tokens):
                return "negated"
            return "asserted"
    return None


def _lines_with_alias(text: str, aliases: Tuple[str, ...]) -> List[str]:
    out = []
    for line in text.splitlines():
        low = line.lower()
        if any(a in low for a in aliases):
            out.append(line)
    return out


def _numbers_in_line(line: str) -> List[float]:
    nums = []
    for m in re.finditer(NUMBER_PATTERN, line):
        try:
            nums.append(float(m.group(1)))
        except ValueError:
            continue
    return nums


def _split_sections(text: str) -> Tuple[str, str]:
    """Split ticket text into (hypothesis_text, factual_text).

    Lines under a POSSIBLE CAUSE header (until the next all-caps header or
    INVESTIGATION marker) are hypotheses; hardware-failure language is permitted
    there as labeled speculation, never as confirmed diagnosis.
    """
    hypo_lines: List[str] = []
    factual_lines: List[str] = []
    in_hypo = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^POSSIBLE CAUSE", stripped.upper()):
            in_hypo = True
            continue
        if in_hypo and (re.match(r"^[A-Z][A-Z \-/]+:$", stripped) or stripped.startswith("INVESTIGATION")):
            in_hypo = False
        (hypo_lines if in_hypo else factual_lines).append(line)
    return "\n".join(hypo_lines), "\n".join(factual_lines)


def validate_candidate(candidate_text: str, ticket_input: TicketInput) -> FieldValidationResult:
    """Validate a candidate ticket string against the typed payload."""
    violations: List[str] = []
    text = candidate_text or ""
    is_connectivity = ticket_input.device_state in STALE_STATES
    hypo_text, factual_text = _split_sections(text)
    scannable = _scrub_label_lines(factual_text + "\n" + hypo_text)
    scannable_factual = _scrub_label_lines(factual_text)

    # 1. Required sections and mandatory fields -------------------------------
    for marker in REQUIRED_SECTION_MARKERS:
        if marker not in text:
            violations.append(f"Missing mandatory section: {marker}.")
    if TICKET_ID_MARKER not in text:
        violations.append("Missing mandatory field: Ticket ID.")
    if PAYLOAD_HASH_MARKER not in text:
        violations.append("Missing mandatory field: input payload hash.")
    else:
        from src.tickets.renderer import canonical_payload_hash

        shown = re.search(r"Input Payload SHA-256:\s*([0-9a-fA-F]{64})", text)
        if shown and shown.group(1).lower() != canonical_payload_hash(ticket_input):
            violations.append(
                "Input payload hash does not match the canonical payload hash."
            )
    if NO_DISPATCH_FOOTER not in text:
        violations.append("Missing mandatory no-dispatch footer.")
    if "Branch Site:" not in text:
        violations.append("Missing mandatory field: Branch Site.")

    # 2. Exact device identity --------------------------------------------------
    if ticket_input.device_id not in text:
        violations.append("Device ID does not match the payload exactly.")
    if ticket_input.device_name not in text:
        violations.append("Device name does not match the payload exactly.")
    if ticket_input.bank not in text:
        violations.append("Bank does not match the payload exactly.")

    # 3. Per-measurement field-value-unit binding --------------------------------
    for m in ticket_input.measurements:
        aliases = FIELD_ALIASES[m.field]
        if m.freshness == "UNAVAILABLE":
            if m.field == "battery_voltage":
                if "battery voltage unavailable" not in text.lower() or "unknown" not in text.lower():
                    violations.append(
                        "Missing-sensor battery measurement lacks the canonical "
                        "unavailable/unknown wording."
                    )
            if re.search(r"\bbattery\s+(?:voltage|bus|level)?\s*(?:is|:)?\s*\d+\.?\d*\s*v\b",
                         scannable_factual.lower()):
                violations.append(
                    "Battery sensor violation: numeric battery voltage claimed "
                    "while the sensor is UNAVAILABLE."
                )
            if re.search(r"\bbattery\s+(depleted|critical|discharged|degraded|low|dying|weak)\b",
                         scannable_factual.lower()):
                violations.append(
                    "Battery sensor violation: battery degradation claimed "
                    "while the sensor is UNAVAILABLE."
                )
            continue
        lines = _lines_with_alias(text, aliases)
        if not lines:
            violations.append(f"Measurement field {m.field!r} is absent from the ticket.")
            continue
        bound = False
        for line in lines:
            nums = _numbers_in_line(line)
            value_ok = any(abs(n - float(m.value)) <= VALUE_DISPLAY_TOLERANCE + 1e-9 for n in nums)
            unit_ok = any(u in line for u in MEASUREMENT_FIELDS[m.field]["units"])
            if value_ok and unit_ok:
                bound = True
                break
        if not bound:
            violations.append(
                f"Field-value-unit mismatch for {m.field}: expected value {m.value} "
                f"with unit {MEASUREMENT_FIELDS[m.field]['units']} on its measurement line."
            )
        # Freshness labeling on the measurement's own lines.
        own_lines = " ".join(lines).lower()
        if m.freshness == "STALE":
            if "last-known" not in own_lines and "stale" not in own_lines:
                violations.append(
                    f"Stale measurement {m.field} is not labeled last-known/stale."
                )
            if re.search(r"\breal-?time\b", own_lines) or re.search(r"\bright now\b", own_lines):
                violations.append(
                    f"Stale measurement {m.field} is described as real-time/current data."
                )
        elif m.freshness == "CURRENT":
            if "last-known" in own_lines or re.search(r"\bstale\b", own_lines):
                violations.append(
                    f"Current measurement {m.field} is mislabeled as stale/last-known."
                )

    # Missing sensor must never be described as missing hardware.
    batt = ticket_input.measurement("battery_voltage")
    if batt.freshness == "UNAVAILABLE":
        for pat in MISSING_HARDWARE_PATTERNS:
            if re.search(pat, scannable.lower()):
                violations.append(
                    "Missing-sensor violation: absent battery sensor described as "
                    "absent battery hardware."
                )
                break

    # 4. Probability: canonical conversion, tier consistency ----------------------
    if ticket_input.calibrated_probability is not None:
        canonical_pct = round(float(ticket_input.calibrated_probability) * 100.0, 1)
        canonical_str = format_probability(ticket_input.calibrated_probability)
        if canonical_str not in text:
            violations.append(
                f"Displayed probability {canonical_str} (canonical rendering of "
                f"{ticket_input.calibrated_probability}) is missing from the ticket."
            )
        for pct in (float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)):
            if abs(pct - canonical_pct) > PROBABILITY_DISPLAY_TOLERANCE_PCT + 1e-9:
                violations.append(
                    f"Probability scale error: ticket shows {pct}% but canonical "
                    f"probability is {canonical_str}."
                )
                break
        for dec in (float(x) for x in re.findall(
                r"calibrated probability\s+(\d+(?:\.\d+)?)(?!\s*%)", text, re.IGNORECASE)):
            if abs(dec - float(ticket_input.calibrated_probability)) > 1e-9:
                violations.append(
                    f"Calibrated-probability figure {dec} contradicts payload "
                    f"{ticket_input.calibrated_probability}."
                )
        # Tier header + band consistency.
        header = re.search(r"\[SYSTEM DIAGNOSTIC TICKET - ([A-Z_]+) RISK\]", text)
        if not header:
            violations.append("Missing diagnostic tier header for an online ticket.")
        elif header.group(1) != ticket_input.risk_tier:
            violations.append(
                f"Tier header {header.group(1)!r} contradicts assigned tier "
                f"{ticket_input.risk_tier!r}."
            )
        expected_band = tier_band_for_probability(float(ticket_input.calibrated_probability))
        surge_exception = (
            ticket_input.risk_tier == "CRITICAL"
            and SURGE_RULE_FORCES_CRITICAL in ticket_input.triggered_rules
        )
        if ticket_input.risk_tier != expected_band and not surge_exception:
            violations.append(
                f"Tier {ticket_input.risk_tier!r} is inconsistent with probability "
                f"{ticket_input.calibrated_probability} (canonical band: {expected_band})."
            )
    else:
        if re.search(r"outage probability\s*:\s*\d", text, re.IGNORECASE):
            violations.append(
                "Connectivity ticket displays a power-outage probability while "
                "electrical condition is unknown."
            )
        if "[SYSTEM CONNECTIVITY TICKET" not in text:
            violations.append("Missing connectivity ticket header for a stale/offline device.")
        for must in ("gateway",):
            if must not in text.lower():
                violations.append(
                    "Connectivity ticket lacks gateway investigation guidance."
                )

    # 5. Urgency wording vs. tier ---------------------------------------------------
    for phrase in FORBIDDEN_URGENCY_BY_TIER.get(ticket_input.risk_tier, ()):
        if phrase in text.lower():
            violations.append(
                f"Urgency wording {phrase!r} is not permitted for tier "
                f"{ticket_input.risk_tier}."
            )
            break

    # 6. Rule IDs: cited ⊆ triggered; triggered must be listed; evidence present -----
    cited_rules = set(re.findall(r"\bRULE_[A-Z0-9_]+\b", text))
    for rule in sorted(cited_rules):
        if rule not in ticket_input.triggered_rules:
            violations.append(
                f"Rule hallucination: cited rule {rule!r} was not triggered."
            )
    for rule in ticket_input.triggered_rules:
        if rule not in text:
            violations.append(f"Triggered rule {rule!r} is not listed in the ticket.")
    from src.tickets.policy import RULE_EVIDENCE_FIELDS

    for rule in ticket_input.triggered_rules:
        needed = RULE_EVIDENCE_FIELDS.get(rule, ())
        if needed and not any(
            ticket_input.measurement(f).freshness != "UNAVAILABLE" for f in needed
        ):
            violations.append(
                f"Rule {rule!r} is triggered but its evidence fields {needed} "
                f"are all UNAVAILABLE."
            )

    # 7. Negation-aware mains / offline claim checks ----------------------------------
    telem_mains = float(ticket_input.measurement("time_since_mains_loss").value or 0.0) \
        if ticket_input.measurement("time_since_mains_loss").freshness != "UNAVAILABLE" else 0.0
    loss_expected = (
        ticket_input.device_state == "ONLINE_POWER_LOSS" or telem_mains > 0.0
    )
    if not is_connectivity:
        for sentence in _sentences(scannable):
            mains_claim = _claim_polarity(sentence, LOSS_TERMS)
            normal_claim = _claim_polarity(sentence, NORMAL_TERMS)
            if loss_expected:
                if normal_claim == "asserted":
                    violations.append(
                        "Mains claim violation: mains described as normal during "
                        "an observed power-loss condition."
                    )
                if mains_claim == "negated":
                    violations.append(
                        "Mains claim violation: power loss denied (negated loss "
                        "claim) during an observed power-loss condition."
                    )
            else:
                if mains_claim == "asserted":
                    violations.append(
                        "Mains claim violation: power loss claimed while mains "
                        "is healthy."
                    )
            offline_claim = _claim_polarity(sentence, OFFLINE_TERMS)
            if ticket_input.device_state in ("ONLINE_OK", "ONLINE_POWER_LOSS"):
                if offline_claim == "asserted":
                    violations.append(
                        "State claim violation: device described as offline while "
                        "it is reporting."
                    )
            if offline_claim == "negated" and is_connectivity:
                violations.append(
                    "State claim violation: offline condition denied on a "
                    "stale/offline device."
                )

    # 8. Unsupported hardware-failure claims (outside labeled hypotheses) ------------
    for pat in HARDWARE_FAILURE_PATTERNS:
        if re.search(pat, scannable_factual.lower()):
            violations.append(
                "Unsupported hardware-failure claim: no breaker, inverter, fiber, "
                "or transformer evidence field exists in the payload."
            )
            break

    return FieldValidationResult(is_valid=not violations, violations=violations)


__all__ = ["FieldValidationResult", "validate_candidate"]
