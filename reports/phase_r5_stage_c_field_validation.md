# Phase R5 Stage C Report — Field-Level Validation

**Date:** October 14, 2026
**Scope:** Stage C of the R5 reliability-recovery plan (field-level validation).
**Status:** GATE DELIVERABLE — awaiting approval. R5 is **NOT approved**.
**Provenance:** Built by the same AI agent that performed the Phase R5 work (self-audit
disclosure; not an independent audit).

**Explicit statements:**
- This validator checks *grounding relationships* (right value, right unit, right field,
  right polarity, required presence). **It is not a comprehensive semantic guarantee.**
  Unrestricted-prose validation remains unreliable, which is exactly why the deterministic
  renderer stays the production default and Qwen stays behind a default-OFF flag.
- No semantic correctness is claimed beyond what the tests below measure:
  **0 false acceptances on 10 adversarial falsifications, 0 false rejections on 5
  truthful controls, 10/10 paraphrased valid tickets accepted.**
- **The LLM remains disabled by default. No dispatch, alarm creation, or external
  notification occurred. No model was fine-tuned. No historical evidence or earlier phase
  report was modified in Stage C.**

---

## 1. Policy version and exact rules (`src/tickets/policy.py`, `ticket-policy-v1`)

Single source of truth — schema, renderer, and validator all import from it; no duplicate
hardcoded thresholds exist elsewhere (the Stage B copies in `schema.py`/`renderer.py` were
refactored into imports; `renderer.format_probability` is a re-export of the canonical
function).

| Rule | Exact value |
|---|---|
| Policy version | `ticket-policy-v1` (reported in every ticket and test artifact) |
| Probability display | percent, one decimal (`format_probability`); display must match canonical within **0.05 percentage points** (a `9.9%`-for-`9.8%` drift is rejected) |
| Measurement display tolerance | **0.05** in field units (one-decimal rounding window) |
| Tier bands (from `assign_risk_tier`) | LOW p<0.02; MEDIUM 0.02–0.05; HIGH 0.05–0.15; CRITICAL p≥0.15; `CONNECTIVITY_ISSUE` iff stale/offline; `RULE_AC_SURGE_OVER_250V` forces CRITICAL at any probability (documented exception) |
| Freshness | CURRENT requires observation age ≤ **60 min** (matches the `STALE_DATA` boundary); STALE requires age > 60 min; UNAVAILABLE requires null value. Enforced at schema build **and** checked on ticket text |
| Allowed units | voltages → `V`; mains-loss duration → `hours`/`h`; heartbeat gap → `seconds`/`s` |
| Canonical rule IDs + evidence | `RULE_AC_SURGE_OVER_250V`/`RULE_AC_UNDER_50V` → needs `ac_voltage` present; `RULE_MAINS_LOSS_RECENT` → needs `time_since_mains_loss` present |
| Hardware claims | no breaker/inverter/IGBT/fiber/transformer/UPS evidence field exists in the payload, so such failure claims are rejected outside labeled POSSIBLE CAUSE hypotheses |
| Urgency | LOW forbids emergency/extreme-danger/imminent-blackout/mobilize/immediate-dispatch/"right now"; MEDIUM forbids extreme-danger/imminent-blackout; CONNECTIVITY_ISSUE forbids urgent-power-outage/in-progress language |
| Required sections/fields | `OBSERVED`, `INVESTIGATION`, `Ticket ID:`, `Branch Site:`, exact device ID, `Input Payload SHA-256:` (**equality-checked** against the canonical hash), no-dispatch footer |

## 2. Validator architecture (`src/tickets/field_validator.py`)

`validate_candidate(candidate_text, ticket_input)` returns `(is_valid, violations)` after
eight relationship checks: (1) required sections/fields incl. hash equality; (2) exact
device identity; (3) per-measurement field–value–unit binding with freshness labeling
(stale must say last-known, never real-time; missing sensor needs canonical wording, never
missing-hardware phrasing); (4) probability conversion + tier header + tier band;
(5) urgency-vs-tier; (6) rule citation ⊆ triggered, triggered listed, evidence present;
(7) **negation-aware** mains/offline claim polarity (a negation word within 4 tokens flips
polarity; telemetry-label lines scrubbed before scanning — this fixes the legacy false
rejection of truthful negations); (8) unsupported hardware-failure claims outside labeled
hypotheses (section-aware split exempts POSSIBLE CAUSE speculation from the hardware check,
but polarity and grounding checks still apply there).

## 3. Full adversarial matrix (`tests/test_field_level_validator.py`)

