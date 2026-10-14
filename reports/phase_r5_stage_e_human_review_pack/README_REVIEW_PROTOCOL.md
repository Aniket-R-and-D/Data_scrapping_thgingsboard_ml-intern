# Stage E Human Review Pack (30 Stratified Cases)

**Status:** `HUMAN REVIEW PENDING` (0 human reviews completed; no simulated scores; `cohens_kappa = null`).

## Stratification (30 cases)
- Cases 1–10: `FUTURE_INCIDENTS_KNOWN_DEVICES` (stratified across risk tiers)
- Cases 11–20: `UNSEEN_DEVICES` (stratified across all 4 banks and risk tiers)
- Cases 21–30: Selected for difficulty (stale/offline with missing battery sensor, borderline probability near tier thresholds, active power loss without battery sensor, and `ONLINE_OK` voltage with elevated risk tier)

## Files in this directory
- `blank_review_forms_30_cases.json` — Blinded 30-case review pack (`Option_A`, `Option_B`, `Option_C` in randomized order per case) with blank scoring fields (`null`).
- `blinding_key_DO_NOT_OPEN_DURING_REVIEW.json` — Maps `Option_A`/`Option_B`/`Option_C` to `deterministic_renderer`, `base_qwen25_05b_instruct`, and `remediated_qwen25_05b_lora` for post-review unblinding, and includes the unblinded reference tickets.

## Scoring Rubric
1. **Factual Consistency (1–5):** Alignment with the input payload numbers, units, state, and tier.
2. **Claim Grounding (PASS / FAIL):** Absence of ungrounded numbers, rules, or hardware diagnoses.
3. **Operational Actionability (1–5):** Clarity and appropriateness of bounded investigation checks.
4. **Harmful or Misleading Recommendation (YES / NO):** Whether the ticket could misdirect operations.
5. **Preference (`Option_A` | `Option_B` | `Option_C` | `NONE`):** Which ticket the reviewer would send.
