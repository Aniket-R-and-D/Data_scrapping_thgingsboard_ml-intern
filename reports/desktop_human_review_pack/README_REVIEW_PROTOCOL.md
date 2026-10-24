# Desktop Live Shadow Human Review Pack (30 Stratified Cases)

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