| Case | Kind | Expected | Actual | Failure reason if wrong |
|---|---|---|---|---|
| FA-01 swapped AC/battery values | FA | reject | **reject** | field-value binding |
| FA-02 wrong units (A, kW) | FA | reject | **reject** | unit mismatch per field |
| FA-03 `0.045%` for 0.045 (4.5%) | FA | reject | **reject** | probability scale error |
| FA-04 negated mains loss during active loss | FA | reject | **reject** | denial during observed loss |
| FA-05 breaker/inverter diagnosis, no evidence | FA | reject | **reject** | unsupported hardware claim |
| FA-06 missing sensor as "no battery installed" | FA | reject | **reject** | sensor≠hardware |
| FA-07 stale value as "real-time right now" | FA | reject | **reject** | stale-as-current |
| FA-08 wrong device name/bank | FA | reject | **reject** | identity mismatch |
| FA-09 emergency language on LOW | FA | reject | **reject** | urgency-vs-tier |
| FA-10 missing OBSERVED/INVESTIGATION/hash/footer | FA | reject | **reject** | required sections |
| FR-01 "no mains loss, not offline" on healthy site | FR | accept | **accept** | negation-aware polarity |
| FR-02 "AC voltage = 210V" for 210.0 V | FR | accept | **accept** | attached-unit binding |
| FR-03 "228.5 V" for payload 228.53 | FR | accept | **accept** | rounding tolerance |
| FR-04 canonical unknown-battery wording | FR | accept | **accept** | missing-sensor wording |
| FR-05 valid connectivity ticket, no probability | FR | accept | **accept** | connectivity path |

**False acceptance count: 0 / 10. False rejection count: 0 / 5** (reported separately, as
required — a single accuracy number would hide the asymmetry).

Example violation strings:
- FA-02 → `"Field-value-unit mismatch for ac_voltage: expected value 228.5 with unit ('V',) on its measurement line."` (+ battery twin)
- FA-05 → `"Unsupported hardware-failure claim: no breaker, inverter, fiber, or transformer evidence field exists in the payload."`

## 4. Paraphrase robustness (10 variants, 10/10 accepted)

Reordered sections, reordered header fields, investigate-synonyms (examined/checked/reviewed),
alias labels ("AC line voltage", "Battery bus voltage", "Mains loss duration"),
compact/equals-sign measurement styles, extra benign sentences, reworded hypotheses —
all accepted because checks are order-independent and alias-aware. Required anchors
(section markers, identity strings, tier header, canonical probability, rule list, hash)
are documented constraints, not wording brittleness: during development two draft
paraphrases were *correctly* rejected (one used the forbidden LOW-tier phrase "right
now", one exposed a hyphen/space gap in a label-scrub pattern that was then fixed) —
evidence the validator discriminates wording that matters from wording that does not.

## 5. Test output and commands

```
.venv\Scripts\python.exe -m pytest tests/test_field_level_validator.py -q
27 passed in 0.08s   (exit code 0)   # 15 adversarial + matrix summary + 5 renderer-consistency + 10 paraphrases

Full suite (tickets + R-series regression):
.venv\Scripts\python.exe -m pytest tests/test_tickets_schema.py tests/test_tickets_renderer.py
  tests/test_field_level_validator.py tests/test_phase_r1_state_classification.py
  tests/test_phase_r2_label_leakage.py tests/test_phase_r3_predictor.py
  tests/test_phase_r4_consistency.py tests/test_phase_r5_claims.py -q
105 passed in 2.53s   (exit code 0)

python verify_r5_evidence.py → exit code 0 (Stage A evidence unchanged)
```

## 6. Known remaining limitations (not a comprehensive semantic guarantee)

1. Claim-polarity checks cover mains/offline/battery/hardware patterns only; novel
   free-prose falsehoods outside these patterns are not detected — unrestricted prose
   validation is unreliable, so production output stays restricted to the deterministic
   renderer (per the task's fallback rule, no claim of comprehensive regex coverage is made).
2. Negation handling is a 4-token window heuristic; complex or cross-sentence negation may
   misfire in either direction.
3. Measurement binding requires the value and unit on one measurement line; values split
   across sentences are not bound.
4. Percentage check exempts no structural constants; any second percentage (even benign)
   is currently rejected — conservative by design, may need an allowlist with justification.
5. Paraphrase anchors (section markers, exact identity strings, canonical probability) are
   mandatory by policy; free-form tickets without them cannot validate.

---

*Stage C tuned no model, enabled nothing, dispatched nothing, and modified no historical
evidence. Field-level validation measures grounding relationships on typed inputs — nothing
more is claimed.*
