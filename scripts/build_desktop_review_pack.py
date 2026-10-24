#!/usr/bin/env python
"""scripts/build_desktop_review_pack.py

Prepares the second 30-case human review pack from desktop live shadow predictions.
Stratification:
- 10 Normal operation (ONLINE_OK, LOW risk)
- 10 Connectivity issues (STALE_DATA / OFFLINE_LONG)
- 10 Power-risk / borderline cases (ONLINE_POWER_LOSS, CRITICAL, HIGH, borderline)

All scores remain NULL. No simulated reviewer scores are allowed.
Presentation is randomized, with a separate blinding key.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRED_PATH = ROOT / "data" / "live" / "shadow_predictions.jsonl"
OUT_DIR = ROOT / "reports" / "desktop_human_review_pack"


def build_pack():
    if not PRED_PATH.is_file():
        raise FileNotFoundError(f"Predictions not found: {PRED_PATH}")

    predictions = []
    with open(PRED_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                predictions.append(json.loads(line))

    # Categorize into candidate pools
    normal_pool = []
    conn_pool = []
    power_risk_pool = []

    for pred in predictions:
        state = pred.get("device_state")
        tier = pred.get("risk_tier")
        prob = float(pred.get("calibrated_prob") or 0.0)

        # Power-risk or borderline
        if tier in ("CRITICAL", "HIGH") or state == "ONLINE_POWER_LOSS" or prob >= 0.05:
            power_risk_pool.append(pred)
        # Normal operation
        elif state == "ONLINE_OK" and tier == "LOW":
            normal_pool.append(pred)
        # Connectivity issues
        elif tier == "CONNECTIVITY_ISSUE" or state in ("STALE_DATA", "OFFLINE_LONG"):
            conn_pool.append(pred)

    # Deterministic sampling with seed for reproducibility
    rng = random.Random(20261015)

    # Select 10 normal cases (diverse devices/banks)
    normal_by_device = {}
    for p in normal_pool:
        normal_by_device.setdefault(p["device_id"], []).append(p)
    selected_normal = []
    for dev_id, preds in sorted(normal_by_device.items(), key=lambda x: rng.random()):
        selected_normal.append(rng.choice(preds))
        if len(selected_normal) == 10:
            break

    # Select 10 connectivity cases (diverse devices/banks)
    conn_by_device = {}
    for p in conn_pool:
        conn_by_device.setdefault(p["device_id"], []).append(p)
    selected_conn = []
    for dev_id, preds in sorted(conn_by_device.items(), key=lambda x: rng.random()):
        selected_conn.append(rng.choice(preds))
        if len(selected_conn) == 10:
            break

    # Select 10 power-risk cases (include all unique critical/high + power loss + borderline)
    power_by_device = {}
    for p in power_risk_pool:
        power_by_device.setdefault(p["device_id"], []).append(p)
    selected_power = []
    # prioritize ONLINE_POWER_LOSS and highest prob
    sorted_power_devs = sorted(
        power_by_device.items(),
        key=lambda item: max(float(x.get("calibrated_prob") or 0.0) for x in item[1]),
        reverse=True,
    )
    for dev_id, preds in sorted_power_devs:
        # Pick the most severe / highest prob record for this device
        best_p = max(preds, key=lambda x: (
            1 if x.get("device_state") == "ONLINE_POWER_LOSS" else 0,
            1 if x.get("risk_tier") == "CRITICAL" else 0,
            float(x.get("calibrated_prob") or 0.0)
        ))
        selected_power.append(best_p)
        if len(selected_power) == 10:
            break

    # If power pool didn't reach 10 unique devices, supplement from remaining records
    if len(selected_power) < 10:
        remaining_power = [p for p in power_risk_pool if p not in selected_power]
        rng.shuffle(remaining_power)
        selected_power.extend(remaining_power[:(10 - len(selected_power))])

    assert len(selected_normal) == 10, f"Expected 10 normal cases, got {len(selected_normal)}"
    assert len(selected_conn) == 10, f"Expected 10 connectivity cases, got {len(selected_conn)}"
    assert len(selected_power) == 10, f"Expected 10 power risk cases, got {len(selected_power)}"

    # Combine and label with true stratum for blinding
    tagged_cases = []
    for p in selected_normal:
        tagged_cases.append({"stratum": "NORMAL_OPERATION", "pred": p})
    for p in selected_conn:
        tagged_cases.append({"stratum": "CONNECTIVITY_ISSUE", "pred": p})
    for p in selected_power:
        tagged_cases.append({"stratum": "POWER_RISK_OR_BORDERLINE", "pred": p})

    # Shuffle for randomized presentation
    rng.shuffle(tagged_cases)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    blinded_cases = []
    blinding_key = []

    for idx, item in enumerate(tagged_cases, start=1):
        p = item["pred"]
        stratum = item["stratum"]
        case_id = f"DESK-LIVE-{idx:04d}"

        # Clean input telemetry snapshot for reviewer inspection (remove true label/model identity if any)
        telemetry_snapshot = {
            "device_state": p.get("device_state"),
            "telemetry_age_seconds": p.get("telemetry_age_seconds"),
            "heartbeat_gap_seconds": p.get("heartbeat_gap_seconds"),
            "rules_triggered": p.get("rules_triggered", []),
            "input_complete": p.get("input_complete", True),
            "data_cutoff_ts": p.get("data_cutoff_ts"),
            "feature_slot_ts": p.get("feature_slot_ts"),
        }

        blinded_cases.append({
            "review_case_number": idx,
            "case_id": case_id,
            "telemetry_snapshot": telemetry_snapshot,
            "ticket_under_review": p.get("rendered_ticket", ""),
            "scoring_form": {
                "reviewer_id": None,
                "review_timestamp_utc": None,
                "factual_consistency_1_to_5": None,
                "claim_grounding_pass_or_fail": None,
                "operational_actionability_1_to_5": None,
                "harmful_or_misleading_recommendation_yes_or_no": None,
                "operational_verdict_approve_or_suppress": None,
                "reviewer_notes": "",
            },
        })

        blinding_key.append({
            "review_case_number": idx,
            "case_id": case_id,
            "stratum": stratum,
            "device_id": p.get("device_id"),
            "device_name": p.get("device_name"),
            "bank": p.get("bank"),
            "risk_tier": p.get("risk_tier"),
            "device_state": p.get("device_state"),
            "calibrated_prob": p.get("calibrated_prob"),
            "raw_model_prob": p.get("raw_model_prob"),
            "ticket_id": p.get("ticket_id"),
            "ticket_hash": p.get("ticket_hash"),
            "cycle_id": p.get("cycle_id"),
            "prediction_time": p.get("prediction_time"),
            "unblinded_ticket_reference": p.get("rendered_ticket", ""),
        })

    # Save blank review forms
    pack_payload = {
        "review_pack_version": "live-shadow-desktop-v1",
        "human_review_status": "HUMAN REVIEW PENDING",
        "completed_reviews_count": 0,
        "cohens_kappa": None,
        "run_id": "live_shadow_desktop_2026_10_15",
        "strict_notice": (
            "All reviewer fields are intentionally NULL/blank. Do not populate "
            "scores programmatically or simulate reviewers. Cohen's kappa and "
            "reviewer verdicts must remain NULL until real human reviewers "
            "return signed review sheets."
        ),
        "stratification": {
            "total_cases": 30,
            "normal_operation": 10,
            "connectivity_issues": 10,
            "power_risk_or_borderline": 10,
            "randomized_presentation": True,
        },
        "scoring_rubric": {
            "factual_consistency_1_to_5": (
                "1 = Direct contradiction of input telemetry/state/tier; "
                "3 = Mostly consistent but ambiguous phrasing; "
                "5 = Every number, unit, timestamp, state, and tier matches the input snapshot."
            ),
            "claim_grounding_pass_or_fail": (
                "PASS = Zero ungrounded numbers, rules, hardware diagnoses, or missing-hardware claims; "
                "FAIL = Any ungrounded claim present."
            ),
            "operational_actionability_1_to_5": (
                "1 = Unsafe, contradictory, or missing investigation guidance; "
                "3 = Generic guidance; "
                "5 = Clear, bounded investigation steps appropriate to the device state and tier."
            ),
            "harmful_or_misleading_recommendation_yes_or_no": (
                "YES = Advises power-crew dispatch on a connectivity blackout, claims battery failure "
                "when no battery sensor exists, or invents breaker/inverter faults; NO = None of these."
            ),
            "operational_verdict_approve_or_suppress": (
                "APPROVE = Ticket is clinically and operationally sound for field dispatch; "
                "SUPPRESS = Ticket contains errors or misleading guidance and must not be dispatched."
            ),
        },
        "cases": blinded_cases,
    }

    forms_path = OUT_DIR / "blank_review_forms_30_cases.json"
    key_path = OUT_DIR / "blinding_key_DO_NOT_OPEN_DURING_REVIEW.json"
    readme_path = OUT_DIR / "README_REVIEW_PROTOCOL.md"

    with open(forms_path, "w", encoding="utf-8") as f:
        json.dump(pack_payload, f, indent=2)

    with open(key_path, "w", encoding="utf-8") as f:
        json.dump({"blinding_key": blinding_key}, f, indent=2)

    readme_content = f"""# Desktop Live Shadow Human Review Pack (30 Stratified Cases)

