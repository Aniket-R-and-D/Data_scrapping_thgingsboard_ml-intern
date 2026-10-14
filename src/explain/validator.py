"""src/explain/validator.py
Comprehensive Faithfulness Validator & Deterministic Template Fallback.

Enforces zero hallucination across two rigorous layers:
1. Numerical Faithfulness: Every numeric figure must be grounded in the input payload or domain constants.
2. Claim-Level Semantic Faithfulness:
   - Device state routing: STALE_DATA and OFFLINE_LONG sites receive connectivity tickets without outage probabilities.
   - Mains state: Claims of mains failure/normal must strictly match physical inputs.
   - Battery state: Devices without battery sensors (SBI, BOB) cannot have invented battery voltage readings or degradation claims.
   - Rule names: Only triggered rules (RULE_*) can be cited; hallucinated rule names trigger immediate rejection.
   - Risk tier and probability wording: Explicit ticket headers and urgency claims must match assigned tiers.
3. Fallback: Immediate replacement with deterministic zero-hallucination ticket templates.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple


class NumericalFaithfulnessValidator:
    """Enforces that every number in the explanation was provided in the input payload."""

    WHITELISTED_CONSTANTS = {1, 2, 3, 4, 5, 6, 12, 15, 24, 48, 60, 90, 100, 360, 3600, 86400}

    def __init__(self, tolerance: float = 0.05):
        self.tolerance = tolerance

    @staticmethod
    def extract_numbers_from_text(text: str) -> List[float]:
        """Extract floating-point and integer numbers from text, ignoring dates/identifiers.
        
        Properly handles numbers followed by units (e.g. 210V, 220V, 50Hz, 4.5%)
        without greedy backtracking truncation.
        """
        pattern = r'(?<![a-zA-Z0-9_\-])([-+]?(?:\d+(?:\.\d+)?|\.\d+))(?:[a-zA-Z%]+)?(?![a-zA-Z0-9_\-])'
        matches = re.finditer(pattern, text)
        nums = []
        for m in matches:
            try:
                nums.append(float(m.group(1)))
            except ValueError:
                continue
        return nums

    @staticmethod
    def collect_allowed_numbers(input_payload: Dict[str, Any]) -> Set[float]:
        """Recursively harvest all numeric values present in the input dictionary."""
        allowed: Set[float] = set()

        def _harvest(obj):
            if isinstance(obj, (int, float)):
                if not (isinstance(obj, float) and math.isnan(obj)):
                    allowed.add(round(float(obj), 2))
                    if 0.0 <= obj <= 1.0:
                        allowed.add(round(float(obj * 100.0), 1))
                        allowed.add(round(float(obj * 100.0), 2))
            elif isinstance(obj, dict):
                for v in obj.values():
                    _harvest(v)
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    _harvest(item)
            elif isinstance(obj, str):
                for num_str in re.findall(r"[-+]?\d*\.?\d+", obj):
                    try:
                        allowed.add(round(float(num_str), 2))
                    except ValueError:
                        pass

        _harvest(input_payload)
        return allowed

    def validate(
        self,
        generated_text: str,
        input_payload: Dict[str, Any],
    ) -> Tuple[bool, List[float]]:
        """Validate whether all numbers in generated text are grounded in input_payload."""
        gen_numbers = self.extract_numbers_from_text(generated_text)
        allowed_numbers = self.collect_allowed_numbers(input_payload)

        unaccounted = []
        for num in gen_numbers:
            if int(num) in self.WHITELISTED_CONSTANTS and abs(num - int(num)) < 1e-4:
                continue

            is_matched = False
            for allowed in allowed_numbers:
                if abs(num - allowed) <= self.tolerance or abs(num - round(allowed, 1)) <= self.tolerance:
                    is_matched = True
                    break
            if not is_matched:
                unaccounted.append(num)

        is_valid = len(unaccounted) == 0
        return is_valid, unaccounted


class ComprehensiveFaithfulnessValidator:
    """Comprehensive validator checking both numerical grounding and semantic claim validity."""

    KNOWN_SYSTEM_RULES = {
        "RULE_RAPID_BATTERY_DISCHARGE",
        "RULE_CRITICAL_BATTERY_LOW",
        "RULE_MAINS_FAILURE_WITH_DEGRADATION",
        "RULE_UNSTABLE_AC_VOLTAGE",
        "RULE_COMMUNICATION_HEARTBEAT_LOSS",
    }

    def __init__(self, tolerance: float = 0.05):
        self.num_validator = NumericalFaithfulnessValidator(tolerance=tolerance)

    def validate(
        self,
        generated_text: str,
        input_payload: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """Validate generated text for numerical and claim-level faithfulness.
        
        Returns:
            (is_valid, violation_reasons)
        """
        violations: List[str] = []

        if not generated_text or len(generated_text.strip()) < 25:
            violations.append("Empty or truncated generation text.")
            return False, violations

        # 1. Numerical Faithfulness Check
        num_ok, ungrounded_nums = self.num_validator.validate(generated_text, input_payload)
        if not num_ok:
            violations.append(f"Ungrounded numerical figures: {ungrounded_nums}")

        device_state = input_payload.get("device_state", "ONLINE_OK")
        is_batt_present = input_payload.get("is_battery_sensor_present", True)
        assigned_tier = str(input_payload.get("risk_tier", "LOW")).upper()
        rules_triggered = set(input_payload.get("rules_triggered", []))
        telem = input_payload.get("telemetry_snapshot", {})
        mains_hours = float(telem.get("time_since_mains_loss_hours", 0.0) or 0.0)
        calibrated_prob = float(input_payload.get("calibrated_prob", 0.0) or 0.0)

        lower_text = generated_text.lower()

        # 2. Device State Routing Check
        if device_state in ("STALE_DATA", "OFFLINE_LONG"):
            # Must be formatted as a connectivity / communication alert
            if not any(k in lower_text for k in ["connectivity", "gateway", "communication", "offline", "stale"]):
                violations.append(f"State routing violation: device_state is {device_state} but output is not a connectivity investigation ticket.")
            # Must NOT claim outage probability
            if re.search(r"\boutage\s+probability\s*:\s*\d+", lower_text):
                violations.append("State routing violation: Stale/Offline site must not display an outage probability.")
            # Must NOT claim urgent power outage in progress
            if "urgent power outage" in lower_text or "power outage in progress" in lower_text:
                violations.append("State routing violation: Stale/Offline site misdiagnosed as urgent physical power outage.")
            # Must recommend investigating gateway / SIM / network / site power
            if not any(k in lower_text for k in ["gateway", "sim", "network", "power at the site", "signal"]):
                violations.append("State routing violation: Missing recommendation to investigate gateway, SIM/network, or site power.")

        elif device_state == "ONLINE_OK":
            # Cannot claim device is offline or communication blackout
            if re.search(r"\b(device\s+offline|gateway\s+offline|communication\s+blackout|connectivity\s+loss)\b", lower_text):
                violations.append("State claim violation: Claimed device offline or connectivity loss when device_state is ONLINE_OK.")

        elif device_state == "ONLINE_POWER_LOSS":
            # Cannot claim mains is normal/healthy
            if re.search(r"\b(mains\s+normal|mains\s+healthy|mains\s+online|grid\s+power\s+normal)\b", lower_text):
                violations.append("State claim violation: Claimed mains is normal when device_state is ONLINE_POWER_LOSS.")

        # 3. Mains State Claim Check
        if mains_hours == 0.0 and device_state == "ONLINE_OK":
            # Exclude standard telemetry header "time since mains loss: 0.0 hours"
            scrubbed_for_mains = re.sub(r"time\s+since\s+mains\s+loss(?:\s+hours)?\s*:\s*0(?:\.0+)?\s*(?:hours|h)?", "", lower_text)
            if re.search(r"\b(mains\s+(failure|loss|disconnected|outage)|grid\s+(failure|loss|down)|utility\s+power\s+(loss|failure))\b", scrubbed_for_mains):
                violations.append("Mains claim violation: Claimed mains failure or loss when mains is currently healthy and active.")
        elif mains_hours > 0.0:
            if re.search(r"\b(mains\s+(normal|healthy|restored|online)|grid\s+normal)\b", lower_text):
                violations.append("Mains claim violation: Claimed mains is normal when active mains loss duration is observed.")

        # 4. Battery Sensor Presence Check
        if not is_batt_present:
            # SBI and BOB sites do not have battery sensors
            # Reject if text claims a specific battery voltage number (e.g. "battery voltage: 12.4 V")
            if re.search(r"\bbattery\s+(?:voltage|bus|level)?\s*(?:is|:)?\s*\d+\.?\d*\s*v\b", lower_text):
                violations.append("Battery sensor violation: Invented battery voltage figure on site without battery hardware.")
            if re.search(r"\b(battery\s+(depleted|critical|discharged|degraded|low))\b", lower_text):
                violations.append("Battery sensor violation: Claimed battery degradation on site without battery hardware.")

        # 5. Rule Name Verification Check
        cited_rules = set(re.findall(r"\bRULE_[A-Z0-9_]+\b", generated_text))
        for r in cited_rules:
            if r not in rules_triggered:
                violations.append(f"Rule hallucination: Cited rule '{r}' was not triggered in verified input payload.")

        # 6. Risk Tier Alignment Check
        # Check explicit tier headers like [SYSTEM DIAGNOSTIC TICKET - HIGH RISK]
        found_tier_headers = re.findall(r"\[SYSTEM DIAGNOSTIC TICKET - ([A-Z]+) RISK\]", generated_text)
        if found_tier_headers:
            header_tier = found_tier_headers[0].upper()
            if header_tier != assigned_tier and assigned_tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                violations.append(f"Risk tier mismatch: Header states '{header_tier}' but assigned tier is '{assigned_tier}'.")

        # Urgency consistency
        if assigned_tier == "LOW" and (
            "extreme danger" in lower_text
            or "urgent emergency outage" in lower_text
            or "imminent blackout" in lower_text
        ):
            violations.append("Urgency wording mismatch: Low-risk scenario described with extreme emergency language.")

        is_valid = len(violations) == 0
        return is_valid, violations


def generate_deterministic_template(input_payload: Dict[str, Any]) -> str:
    """Zero-hallucination deterministic fallback diagnosis and ticket with state-based routing."""
    dname = input_payload.get("device_name", "UNKNOWN_BRANCH")
    bank = input_payload.get("bank", "BANK")
    state = input_payload.get("device_state", "ONLINE_OK")
    tier = input_payload.get("risk_tier", "LOW")
    rules_hit = input_payload.get("rules_triggered", [])
    top_feats = input_payload.get("top_contributing_features", "N/A")
    telem = input_payload.get("telemetry_snapshot", {})
    is_batt_present = input_payload.get("is_battery_sensor_present", True)

    ac_v = telem.get("ac_voltage", "N/A")
    batt_v = telem.get("battery_voltage", "N/A")
    mains_age = telem.get("time_since_mains_loss_hours", 0.0)

    rules_str = ", ".join(rules_hit) if rules_hit else "None"

    # ROUTING LAYER: STALE_DATA / OFFLINE_LONG -> Connectivity Investigation Ticket
    if state in ("STALE_DATA", "OFFLINE_LONG") or tier == "CONNECTIVITY_ISSUE":
        ticket = (
            f"[SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]\n"
            f"Branch Site: {dname} ({bank})\n"
            f"Device State: {state} (Telemetry staleness / communication drop)\n"
            f"Assigned Status: CONNECTIVITY_ISSUE\n"
            f"Active Physical Rule Violations: {rules_str}\n\n"
            f"Operational Diagnosis:\n"
            f"Device communication lost or telemetry heartbeat stale. "
            f"Instantaneous electrical sensor readings are unavailable or untrusted. "
            f"Outage probability is NOT evaluated during communication blackout.\n\n"
            f"DISPATCH RECOMMENDATION: Investigate gateway, SIM/network, power at the site. "
            f"Verify device power supply and local router/SIM signal strength before dispatching electrical maintenance."
        )
        return ticket

    # ONLINE STATES: Power Outage Diagnostic Ticket
    prob_pct = round(float(input_payload.get("calibrated_prob", 0.0)) * 100.0, 1)

    if not is_batt_present or batt_v in ("N/A", None) or (isinstance(batt_v, float) and math.isnan(batt_v)):
        batt_display = "Not equipped (No battery voltage sensor present)"
    else:
        batt_display = f"{batt_v} V"

    if tier in ("HIGH", "CRITICAL"):
        action = (
            "DISPATCH RECOMMENDATION: Urgent electrical maintenance required. "
            "Inspect site utility power distribution board, UPS battery bank health, and primary line inverter. "
            "Verify mains contactor and physical breaker positions."
        )
    elif tier == "MEDIUM":
        action = (
            "DISPATCH RECOMMENDATION: Proactive branch monitoring. "
            "Verify site AC line voltage stability over next 6 hours. "
            "Schedule inverter battery load test during off-peak hours if gap persists."
        )
    else:
        action = (
            "DISPATCH RECOMMENDATION: Normal branch operations. "
            "No field technician dispatch required. Continue automated telemetry polling."
        )

    ticket = (
        f"[SYSTEM DIAGNOSTIC TICKET - {tier} RISK]\n"
        f"Branch Site: {dname} ({bank})\n"
        f"Device State: {state}\n"
        f"24-Hour Outage Probability: {prob_pct}%\n"
        f"Assigned Risk Tier: {tier}\n"
        f"Active Physical Rule Violations: {rules_str}\n"
        f"Key Predictive Drivers: {top_feats}\n\n"
        f"Observed Physical Telemetry Snapshot:\n"
        f"- AC Line Voltage: {ac_v} V\n"
        f"- Battery Voltage: {batt_display}\n"
        f"- Time Since Mains Loss: {mains_age} hours\n\n"
        f"{action}"
    )
    return ticket
