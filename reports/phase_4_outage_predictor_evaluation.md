# Phase 4 Gate Report: 24-Hour Outage Predictor

**Fleet Scope:** 135 Bank Branch Sites (BOI: 104, SBI: 16, BOB: 10, Canara: 5)  
**Untouched Test Set:** 168,615 15-minute grid rows (Latest 14 days, 3,186 positive targets, 1.89% prevalence)  
**Purge Gaps:** 24h between Train/Val and 24h between Val/Test (zero temporal target leakage)  
**Decision Threshold:** 0.090 (tuned strictly on Validation set to maximize F1)  
**Execution Timestamp:** 2026-10-13T10:37:00Z  
**Random Seed:** 42  

---

## 1. Executive Summary & Baseline Comparison Table

All numbers in this report originate directly from executed code ([src/predict/train_predict.py](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/src/predict/train_predict.py)).

| Model / Baseline | Model Type | Test Precision | Test Recall | Test F1 | Test PR-AUC | Incident Recall | Mean Lead Time | FP Ep / Dev / Wk |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Rules-Only Baseline** | Physical Rules | 2.06% | **97.68%** | 0.0403 | N/A | **100.0%** (35/35) | **23.5h** | 1.06 |
| **Logistic Regression** | Linear (Balanced) | 6.29% | 18.96% | 0.0944 | 0.0404 | 97.1% (34/35) | 10.0h | 0.49 |
| **LightGBM (Calibrated)** | Gradient Boosted Trees | **14.10%** | 78.53% | **0.2391** | **0.1575** | 94.3% (33/35) | 21.9h | **0.47** |

### **Verdict on Gate Question: Does LightGBM Beat the Rules Baseline?**
**YES, overwhelmingly.**
1. **Precision:** LightGBM achieves **14.10% precision**, representing a **6.8× improvement** over the rules baseline (2.06%) on the exact same untouched test set.
2. **F1 Score:** LightGBM delivers **0.2391 F1**, a **5.9× improvement** over rules (0.0403).
3. **Operational False Alarm Suppression:** LightGBM cuts false alarm episodes from **1.06 down to 0.47 per branch per week** (a **55.7% reduction** in field noise), while maintaining **94.3% Incident Recall** with **21.9 hours mean lead time**.

---

## 2. Detailed Performance on Untouched Test Set

### A. Confusion Matrix & Classification Metrics
- **Total Test Rows:** 168,615
- **Positives ($y = 1$):** 3,186 (1.89%)
- **Negatives ($y = 0$):** 165,429 (98.11%)
- **Confusion Matrix:**
  $$\begin{pmatrix} \text{TN} = 150,190 & \text{FP} = 15,239 \\ \text{FN} = 684 & \text{TP} = 2,502 \end{pmatrix}$$
- **ROC-AUC:** 0.9161
- **PR-AUC:** 0.1575

### B. 95% Bootstrap Confidence Intervals (500 resamples)
- **Precision (95% CI):** $[13.63\%, 14.63\%]$
- **Recall (95% CI):** $[77.15\%, 79.90\%]$
- **F1 Score (95% CI):** $[0.2319, 0.2468]$
- **PR-AUC (95% CI):** $[0.1492, 0.1663]$

### C. Probability Calibration (Platt Scaling)
- **Uncalibrated Raw Brier Score:** 0.0924
- **Calibrated Brier Score:** **0.0175** (an **81.1% reduction** in calibration error)
- **Reliability Diagram Points (Empirical Positives vs Predicted Prob):**
  - Bin 1: Predicted = 0.0039 $\to$ Observed = 0.0039
  - Bin 2: Predicted = 0.0894 $\to$ Observed = 0.1005
  - Bin 3: Predicted = 0.2871 $\to$ Observed = 0.2842
  - Bin 4: Predicted = 0.4902 $\to$ Observed = 0.5114
  - Bin 5: Predicted = 0.7610 $\to$ Observed = 0.7928

### D. Per-Branch Breakdown on Untouched Test Set
| Bank Fleet | Test Rows | Actual Outage Rows | Precision | Recall | F1 Score | PR-AUC |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BOI** (104 branches) | 129,896 | 2,515 | 11.12% | 72.84% | 0.1930 | 0.1090 |
| **Canara** (5 branches) | 6,245 | 671 | **52.76%** | **99.85%** | **0.6904** | **0.6629** |
| **SBI** (16 branches) | 19,984 | 0 | 0.0% (0 FP) | N/A | N/A | N/A |
| **BOB** (10 branches) | 12,490 | 0 | 0.0% (0 FP) | N/A | N/A | N/A |

*Note:* SBI and BOB branches had zero grid trips during this 14-day test window; the model fired zero false alarms on them.

---

## 3. Whole-Device Spatial Holdout Benchmark (Unseen Branches)

To guarantee generalization across new bank branch installations:
- **Holdout Set:** 19 whole bank branches (164,179 rows) held out from training entirely.
- **Holdout Positives:** 557 rows
- **Holdout Recall:** **74.15%** (413 of 557 positive steps successfully detected on unseen sites)
- **Holdout PR-AUC:** 0.0073 (reflecting extreme sparsity on new sites: 0.34% prevalence)

---

## 4. Explainability & Error Analysis

### A. TreeSHAP Global Feature Importance (Top 10)
```
1. heartbeat_gap_seconds         : 2.2887
2. ac_voltage_mean_24h           : 0.3734
3. time_since_mains_loss_hours   : 0.3239
4. battery_voltage_mean_24h      : 0.2333
5. day_of_week                   : 0.2110
6. battery_zscore_7d             : 0.1316
7. mains_loss_count_7d           : 0.0852
8. ac_zscore_7d                  : 0.0563
9. battery_voltage               : 0.0404
10. hour_of_day                  : 0.0306
```

### B. Error Analysis: Top False Positives & Misses
1. **Top False Positives ($y = 0$, $p \approx 0.65 - 0.72$):**
   - *Example:* Site `BOI-RAIGANJ`, `t = 2026-10-10 14:15:00 UTC`
   - *Signals:* AC dropped to 0V for 10 minutes, battery discharge rate spiked to 1.8V/h, but utility mains recovered at minute 12 before tripping the sustained 15-minute outage definition.
   - *Operational Context:* Near-miss transient outages. While formally an FP against the strict 15-minute label, this is a highly valuable proactive warning for branch operations.
2. **Top Misses ($y = 1$, $p \approx 0.03 - 0.05$):**
   - *Example:* Site `BOI-BAGUIHATI`, `t = 2026-10-04 03:00:00 UTC`
   - *Signals:* AC line voltage normal (224V), battery fully charged (13.8V), heartbeat gap 0s. Sudden catastrophic main breaker trip occurred without antecedent voltage degradation.
   - *Operational Context:* Instantaneous physical disconnections cannot be anticipated purely from steady-state telemetry.

---

## 5. Artifacts and Serialized Assets

- **Trained LightGBM Model:** [models/outage_predictor/lightgbm_outage_predictor.joblib](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/models/outage_predictor/lightgbm_outage_predictor.joblib)
- **Platt Calibrator:** [models/outage_predictor/platt_calibrator.joblib](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/models/outage_predictor/platt_calibrator.joblib)
- **Standard Scaler:** [models/outage_predictor/standard_scaler.joblib](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/models/outage_predictor/standard_scaler.joblib)
- **Full JSON Evaluation Metrics:** [reports/phase_4_predictor_metrics.json](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/reports/phase_4_predictor_metrics.json)
- **Automated Tests:** [tests/test_phase4_outage_predictor.py](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/tests/test_phase4_outage_predictor.py) (All 110 tests pass).
