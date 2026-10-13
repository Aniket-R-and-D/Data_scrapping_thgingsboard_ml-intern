# Phase R0: Project Remediation Plan & Architecture Review

**Project:** ThingsBoard Telemetry Anomaly Detection & 24-Hour Outage Prediction  
**Fleet:** 135 Commercial Bank Branch Sites (BOI, SBI, BOB, Canara)  
**Pass:** Senior ML Engineer Remediation Pass (Phases R0 through R6)  
**Date:** October 13, 2026  
**Status:** PROPOSED — GATE STOPPED (Awaiting Approval)  

---

## 1. Context & Independent Review Findings

An independent technical audit of Phases 0 through 6 identified fundamental data-engineering, causal-modeling, and evaluation flaws that invalidate the unadjusted claims of the earlier pipeline:

1. **Issue A: Direct Label Leakage (Target-Feature Confounding):**
   - In `configs/config.yaml`, the outage target definition included `heartbeat_gap_s >= 900` sustained for $\ge 15$ minutes.
   - Simultaneously, `heartbeat_gap_seconds` was fed into the LightGBM model as a core feature and emerged as the top SHAP contributor by a wide margin.
   - The model was essentially predicting the presence of a heartbeat gap from a current heartbeat gap, creating artificial performance inflation rather than forecasting physical grid failure.

2. **Issue B: Data Quality & Missingness Confounded with Power Loss:**
   - In raw telemetry, 50.4% of recorded battery voltage values are literal `0.0 V` sentinels (indicating unmonitored or disconnected sensor channels), while 18 of 135 devices have no battery monitoring at all.
   - In `src/features/build_features.py`, missing values for AC and battery were forward-filled up to 2 hours and subsequently filled with `0.0 V`. Missing heartbeat was filled with the sentinel `86400.0 s`.
   - Consequently, when devices were simply offline, de-registered, or unequipped with battery sensors, the pipeline evaluated `ac < 50.0 V` and `battery < 10.8 V` as `True`, labeling prolonged offline periods as continuous physical electrical outages.

3. **Issue C: Phase 5 Shadow Mode Inconsistencies & Replay Masking:**
   - The reported tier distribution table claimed 0 outages in `LOW` and `MEDIUM` tiers, while the confusion matrix reported 432 false negatives (FN).
   - The tier volume (HIGH = 79,448 of 90,855 cycles, ~87.4%) contradicted the confusion matrix where predicted negatives were ~78,548.
   - The simulation ran directly after Phase 4 training and replayed the historical test period, making it a backtest rather than true shadow validation.

4. **Issue D: Explanation Layer Overstatement & Semantic Hallucination:**
   - The Phase 6 validator checked numbers only, ignoring categorical claims (e.g., claiming "mains healthy" during a blackout or diagnosing stale devices with "urgent power outage").
   - The headline "100% delivered faithfulness" was achieved via the deterministic template fallback; the raw LLM pass rate was only 28.0%.
   - The human audit methodology was informal and lacked blinded scoring or inter-rater reliability protocols.

---

## 2. Legacy Preservation & Baseline Archival

Prior to executing any code modifications, the entire legacy pipeline state was preserved in:
`archive/legacy_v1_run/`
- `archive/legacy_v1_run/reports/`: All 14 markdown and JSON metrics reports.
- `archive/legacy_v1_run/models/`: Serialized LightGBM, Isolation Forest, Autoencoder, and Platt scalers.
- `archive/legacy_v1_run/data_processed/`: Processed feature matrices and event tables.

This ensures all subsequent remediation comparisons are directly auditable against the original baseline.

---

## 3. Remediation Roadmap: Phases R0 through R6

```
Phase R0: Remediation Plan (Gate Stop & Approvals)
   │
   ▼
Phase R1: Data State Classification (ONLINE vs STALE vs OFFLINE)
   │
   ▼
Phase R2: Label Redesign (Decouple Electrical vs Connectivity)
   │
   ▼
Phase R3: Retrain & Re-Evaluate (Leakage-Free LightGBM + Ablation)
   │
   ▼
Phase R4: Tiering & Rigorous Shadow Mode (Unified Logs & Consistency Verification)
   │
   ▼
Phase R5: Improved Explanation Layer (Claim-Level Validator & LoRA Alignment)
   │
   ▼
Phase R6: Documentation & Model Card (Honest Limits & Deprecations)
```

---

### Phase R1: Data State Classification (Fixing Issue B)

#### Objective:
Disentangle true physical sensor readings from gateway offline, sensor unequipped, and stale communication states.

