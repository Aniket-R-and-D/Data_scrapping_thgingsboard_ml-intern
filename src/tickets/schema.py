"""src/tickets/schema.py — Stage B/C typed input contract for deterministic tickets.

Standard-library only (dataclasses): no new dependencies introduced.
All policy rules are imported from src/tickets/policy.py (single source of
truth); this module defines no thresholds of its own.

Rejected inputs raise TicketValidationError and are never rendered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from src.tickets.policy import (
    ALLOWED_INVESTIGATION_ACTIONS,
    DEVICE_STATES,
    FRESHNESS_CURRENT_MAX_AGE,
    FRESHNESS_VALUES,
    KNOWN_RULE_IDS,
    MEASUREMENT_FIELDS,
    POLICY_VERSION,
    REQUIRED_MEASUREMENT_FIELDS,
    RISK_TIERS,
    STALE_STATES,
    SURGE_RULE_FORCES_CRITICAL,
    tier_band_for_probability,
)

# Re-exported so existing imports keep working.
__all__ = [
    "ALLOWED_INVESTIGATION_ACTIONS",
    "KNOWN_RULE_IDS",
    "POLICY_VERSION",
    "Measurement",
    "TicketInput",
    "TicketValidationError",
    "utc_now",
]


class TicketValidationError(ValueError):
    """Raised when a ticket input violates the schema. Never rendered."""


@dataclass(frozen=True)
class Measurement:
    """One telemetry measurement with value, unit, timestamp, and freshness."""

    field: str
    value: Optional[float]
    unit: Optional[str]
    observed_at: Optional[datetime]
    freshness: str

    def __post_init__(self) -> None:
        if self.field not in MEASUREMENT_FIELDS:
            raise TicketValidationError(
                f"Unknown measurement field {self.field!r}. "
                f"Known fields: {sorted(MEASUREMENT_FIELDS)}."
            )
        if self.freshness not in FRESHNESS_VALUES:
            raise TicketValidationError(
                f"Invalid freshness {self.freshness!r} for {self.field}. "
                f"Must be one of {FRESHNESS_VALUES}."
            )
        expected_units = MEASUREMENT_FIELDS[self.field]["units"]
        if self.freshness == "UNAVAILABLE":
            if self.value is not None:
                raise TicketValidationError(
                    f"{self.field}: freshness UNAVAILABLE requires value=None."
                )
        else:
            if self.value is None:
                raise TicketValidationError(
                    f"{self.field}: freshness {self.freshness} requires a value."
                )
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise TicketValidationError(
                    f"{self.field}: value must be a number, got {self.value!r}."
                )
            if isinstance(self.value, float) and (math.isnan(self.value) or math.isinf(self.value)):
                raise TicketValidationError(
                    f"{self.field}: value must be finite, got {self.value!r}."
                )
            if self.unit not in expected_units:
                raise TicketValidationError(
                    f"{self.field}: wrong unit {self.unit!r}. "
                    f"Expected one of {expected_units}."
                )
            if self.observed_at is None:
                raise TicketValidationError(
                    f"{self.field}: freshness {self.freshness} requires observed_at."
                )
            if self.observed_at.tzinfo is None:
                raise TicketValidationError(
                    f"{self.field}: observed_at must be timezone-aware (UTC)."
                )


@dataclass
class TicketInput:
    """Validated operational ticket input. Construct via TicketInput.build()."""

    device_id: str
    device_name: str
    bank: str
    prediction_timestamp_utc: datetime
    model_version: str
    policy_version: str
    device_state: str
    measurements: List[Measurement]
    calibrated_probability: Optional[float]
    risk_tier: str
    triggered_rules: List[str]
    observed_facts: List[str]
    possible_causes: List[str]
    approved_investigation_actions: List[str]

    @staticmethod
    def build(
        device_id: str,
        device_name: str,
        bank: str,
        prediction_timestamp_utc: datetime,
        model_version: str,
        policy_version: str,
        device_state: str,
        measurements: List[Measurement],
        calibrated_probability: Optional[float],
        risk_tier: str,
        triggered_rules: List[str],
        observed_facts: List[str],
        possible_causes: List[str],
        approved_investigation_actions: List[str],
    ) -> "TicketInput":
        errors: List[str] = []

        if not device_id or not str(device_id).strip():
            errors.append("device_id must be a non-empty string.")
        if not device_name or not str(device_name).strip():
            errors.append("device_name must be a non-empty string.")
        if not bank or not str(bank).strip():
            errors.append("bank must be a non-empty string.")
        if not isinstance(prediction_timestamp_utc, datetime) or prediction_timestamp_utc.tzinfo is None:
            errors.append("prediction_timestamp_utc must be a timezone-aware datetime (UTC).")
        if not model_version or not str(model_version).strip():
            errors.append("model_version must be a non-empty string.")
        if policy_version != POLICY_VERSION:
            errors.append(
                f"policy_version must be exactly {POLICY_VERSION!r}, got {policy_version!r}."
            )
        if device_state not in DEVICE_STATES:
            errors.append(f"device_state must be one of {DEVICE_STATES}, got {device_state!r}.")
        if risk_tier not in RISK_TIERS:
            errors.append(f"risk_tier must be one of {RISK_TIERS}, got {risk_tier!r}.")

        is_connectivity = device_state in STALE_STATES

        # Tier/state consistency: CONNECTIVITY_ISSUE exactly for stale/offline states.
        if is_connectivity and risk_tier != "CONNECTIVITY_ISSUE":
            errors.append(
                f"device_state {device_state} requires risk_tier "
                f"'CONNECTIVITY_ISSUE', got {risk_tier!r}."
            )
        if not is_connectivity and risk_tier == "CONNECTIVITY_ISSUE":
            errors.append(
                f"risk_tier 'CONNECTIVITY_ISSUE' requires device_state in "
                f"{STALE_STATES}, got {device_state!r}."
            )

        # Probability discipline: null on connectivity tickets; float in [0,1] otherwise.
        if is_connectivity:
            if calibrated_probability is not None:
                errors.append(
                    "Connectivity tickets must not carry a power-outage probability: "
                    "calibrated_probability must be null for STALE_DATA/OFFLINE_LONG."
                )
        else:
            if calibrated_probability is None:
                errors.append(
                    f"calibrated_probability is required for {device_state} tickets "
                    "(null means unavailable, which online tickets must not claim)."
                )
            elif (
                isinstance(calibrated_probability, bool)
                or not isinstance(calibrated_probability, (int, float))
                or (isinstance(calibrated_probability, float) and math.isnan(calibrated_probability))
                or not (0.0 <= float(calibrated_probability) <= 1.0)
            ):
                errors.append(
                    "calibrated_probability must be a float in [0, 1], "
                    f"got {calibrated_probability!r}."
                )

        # Tier/probability band consistency (Stage C): the assigned tier must be
        # the canonical band for the probability, except the documented surge
        # exception (RULE_AC_SURGE_OVER_250V forces CRITICAL at any probability).
        if (
            not is_connectivity
            and calibrated_probability is not None
            and isinstance(calibrated_probability, (int, float))
            and not isinstance(calibrated_probability, bool)
            and 0.0 <= float(calibrated_probability) <= 1.0
            and risk_tier in RISK_TIERS
            and risk_tier != "CONNECTIVITY_ISSUE"
        ):
            expected_band = tier_band_for_probability(float(calibrated_probability))
            surge_exception = (
                risk_tier == "CRITICAL"
                and SURGE_RULE_FORCES_CRITICAL in (triggered_rules or [])
            )
            if risk_tier != expected_band and not surge_exception:
                errors.append(
                    f"risk_tier {risk_tier!r} is inconsistent with calibrated_probability "
                    f"{calibrated_probability} (canonical band: {expected_band})."
                )

        unknown_rules = [r for r in triggered_rules if r not in KNOWN_RULE_IDS]
        if unknown_rules:
            errors.append(
                f"Unknown rule IDs rejected: {unknown_rules}. "
                f"Known rule IDs: {sorted(KNOWN_RULE_IDS)}."
            )

        present_fields = [m.field for m in measurements]
        for required in REQUIRED_MEASUREMENT_FIELDS:
            if required not in present_fields:
                errors.append(f"Missing required measurement: {required!r}.")
        if len(set(present_fields)) != len(present_fields):
            errors.append(f"Duplicate measurement fields: {present_fields}.")

        # Freshness labeling vs. observed_at age (Stage C, policy threshold).
        if isinstance(prediction_timestamp_utc, datetime) and prediction_timestamp_utc.tzinfo is not None:
            for m in measurements:
                if m.freshness == "UNAVAILABLE" or m.observed_at is None:
                    continue
                age = prediction_timestamp_utc - m.observed_at
                if age.total_seconds() < 0:
                    errors.append(
                        f"{m.field}: observed_at is after prediction_timestamp_utc."
                    )
                elif m.freshness == "CURRENT" and age > FRESHNESS_CURRENT_MAX_AGE:
                    errors.append(
                        f"{m.field}: labeled CURRENT but observed "
                        f"{age} before prediction (policy: CURRENT requires age <= "
                        f"{FRESHNESS_CURRENT_MAX_AGE})."
                    )
                elif m.freshness == "STALE" and age <= FRESHNESS_CURRENT_MAX_AGE:
                    errors.append(
                        f"{m.field}: labeled STALE but observed only "
                        f"{age} before prediction (policy: STALE requires age > "
                        f"{FRESHNESS_CURRENT_MAX_AGE})."
                    )

        if not observed_facts or not all(isinstance(f, str) and f.strip() for f in observed_facts):
            errors.append("observed_facts must be a non-empty list of non-empty strings.")
        if not isinstance(possible_causes, list) or not all(
            isinstance(c, str) and c.strip() for c in possible_causes
        ):
            errors.append("possible_causes must be a list of non-empty strings (may be empty list).")
        if not approved_investigation_actions or not all(
            isinstance(a, str) and a.strip() for a in approved_investigation_actions
        ):
            errors.append(
                "approved_investigation_actions must be a non-empty list of non-empty strings."
            )
        else:
            disallowed = [
                a for a in approved_investigation_actions if a not in ALLOWED_INVESTIGATION_ACTIONS
            ]
            if disallowed:
                errors.append(
                    f"Investigation actions outside the bounded vocabulary rejected: "
                    f"{disallowed}. Allowed: {sorted(ALLOWED_INVESTIGATION_ACTIONS)}."
                )

        if errors:
            raise TicketValidationError("; ".join(errors))

        return TicketInput(
            device_id=str(device_id),
            device_name=str(device_name),
            bank=str(bank),
            prediction_timestamp_utc=prediction_timestamp_utc,
            model_version=str(model_version),
            policy_version=policy_version,
            device_state=device_state,
            measurements=list(measurements),
            calibrated_probability=(
                None if calibrated_probability is None else float(calibrated_probability)
            ),
            risk_tier=risk_tier,
            triggered_rules=list(triggered_rules),
            observed_facts=list(observed_facts),
            possible_causes=list(possible_causes),
            approved_investigation_actions=list(approved_investigation_actions),
        )

    def measurement(self, field_name: str) -> Measurement:
        for m in self.measurements:
            if m.field == field_name:
                return m
        raise TicketValidationError(f"Measurement {field_name!r} not present.")


def utc_now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
