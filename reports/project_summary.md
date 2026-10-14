# Project Summary — Bank Branch Telemetry Outage Warning & Diagnostic Tickets

## Problem statement

135 bank branch IoT gateways report power telemetry through ThingsBoard. Operations staff
needed (1) early warning of 24-hour utility power outages and (2) accurate, non-misleading
diagnostic tickets distinguishing real electrical failures from gateway communication
blackouts — without ever auto-dispatching technicians or creating alarms.

## Architecture

15-minute telemetry → 4-state device classification (`ONLINE_OK`, `ONLINE_POWER_LOSS`,
`STALE_DATA`, `OFFLINE_LONG`) → leakage-free LightGBM predictor with Platt calibration →
tier assignment and physical rules → typed `TicketInput` validation (`ticket-policy-v1`) →
deterministic renderer (OBSERVED / UNKNOWN / POSSIBLE CAUSE / INVESTIGATION) → Stage C
field validator. A fine-tuned Qwen-0.5B model exists behind a default-OFF experimental flag
for commentary only. Full diagram and commands: `README.md`.

## What was built

- Leakage-free prediction pipeline (heartbeat gap excluded from label and features),
  calibrated probabilities, and tier assignment with purge-buffered splits.
- Deterministic typed ticket system: schema, renderer, field validator, LLM boundary
  bridge, and fail-closed startup check — **152 tests**, all passing.
- Clean evaluation infrastructure: 816 grouped incidents, three frozen splits (212 / 407 /
  150 records) with zero cross-split payload overlap, and a 30-case blinded human-review
  pack with blank forms.

## What was measured

- Predictor (untouched split, 29 events, 250 bootstraps): precision **20.26%**, recall
  **79.68%**, incident recall **89.66% (26/29)**, mean lead time **22.6 h**.
- Tickets: **100%** deterministic render success and **100%** Stage C validator acceptance
  on all clean splits; validator suite **0/10** false acceptances, **0/5** false rejections.
- LLM comparison (557 paired records): Qwen added **0** supported facts or steps beyond the
  renderer while introducing unsupported claims on 45 records and running ~3,200× slower —
  hence **the deterministic renderer is the production default and the LLM remains
  experimental and disabled by default**.
- Alert volume: **0.2029** power and **1.0415** connectivity episodes per device per week.

## What was corrected during the reliability audit

The prior R5 reports contained a fabricated "double-blind human audit" (scores were
hardcoded heuristics), a contaminated "held-out" benchmark (50/100 inputs duplicated from
training), an unsupported "~90–95% semantic accuracy" estimate, and overbroad "100% safe"
claims. All were retracted with visible markings, a correction notice, a 16-entry
claim–evidence ledger, and a one-command reproduction script — while preserving every
checkpoint and historical report. **Independent human review is pending.**

## Final operational recommendation

Run the deterministic renderer as the sole ticket path with the R6 release configuration
(`EXPERIMENTAL_LLM_ENABLED=false`, read-only telemetry, dispatch/alarms/notifications
disabled, startup check passing). Keep Qwen experimental until a non-tabular need, ≥99.5%
commentary validation, and completed blinded human review jointly justify it — and do not
retrain on frozen evaluation splits.

## Honest limitations

Instantaneous breaker trips are unpredictable; blackouts hide electrical state; SBI/BOB
sites lack battery sensors; silent gateways create connectivity/power ambiguity; new sites
fall back to fleet priors; and no human has yet reviewed any ticket, so independent
semantic accuracy is **NOT MEASURED**.

## What you would do next with more labeled incidents or reviewer time

1. Collect the 30-case blinded human review (10 future-known, 10 unseen, 10 difficult)
   and unblind preference totals before any LLM enablement discussion.
2. Grow the independent electrical-outage event catalog beyond 29 test events to tighten
   precision/recall confidence intervals, especially for SBI/BOB fleets.
3. Add per-device adaptive tier thresholds and evaluate lead-time utility with NOC staff
   in a read-only live shadow before any production promotion.