#### Implementation Steps:
1. **Sentinel & Distribution Analysis:**
   - Audit joint distribution of AC voltage, battery voltage, mains state, and heartbeat arrival.
   - Treat battery voltage $= 0.0\text{ V}$ as `NaN` (sensor unequipped / disconnected channel), because an operating IoT gateway cannot communicate while powered by a zero-volt battery.
   - Distinguish true AC loss ($220\text{ V} \to 0\text{ V}$ observed while gateway is active) from missing telemetry.
2. **Deterministic Device State Machine (`device_state`):**
   - `ONLINE_OK`: Telemetry fresh ($< 15$ min age); AC nominal ($\ge 180\text{ V}$), battery floating ($\ge 12.0\text{ V}$), or mains indicated `ON`.
   - `ONLINE_POWER_LOSS`: Telemetry fresh ($< 60$ min age); active reporting shows AC $< 50\text{ V}$ or mains `OFF` with battery discharging.
   - `STALE_DATA`: No valid telemetry received for $> 60$ minutes and $\le 24$ hours. Gateway communication stalled.
   - `OFFLINE_LONG`: No communication for $> 24$ hours ($> 86,400\text{ s}$). Gateway fully de-energized or link severed.
3. **Explicit Missingness Encoding:**
   - Eliminate silent zero fills in rolling features. Replace with `NaN` and mask properly.
   - Add explicit causal indicators: `is_battery_sensor_present`, `telemetry_age_minutes`, `device_state`.
4. **Deliverables & Gate:**
   - State distribution table across all 135 sites and 4 banks.
   - Audit report showing what percentage of legacy "outage" labels occurred during `STALE_DATA` / `OFFLINE_LONG` states.
   - Unit tests covering state machine transitions in `tests/test_phase_r1_state_classification.py`.

---

### Phase R2: Label Redesign & Leakage Elimination (Fixing Issue A)

#### Objective:
Strictly isolate electrical power outages from connectivity/heartbeat interruptions, eliminating target-feature leakage.

#### Implementation Steps:
1. **Target Disaggregation:**
   - `y_power_outage_24h` (Primary Predictive Target): Defined **exclusively on electrical evidence**. Mains lost (`statusbox_mains_on == 0` or AC drop $< 50\text{ V}$) sustained for $\ge 15$ minutes AND (battery $< 10.8\text{ V}$ on equipped channels OR verified utility `POWER OFF` alarm). Heartbeat gap is **100% excluded** from this label definition.
   - `y_connectivity_loss_24h` (Secondary Operational Target): Heartbeat silence $\ge 60$ minutes. Treated as a separate network telemetry problem, not conflated with power grid failure.
2. **Feature Sanitation:**
   - Strip raw `heartbeat_gap_seconds` and instantaneous communication silence from the predictive feature set.
   - Disallow any feature directly derived from the label definition at prediction time $t$.
   - Any communication stability features must be strictly historical and lagged (e.g., `heartbeat_gap_count_prev_7d` with a minimum 24-hour lag).
3. **Sample Size & Viability Audit:**
   - Recalculate independent power outage event counts across all 135 branches.
   - If independent electrical events $< 30$, declare insufficient positive sample size honestly and recommend physical rules + anomaly detection only.
4. **Deliverables & Gate:**
   - Formal label specification written into `configs/config.yaml`.
   - Event counts, positive rates, and events per device.
   - Automated mutual-information / shift leakage test in `tests/test_phase_r2_label_leakage.py`.

---

### Phase R3: Retrain and Re-evaluate Outage Predictor

#### Objective:
Retrain the outage predictor on the sanitized feature matrix and report transparent, un-inflated performance metrics.

#### Implementation Steps:
1. **Purged Splits:**
   - Chronological split: Train (earlier), Validation (middle), Test (latest) with strict 24-hour purge buffers.
   - Spatial holdout: 19 whole bank branch sites evaluated separately.
2. **Benchmarking & Leakage Ablation Study:**
   - Evaluate 4 configurations on the untouched test set:
     1. Deterministic Physical Rules Baseline.
     2. Logistic Regression Baseline.
     3. LightGBM (Sanitized, Calibrated).
     4. LightGBM (Ablation with Old Heartbeat Feature included) — explicitly measuring the exact artificial lift caused by leakage.
3. **Comprehensive Metrics & Confidence Intervals:**
   - Report: Positives count, Confusion Matrix, Precision, Recall, F1, PR-AUC, PR-AUC / Prevalence (lift over random), Calibration Curve (Brier score), Mean & Median Lead Time, Alert Volume (episodes/site/week), Per-Bank Breakdown, and Bootstrap 95% Confidence Intervals.
4. **Device-Episode Evaluation:**
   - Calculate event-level recall: Proportion of independent power outages preceded by an alert in $(t-24\text{h}, t]$.
   - Episode false alarm rate: Cluster consecutive alarms into discrete alert episodes.