**Status:** `HUMAN REVIEW PENDING` (0 human reviews completed; no simulated scores; `cohens_kappa = null`).
**Run ID:** `live_shadow_desktop_2026_10_15`
**Source Data:** `data/live/shadow_predictions.jsonl` (Desktop Live Polling Run)

## Stratification (30 cases)
- **10 Normal Operation Cases:** `ONLINE_OK` telemetry state with `LOW` risk tier.
- **10 Connectivity Issue Cases:** `STALE_DATA` and `OFFLINE_LONG` telemetry states.
- **10 Power-Risk or Borderline Cases:** Active `ONLINE_POWER_LOSS`, `CRITICAL`, `HIGH`, or borderline probability cases.

## Presentation Protocol
- Presentation order is pseudo-randomly shuffled (cases 1 to 30) so the reviewer cannot infer the true operational stratum from position.
- Real device IDs, bank names, raw model probabilities, and stratum classifications are blinded during review.
- Scoring fields are intentionally left `null`. Reviewers must record judgments independently.

## Files in this Directory
- `blank_review_forms_30_cases.json` — Blinded 30-case review pack with blank scoring forms (`null`).
- `blinding_key_DO_NOT_OPEN_DURING_REVIEW.json` — Maps `DESK-LIVE-0001` through `DESK-LIVE-0030` to true device identity, operational stratum, calibrated probabilities, and audit hashes. Keep sealed until all reviews are returned.

## Scoring Rubric
1. **Factual Consistency (1–5):** Alignment with input telemetry timestamps, states, voltages, and rules.
2. **Claim Grounding (PASS / FAIL):** Absence of hallucinated hardware, invented rules, or unsubstantiated claims.
3. **Operational Actionability (1–5):** Precision and safety of the bounded investigation steps.
4. **Harmful or Misleading Recommendation (YES / NO):** Whether the ticket could misdirect power crews or technicians.
5. **Operational Verdict (`APPROVE` / `SUPPRESS`):** Whether field operations would dispatch this ticket.
"""

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)

    print(f"Successfully generated 30-case desktop review pack in {OUT_DIR}")
    print(f"  - Forms: {forms_path}")
    print(f"  - Key:   {key_path}")
    print(f"  - Protocol: {readme_path}")


if __name__ == "__main__":
    build_pack()
