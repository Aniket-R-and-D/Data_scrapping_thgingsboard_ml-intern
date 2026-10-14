# Phase R5 Stage E Report — Does the LLM Add Value?

**Date:** October 14, 2026
**Scope:** Stage E of the R5 reliability-recovery plan (paired evaluation of deterministic renderer, base instruction model, and existing remediated Qwen checkpoint on the frozen Stage D clean evaluation splits; 30-case human review pack; evidence-backed LLM enablement decision).
**Policy Version:** `ticket-policy-v1`
**Validator Version:** `stage-c-field-validator-v1`
**Status:** GATE DELIVERABLE — awaiting approval.
**Provenance:** Executed by the same AI agent that performed the Phase R5 work (self-audit disclosure; not an independent audit).

**Explicit statements:**
- **No model fine-tuning occurred in Stage E.**
- **No automatic dispatch, ThingsBoard alarm creation, or external notification occurred.**
- **No historical evidence, earlier phase report, frozen manifest, or evaluation split file was modified.**
- **Independent semantic accuracy is `NOT MEASURED`.** No human reviewer has scored any ticket. Human review status is **`HUMAN REVIEW PENDING`** (`completed_reviews_count = 0`, `cohens_kappa = null`).
- **Evidence-backed recommendation: `KEEP EXPERIMENTAL`** (the deterministic renderer `src/tickets/renderer.py` remains the sole default operational ticket path; `EXPERIMENTAL_LLM_ENABLED` remains `False`).

---

## 1. Models, Checkpoints, Cryptographic Hashes, and Generation Settings