5. **Operating Point Selection:**
   - High-Recall Warning Feed: Threshold tuned on validation for $\ge 80\%$ recall.
   - High-Precision Dispatch Candidate: Threshold tuned on validation for maximum precision ($\ge 30\%$).
6. **Deliverables & Gate:**
   - Full evaluation report with ablation comparison table.
   - Unambiguous senior verdict: Does LightGBM legitimately beat rules once leakage is eliminated?

---

### Phase R4: Fix Tiering & Re-do Shadow Mode (Fixing Issue C)

#### Objective:
Establish mathematical consistency across all evaluation tables and establish genuine operational validation.

#### Implementation Steps:
1. **Calibrated Probability Tiering:**
   - Derive tiers strictly from calibrated probability thresholds tuned on validation data:
     - `CRITICAL`: Top 1–2% highest risk ($P \ge P_{\text{crit}}$).
     - `HIGH`: Top 3–8% high risk ($P_{\text{high}} \le P < P_{\text{crit}}$). Combined HIGH + CRITICAL $< 10\%$.
     - `MEDIUM`: Moderate risk ($P_{\text{med}} \le P < P_{\text{high}}$).
     - `LOW`: Baseline operational noise ($P < P_{\text{med}}$).
   - Rules may elevate tier only for severe instantaneous electrical hazards (e.g. AC Surge $> 250\text{ V}$).
   - Sites in `STALE_DATA` / `OFFLINE_LONG` produce a separate `CONNECTIVITY_ISSUE` alert, never a power outage tier.
2. **Unified Evaluation Pipeline & Consistency Assertion:**
   - Both the risk tier table and the confusion matrix must be generated in a single script from the identical log file.
   - Automated unit test asserting:
     Total Cycles = TP + FP + FN + TN = Sum of Tiers
     Total Realized Outages = TP + FN = Sum of Realized Outages per Tier
3. **Clear Run Separation:**
   - `BACKTEST`: Historical replay on an untouched period never used in training, tuning, or threshold selection.
   - `LIVE_SHADOW`: Operational daemon scoring incoming cycles every 15 minutes, persisting predictions with timestamp and model version, and scoring with a 24-hour maturation lag.
4. **Deliverables & Gate:**
   - Verified backtest report with 100% table consistency.
   - Active `src/shadow/shadow_job.py` daemon running with logged start timestamp.

---

### Phase R5: Improve the Explanation Layer (Fixing Issue D)

#### Objective:
Transform the explanation layer from a numbers-only validator with heavy template fallback into an operationally safe, claim-validated reasoning engine.

#### Implementation Steps:
1. **State-Conditioned Routing:**
   - If `device_state` in (`STALE_DATA`, `OFFLINE_LONG`): Output connectivity ticket ("Investigate cellular gateway link, SIM connectivity, site breaker"). Zero outage probability displayed.
   - If `device_state == ONLINE_POWER_LOSS`: Output electrical failure dispatch ticket.
2. **Semantic & Claim-Level AST Validator:**
   - Extend `src/explain/validator.py` beyond numerical extraction.
   - Cross-check qualitative claims against payload:
     - Mains status ("mains healthy" vs "mains lost").
     - Battery status ("battery normal" vs "battery discharging" vs "unmonitored").
     - Risk tier names and probability ranges.
     - Active physical rule names.
3. **Dual Metric Reporting:**
   - Report **Raw LLM Pass Rate** as the primary model metric (not masked by fallback).
   - Report **Delivered Faithfulness** (after fallback) as the operational safety guarantee.
4. **Dataset Alignment & LoRA Fine-Tuning:**
   - Generate an expanded, grounded dataset ($\ge 500$ cases) from the remediated pipeline.
   - Reserve a strict held-out test split ($\ge 60$ cases) never seen during fine-tuning.
   - Fine-tune Qwen LoRA on the training split only.
   - Benchmark raw pass rate before vs. after fine-tuning. If fine-tuning fails to achieve $\ge 70\%$ raw pass rate, designate the deterministic template as the primary production engine and LLM as secondary.
5. **Formal Human Audit Methodology:**
   - Double-blind evaluation protocol on 30 cases with 2 reviewers.
   - Report rubric, Cohen's kappa inter-rater agreement, and show the 5 worst failure cases.

---

### Phase R6: Documentation & Model Card

#### Objective:
Publish honest, transparent documentation and production model card reflecting all remediated findings.

#### Implementation Steps:
1. **Model Card:** Scope, intended use (advisory warning feed, NOT autonomous dispatch), data lineage, calibrated performance with bootstrap CIs, known failure modes (instant utility breaker trips without degradation, missing battery hardware), and telemetry drift monitoring plan.
2. **README:** One-command reproducible execution per phase.
3. **Deprecations:** Mark `deep_model_test_report.md` and legacy v1 reports as formally deprecated.
4. **Limitations Section:** Exact sample sizes, fleet coverage limits, and failure boundaries.

---

## 4. Files Touched & Impact Radius

| Phase | Files to Create / Modify | Purpose of Change |
|---|---|---|
| **R0** | `configs/config.yaml`, `reports/remediation_plan_r0.md` | Establish remediation parameters and freeze legacy baseline. |
| **R1** | `src/features/state_machine.py`, `src/features/build_features.py`, `tests/test_phase_r1_state_classification.py` | Implement 4-state device classification; treat battery 0.0V as NaN; replace silent fills. |
| **R2** | `configs/config.yaml`, `src/ingest/audit_data.py`, `tests/test_phase_r2_label_leakage.py` | Isolate electrical outage label `y_power_outage_24h` from heartbeat gap; test zero leakage. |
| **R3** | `src/predict/train_predict.py`, `src/eval/benchmark_remediation.py`, `tests/test_phase_r3_predictor.py` | Retrain LightGBM with purged splits; run heartbeat ablation; report honest test metrics. |
| **R4** | `src/shadow/shadow_job.py`, `src/shadow/evaluate_shadow.py`, `tests/test_phase_r4_consistency.py` | Recalibrate tiers to $<10\%$ volume; unify logging; add mathematical consistency assertions. |
| **R5** | `src/explain/validator.py`, `src/explain/explainer.py`, `src/explain/fine_tune_lora.py`, `tests/test_phase_r5_claims.py` | State-based routing; claim-level AST validator; train/test split; benchmark raw pass rate. |
| **R6** | `README.md`, `reports/remediation_final_report.md`, `docs/MODEL_CARD.md` | Final documentation, model card, and formal deprecation notices. |

---

## 5. Potential Risks & What Could Invalidate Results

1. **Risk: Insufficient Independent Electrical Outages ($N < 30$):**
   - If decoupling heartbeat gap leaves fewer than 30 independent physical power outages across the 135 sites, training a supervised gradient-boosted tree will severely overfit or fail to generalize.
   - *Mitigation:* We will audit event counts immediately in Phase R2. If $N < 30$, we will explicitly stop and recommend relying on deterministic rules and unsupervised anomaly detection rather than forcing a model.
2. **Risk: Performance Drop After Leakage Removal:**
   - Stripping `heartbeat_gap_seconds` will likely reduce reported PR-AUC and precision compared to Phase 4.
   - *Mitigation:* We adhere to the senior ML engineer principle: "Honest is the goal, not good-looking." We will report the unvarnished lower numbers and ablation delta clearly.
3. **Risk: Battery Sensor Discrepancy Across Hardware Generations:**
   - With 50.4% of raw battery readings at 0.0V, half the fleet lacks battery voltage telemetry.
   - *Mitigation:* The state machine and feature engine will explicitly treat missing battery channels as unequipped (`NaN` + `is_battery_sensor_present = 0`), preventing false positive discharges.
4. **Risk: LLM Fine-Tuning Overfitting or Token Drift:**
   - LoRA fine-tuning on a small sample can memorize phrasing without mastering claim faithfulness.
   - *Mitigation:* Strict held-out evaluation set ($\ge 60$ cases) and automatic fallback to deterministic templates if raw LLM pass rate fails to exceed 70%.

---

## 6. Blocking & Clarification Questions for User Approval

Before initiating Phase R1, please confirm or guide us on:

1. **Battery Channel Interpretation:**
   - Our raw audit shows that 50.4% of battery readings are flat `0.0 V`, and 18 branches have no battery file. We propose treating `0.0 V` as `NaN` (sensor unequipped/unmonitored) unless accompanied by a continuous non-zero voltage drop history. Do you approve this treatment?
2. **Electrical Outage Definition (`y_power_outage_24h`):**
   - We propose defining a true electrical outage as: Mains lost (`statusbox_mains_on == 0` or AC drop $< 50\text{ V}$) sustained for $\ge 15$ minutes AND (battery $< 10.8\text{ V}$ on equipped channels OR verified utility `POWER OFF` alarm). Heartbeat gap is completely excluded. Do you approve this definition?
3. **Connectivity Target Modeling:**
   - For `y_connectivity_loss_24h` (heartbeat silence $\ge 60$ min), should we model it as a secondary LightGBM classifier in Phase R3, or handle it strictly via the Phase R1 operational state machine?
4. **Shadow Mode Execution Period:**
   - For Phase R4, should the LIVE_SHADOW daemon run as a background task against the incoming stream / API, or would you prefer a clearly marked `HISTORICAL_BACKTEST_UNSEEN` run over an untouched 14-day holdout slice?

---

## 7. Gate Status

**Phase R0 Plan Status:** COMPLETE  
**Next Step:** Awaiting your response to the questions above and your explicit **"approved"** before proceeding to Phase R1.