| System | Identifier / Path | Weights / Artifact SHA-256 | Peak GPU Memory |
|---|---|---|---:|
| **A. Deterministic Renderer (Default)** | `src/tickets/renderer.py` (`ticket-policy-v1`) | Code-deterministic (0 model weights) | **0.00 MB** |
| **B. Base Instruction Model** | `Qwen/Qwen2.5-0.5B-Instruct` (HuggingFace local cache) | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` | **1,822.56 MB** |
| **C. Remediated Qwen Checkpoint** | `models/qwen_reasoning_outage_predictor/remediated_final_model` (`model.safetensors`) | `be45761a2a66ed5dc5911009a1c3784af1d2d4dc5b392b9343b76cdbfe58e051` | **1,823.92 MB** |
| **C (LoRA Adapter Source)** | `models/qwen_reasoning_outage_predictor/remediated_lora_adapter` (`adapter_model.safetensors`) | `db16593c3500c4d09c5dd4bfb4aae448a6ca91856eaf5db25d6d314681843438` | — |

### Identical Generation Settings Across Systems B and C
- **Compute Device:** NVIDIA GeForce RTX 3050 6GB Laptop GPU (`torch.float16`)
- **`max_new_tokens`:** `220`
- **`do_sample`:** `False` (greedy deterministic decoding; `temperature = null`, `top_p = null`)
- **`repetition_penalty`:** `1.1`
- **`padding_side`:** `"left"`
- **`batch_size`:** `32`
- **`system_prompt_sha256`:** `9717901f277575a2648f48bc344108cb107aa0912a985aa5212a3de2bd31b262`
- **Per-case prompt SHA-256:** Recorded for every evaluation record in [`reports/phase_r5_stage_e_raw_results.json`](phase_r5_stage_e_raw_results.json).
- **Evaluation sets used:** Frozen Stage D `FUTURE_INCIDENTS_KNOWN_DEVICES` (407 records) and `UNSEEN_DEVICES` (150 records). Neither `TRAIN` nor the retired contaminated benchmark was used.

---

## 2. Per-Set Quantitative Metrics (Reported Separately; Never Pooled)

### 2.1 Split A: `FUTURE_INCIDENTS_KNOWN_DEVICES` (407 records, 297 incidents, 28 known devices, post-24h purge)

| Metric | A. Deterministic Renderer | B. Base `Qwen2.5-0.5B-Instruct` | C. Remediated Qwen Checkpoint |
|---|---:|---:|---:|
| **Evaluated records** | 407 | 407 | 407 |
| **Exact payload duplication vs. `TRAIN`** | **0** | **0** | **0** |
| **Generation / render success rate** | **100.00%** (407/407) | **100.00%** (407/407) | **100.00%** (407/407) |
| **Stage C field-validator acceptance (`ticket-policy-v1`)** | **100.00%** (407/407) | **0.00%** (0/407) | **0.00%** (0/407) |
| **Stage C fallback frequency** | **0.00%** | **100.00%** | **100.00%** |
| **Legacy R5 validator acceptance (10-key prompt schema)** | N/A (different schema) | 87.96% (358/407) | 99.51% (405/407) |
| **All content fields accurate (identity, state, tier, prob, AC, batt, mains, rules)** | **100.00%** (407/407) | **7.62%** (31/407) | **99.02%** (403/407) |
| — Identity (`device_name` + `bank`) | 100.00% | 100.00% | 100.00% |
| — Device state | 100.00% | 98.77% | 100.00% |
| — Risk tier | 100.00% | 88.70% | 100.00% |
| — Probability (formatted % within ±0.05 pp, or omitted on connectivity) | 100.00% | 16.71% | 99.26% (404/407) |
| — `ac_voltage` value + `V` unit | 100.00% | 73.71% | 100.00% |
| — `battery_voltage` value + `V` unit or absent-sensor notice | 100.00% | 89.43% | 99.75% (406/407) |
| — `time_since_mains_loss` value + `hours` unit | 100.00% | 78.62% | 100.00% |
| — Triggered rules exact match | 100.00% | 98.28% | 100.00% |
| **Required-field completeness (`ticket-policy-v1`)** | **100.00%** (407/407) | **0.00%** (0/407) | **0.00%** (0/407) |
| **Records with unsupported claims** | **0 (0.00%)** | **380 (93.37%)** | **14 (3.44%)** |
| **Commentary usefulness:** `adds_supported_investigation_step` | 0 | **0** | **0** |
| **Commentary usefulness:** `adds_supported_observed_fact` | 0 | **0** | **0** |
| **Commentary usefulness:** `adds_no_new_information` | 407 | 27 | 393 |
| **Commentary usefulness:** `adds_unsupported_claim` | 0 | 380 | 14 |
| **Mean latency per ticket** | **0.00009 s** (0.09 ms) | **0.42971 s** (430 ms) | **0.28747 s** (287 ms) |
| **p95 latency per ticket** | **0.00016 s** (0.16 ms) | **0.57860 s** (579 ms) | **0.35740 s** (357 ms) |
| **Peak GPU memory** | **0.00 MB** | **1,822.56 MB** | **1,823.92 MB** |
| **Independent semantic accuracy** | **NOT MEASURED** | **NOT MEASURED** | **NOT MEASURED** |

### 2.2 Split B: `UNSEEN_DEVICES` (150 records, 90 incidents, 27 whole held-out devices across all 4 banks)

| Metric | A. Deterministic Renderer | B. Base `Qwen2.5-0.5B-Instruct` | C. Remediated Qwen Checkpoint |
|---|---:|---:|---:|
| **Evaluated records** | 150 | 150 | 150 |
| **Exact payload duplication vs. `TRAIN`** | **0** | **0** | **0** |
| **Generation / render success rate** | **100.00%** (150/150) | **100.00%** (150/150) | **100.00%** (150/150) |
| **Stage C field-validator acceptance (`ticket-policy-v1`)** | **100.00%** (150/150) | **0.00%** (0/150) | **0.00%** (0/150) |
| **Stage C fallback frequency** | **0.00%** | **100.00%** | **100.00%** |
| **Legacy R5 validator acceptance (10-key prompt schema)** | N/A (different schema) | 61.33% (92/150) | 96.67% (145/150) |
| **All content fields accurate (identity, state, tier, prob, AC, batt, mains, rules)** | **100.00%** (150/150) | **18.00%** (27/150) | **96.67%** (145/150) |
| — Identity (`device_name` + `bank`) | 100.00% | 100.00% | 100.00% |
| — Device state | 100.00% | 88.00% | 100.00% |
| — Risk tier | 100.00% | 73.33% | 100.00% |
| — Probability (formatted % within ±0.05 pp, or omitted on connectivity) | 100.00% | 45.33% | 96.67% (145/150) |
| — `ac_voltage` value + `V` unit | 100.00% | 52.00% | 100.00% |
| — `battery_voltage` value + `V` unit or absent-sensor notice | 100.00% | 92.67% | 100.00% |
| — `time_since_mains_loss` value + `hours` unit | 100.00% | 56.00% | 100.00% |
| — Triggered rules exact match | 100.00% | 96.00% | 100.00% |
| **Required-field completeness (`ticket-policy-v1`)** | **100.00%** (150/150) | **0.00%** (0/150) | **0.00%** (0/150) |
| **Records with unsupported claims** | **0 (0.00%)** | **135 (90.00%)** | **31 (20.67%)** |
| **Commentary usefulness:** `adds_supported_investigation_step` | 0 | **0** | **0** |
| **Commentary usefulness:** `adds_supported_observed_fact` | 0 | **0** | **0** |
| **Commentary usefulness:** `adds_no_new_information` | 150 | 15 | 119 |
| **Commentary usefulness:** `adds_unsupported_claim` | 0 | 135 | 31 |
| **Mean latency per ticket** | **0.00008 s** (0.08 ms) | **0.31410 s** (314 ms) | **0.26982 s** (270 ms) |
| **p95 latency per ticket** | **0.00013 s** (0.13 ms) | **0.48470 s** (485 ms) | **0.44310 s** (443 ms) |
| **Peak GPU memory** | **0.00 MB** | **1,822.56 MB** | **1,823.92 MB** |
| **Independent semantic accuracy** | **NOT MEASURED** | **NOT MEASURED** | **NOT MEASURED** |

### 2.3 Stage C Adversarial Suite Reference Counts
- **False acceptances:** **0 / 10**
- **False rejections:** **0 / 5**
- **Paraphrase variants accepted:** **10 / 10**

---

## 3. Paired Comparisons (Answers to Stage E.3 Questions)

| Paired Question | Deterministic vs. Base Model | Deterministic vs. Remediated Qwen | Base Model vs. Remediated Qwen |
|---|---|---|---|
| **1. Does the second system add any supported information absent from the deterministic ticket?** | **NO** (0/407 on `FUTURE`, 0/150 on `UNSEEN`). The deterministic renderer already outputs 100% of validated fields in `TicketInput`. | **NO** (0/407 on `FUTURE`, 0/150 on `UNSEEN`). Remediated Qwen was trained to imitate the legacy deterministic template; it never adds a supported fact or approved check beyond the payload. | Neither LLM adds supported information absent from the deterministic ticket. |
| **2. Does it introduce unsupported claims?** | **YES:** Base introduces unsupported claims on **380/407 (93.4%)** `FUTURE` cases and **135/150 (90.0%)** `UNSEEN` cases (raw decimal probabilities instead of %, missing connectivity investigation steps, invented thresholds like `5 days` or `1800s`, and battery-failure claims on sensorless sites). | **YES:** Remediated Qwen introduces unsupported claims on **14/407 (3.4%)** `FUTURE` cases and **31/150 (20.7%)** `UNSEEN` cases: (a) on every `HIGH`/`CRITICAL` site without a battery sensor, its memorized template instructs technicians to *"Inspect … UPS battery bank health"* despite just stating no battery sensor is present; (b) it emits *"Urgent electrical maintenance required"* (unbounded dispatch directive); and (c) on **8 cases** (3 `FUTURE`, 5 `UNSEEN`) it mis-rounds/truncates the outage probability (`36.0%` for `0.3697 -> 37.0%`, `24.0%` for `0.2378 -> 23.8%`, `42.0%` for `0.4193 -> 41.9%`, `29.0%` for `0.2894 -> 28.9%`), plus 1 `STALE_DATA` case (`FUTU-0049`) where it prints stale voltages as an unlabeled "Observed Physical Telemetry Snapshot". | Remediated Qwen substantially reduces raw field errors compared to Base (accurate on all content fields on 373/407 `FUTURE` and 118/150 `UNSEEN` records where Base fails), confirming that LoRA memorized the legacy template format — **but it still fails where the deterministic renderer has 0 errors.** |
| **3. Does it improve actionability for field staff?** | **NO.** Base frequently omits gateway/SIM checks on stale sites and suggests checking batteries on unequipped sites. | **NO.** Remediated Qwen repeats fixed boilerplate paragraphs and lacks the Stage B/C separation of `OBSERVED`, `UNKNOWN`, `POSSIBLE CAUSE`, and bounded `INVESTIGATION` actions. | Remediated Qwen is more structured than Base, but strictly less actionable and less precise than the deterministic renderer. |
| **4. Is it reliable enough to be enabled by default?** | **NO** (0% Stage C acceptance; 7.6%–18.0% content-field accuracy). | **NO** (0% Stage C acceptance under `ticket-policy-v1`; 9 failures on content-field accuracy across the 557 clean records; 45 records with unsupported battery/dispatch/rounding claims). | **NO.** Neither model is reliable enough for default production use. |
| **5. Is it faster or slower than the deterministic path?** | **~3,900×–4,800× slower** (314–430 ms on GPU + 1.82 GB VRAM vs. 0.08–0.09 ms on CPU + 0 MB VRAM). | **~3,200×–3,400× slower** (270–287 ms on GPU + 1.82 GB VRAM vs. 0.08–0.09 ms on CPU + 0 MB VRAM). | Remediated Qwen is ~14%–33% faster than Base due to shorter, uniform template completions. |

---

## 4. Concrete Examples from the Clean Evaluation Sets

### 4.1 Remediated Qwen — Probability Truncation & Sensorless Battery Claim on `UNSEEN_DEVICES` (`UNSE-0003`, `BOI-MALDATOWN`)
- **Input payload summary:** `device_state = ONLINE_OK`, `risk_tier = CRITICAL`, `calibrated_probability = 0.3697` (canonical display: **`37.0%`**), `ac_voltage = 220.0 V (CURRENT)`, `battery_voltage = UNAVAILABLE` (no battery sensor), `time_since_mains_loss = 168.0 hours (CURRENT)`.
- **Remediated Qwen raw output:**
  ```text
  [SYSTEM DIAGNOSTIC TICKET - CRITICAL RISK]
  Branch Site: BOI-MALDATOWN (BOI)
  Device State: ONLINE_OK
  24-Hour Outage Probability: 36.0%
  Assigned Risk Tier: CRITICAL
  Active Physical Rule Violations: None
  Key Predictive Drivers: ac_voltage_slope_6h; time_since_mains_loss_hours

  Observed Physical Telemetry Snapshot:
  - AC Line Voltage: 220.0 V
  - Battery Voltage: Not equipped (No battery voltage sensor present)
  - Time Since Mains Loss: 168.0 hours

  DISPATCH RECOMMENDATION: Urgent electrical maintenance required. Inspect site utility power distribution board, UPS battery bank health, and primary line inverter. Verify mains contactor and physical breaker positions.
  ```
- **Why rejected / unsupported:** (1) Truncates `0.3697` (`37.0%`) to `36.0%` (a 1.0 percentage-point error, 20× the `0.05` pp policy tolerance); (2) instructs field staff to *"Inspect … UPS battery bank health"* despite stating three lines earlier that no battery sensor is present; (3) issues an unbounded dispatch directive (*"Urgent electrical maintenance required"*) and omits `Ticket ID`, `Device ID`, `UNKNOWN`, `POSSIBLE CAUSE`, and `Input Payload SHA-256`.

### 4.2 Remediated Qwen — Stale Data Presented as Current Snapshot on `FUTURE_INCIDENTS_KNOWN_DEVICES` (`FUTU-0049`, `BOI-DOBSON`)
- **Input payload summary:** `device_state = STALE_DATA`, `risk_tier = CONNECTIVITY_ISSUE`, `calibrated_probability = null`, `triggered_rules = ["RULE_AC_SURGE_OVER_250V"]`, stale last-known `ac_voltage = 270.0 V (STALE)`, `battery_voltage = 14.0 V (STALE)`.
- **Remediated Qwen raw output:**
  ```text
  [SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]
  Branch Site: BOI-DOBSON (BOI)
  Device State: STALE_DATA (Telemetry staleness / communication drop)
  Assigned Status: CONNECTIVITY_ISSUE
  Active Physical Rule Violations: RULE_AC_SURGE_OVER_250V
  Key Predictive Drivers: heartbeat_staleness

  Observed Physical Telemetry Snapshot:
  - AC Line Voltage: 270.0 V
  - Battery Voltage: 14.0 V
  - Time Since Mains Loss: 815.0 hours

  DISPATCH RECOMMENDATION: Investigate gateway, SIM/network, power at the site. Verify site AC line voltage stability over 250 V before dispatching electrical maintenance.
  ```
- **Why rejected / unsupported:** Because `RULE_AC_SURGE_OVER_250V` was present on a `STALE_DATA` record, the model blended the connectivity template with the diagnostic template and printed stale last-known voltages (`270.0 V`, `14.0 V`) under `Observed Physical Telemetry Snapshot` without labeling them stale or last-known.

### 4.3 Base Instruction Model — Worst Failure on `UNSEEN_DEVICES` (`UNSE-0001`, `BOI-MALDATOWN`, `OFFLINE_LONG`)
- **Input payload summary:** `device_state = OFFLINE_LONG`, `risk_tier = CONNECTIVITY_ISSUE`, `battery_voltage = UNAVAILABLE`, `time_since_mains_loss = 168.0 hours (STALE)`.
- **Base model raw output (excerpt):**
  ```text
  The device is currently offline for **168 hours**, which exceeds the allowed duration of **5 days**. This indicates potential issues with the connection to the main power grid.
  ##### Potential Causes:
  1. **Battery Failure**: The battery may be failing due to prolonged exposure to low-voltage conditions.
  2. **Power Grid Issues**: There could be a problem with the power grid itself...
  #### Recommendations:
  1. **Check Battery Health**: Ensure the...
  ```
- **Why rejected / unsupported:** Misinterprets `time_since_mains_loss_hours = 168.0` as offline duration, invents a `"5 days"` policy threshold, diagnoses `"Battery Failure"` and recommends `"Check Battery Health"` on a site with no battery sensor, and diagnoses power grid failure on a communication blackout.

### 4.4 Best Remediated Qwen Output (`FUTU-0001`, `CANARA-CHETLA`, `ONLINE_OK`, `LOW`)
- On steady `LOW`-risk online records where `calibrated_prob` does not require upward rounding in the hundredths place (e.g., `0.0046 -> 0.5%`), Remediated Qwen reproduces the legacy R5 template cleanly with 0 numerical errors — **but still adds zero information beyond the deterministic renderer while taking ~3,200× longer and omitting Stage B/C traceability fields.**

---

## 5. Human Review Pack Status (`reports/phase_r5_stage_e_human_review_pack/`)

- **Status:** **`HUMAN REVIEW PENDING`** (`completed_reviews_count = 0`, `cohens_kappa = null`).
- **Files created:**
  - [`reports/phase_r5_stage_e_human_review_pack/blank_review_forms_30_cases.json`](phase_r5_stage_e_human_review_pack/blank_review_forms_30_cases.json) — 30 stratified cases (10 `FUTURE_INCIDENTS_KNOWN_DEVICES`, 10 `UNSEEN_DEVICES`, 10 selected for difficulty: stale/offline with missing battery sensor, borderline probability near tier cutoffs, active power loss without battery sensor, and `ONLINE_OK` voltage with elevated risk tier). Each case contains the full `input_payload`, randomized `Option_A` / `Option_B` / `Option_C` tickets, and a completely blank (`null`) scoring form covering:
    1. `factual_consistency_1_to_5`
    2. `claim_grounding_pass_or_fail`
    3. `operational_actionability_1_to_5`
    4. `harmful_or_misleading_recommendation_yes_or_no`
    5. `preferred_ticket_to_send` (`Option_A` | `Option_B` | `Option_C` | `NONE`)
  - [`reports/phase_r5_stage_e_human_review_pack/blinding_key_DO_NOT_OPEN_DURING_REVIEW.json`](phase_r5_stage_e_human_review_pack/blinding_key_DO_NOT_OPEN_DURING_REVIEW.json) — randomized option-to-system key and unblinded reference tickets for post-review unblinding.
  - [`reports/phase_r5_stage_e_human_review_pack/README_REVIEW_PROTOCOL.md`](phase_r5_stage_e_human_review_pack/README_REVIEW_PROTOCOL.md) — instructions for human reviewers.

---

## 6. Evidence-Backed LLM Enablement Recommendation

### Decision: **`KEEP EXPERIMENTAL`** (Default OFF; Deterministic Renderer Remains the Only Operational Path)

1. **Why not enable the LLM by default?**
   - Across all **557** clean evaluation records (`407` `FUTURE_INCIDENTS_KNOWN_DEVICES` + `150` `UNSEEN_DEVICES`), the existing remediated Qwen model adds **zero supported observed facts (`0/557`)** and **zero supported investigation steps (`0/557`)** beyond the deterministic renderer.
   - Because the remediated Qwen model was trained to copy the legacy R5 template, its best outputs are simply slower, less complete approximations of a Python template, while its failure modes include probability truncation errors (`36.0%` for `37.0%`, `24.0%` for `23.8%`, `42.0%` for `41.9%`), instructing technicians to inspect UPS battery banks on sensorless sites (`45/557` records), and printing stale voltages as current snapshots when a rule is present on a `STALE_DATA` gateway (`FUTU-0049`).
   - The deterministic renderer (`src/tickets/renderer.py`) achieves **100.0% Stage C field-validator acceptance**, **0 unsupported claims**, **0.08–0.09 ms CPU latency**, and **0 MB GPU memory**.
2. **Why not retrain right now (`RETRAIN WITH JUSTIFICATION` rejected)?**
   - A structured operational ticket whose inputs are already tabular/typed (`TicketInput`) has **no capability gap that requires an LLM**: every measurement, timestamp, rule, tier, and approved investigation action is already deterministically known and rendered without error by `src/tickets/renderer.py`. Retraining Qwen to imitate the new Stage B/C template would only repeat the target-collapse mistake documented in Stage A (memorizing a deterministic Python function with a 500M-parameter neural network).
3. **What evidence would be required to justify enabling `EXPERIMENTAL_LLM_ENABLED` in the future?**
   - A non-tabular input source that a deterministic template cannot render (e.g., free-text technician field notes or multi-incident historical narratives) where an LLM commentary block is needed; **AND**
   - Evaluation on the frozen Stage D splits (`FUTURE_INCIDENTS_KNOWN_DEVICES` and `UNSEEN_DEVICES`) showing `>= 99.5%` Stage C commentary validation pass rate with **0** probability/unit/sensorless-battery errors; **AND**
   - Completed, unblinded human review of the 30-case review pack (`blank_review_forms_30_cases.json`) by qualified reviewers demonstrating a statistically significant preference for tickets with LLM commentary over the deterministic ticket alone.

---

## 7. Commands Run and Exit Codes

| Command | Exit Code | Result |
|---|---:|---|
| `.venv\Scripts\python.exe src/eval/run_stage_e_comparison.py` | **0** | Evaluated 3 systems × 557 clean records; wrote `reports/phase_r5_stage_e_raw_results.json` and 30-case review pack in `reports/phase_r5_stage_e_human_review_pack/` |
| `.venv\Scripts\python.exe -m pytest tests/test_stage_e_comparison.py -q` | **0** | `5 passed in 0.09s` |
| Full regression suite (`test_tickets_schema`, `test_tickets_renderer`, `test_field_level_validator`, `test_evaluation_split_integrity`, `test_stage_e_comparison`, `test_phase_r1..r5`) | **0** | `117 passed` |
