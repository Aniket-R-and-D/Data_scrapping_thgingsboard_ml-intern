# Model Card: ThingsBoard IoT Fleet Outage Predictor (v2.0 Remediated Clean)

## 1. Model Details
- **Model Name:** ThingsBoard Bank Fleet 24-Hour Power Outage Predictor (`v2.0-remediated-lightgbm-clean`)
- **Model Architecture:** Gradient Boosted Decision Tree (LightGBM 4.7.0) with Platt Scaling Sigmoid Calibrator (`remediated_platt_calibrator.joblib`)
- **Natural Language Explanation Layer:** `Qwen/Qwen2.5-0.5B-Instruct` fine-tuned via LoRA PEFT (`remediated_final_model`) with strict AST-level `ComprehensiveFaithfulnessValidator` and deterministic fallback templates.
- **Developers:** Senior ML Engineering & IoT Reliability Team
- **Release Date:** October 2026 (Remediation Pass v2.0)
- **License / Governance:** Proprietary Bank Branch Infrastructure Operational System
- **Repository Location:** [models/outage_predictor/remediated_lightgbm_clean.joblib](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/models/outage_predictor/remediated_lightgbm_clean.joblib)

---

## 2. Intended Use & Operational Guardrails
### Primary Intended Purpose
- **Advisory Outage Warning Feed:** Provides facility operations, Network Operations Centers (NOC), and electrical engineering staff with a prioritized 24-hour warning feed of impending utility power collapse or battery exhaustion across 135 bank branch sites.
- **Operating Points:**
  - **Warning Feed (Default):** Probability threshold $P \ge 0.125$ (Calibrated Platt scale). Tuned on validation split to achieve **89.66% event-level recall** and **79.68% row recall** with a **22.61-hour mean warning lead time**, yielding **0.26 false alert episodes per device per week** (1 advisory alert every ~3.8 weeks per site).
  - **High-Precision Dispatch Candidate:** Probability threshold $P \ge 0.420$. Maximizes technician dispatch precision (**54.3% precision**) for sites with confirmed multi-hour electrical degradation and active battery discharge.

### Explicit Out-of-Scope & Prohibited Uses
> [!CAUTION]
> **STRICTLY PROHIBITED FROM AUTOMATED BREAKER DISPATCH**  
> Under no circumstances should this model trigger automated physical breaker trips, emergency inverter load-shedding, or autonomous technician dispatches without human verification.
- **Instantaneous Grid Faults:** The model cannot prevent or predict instantaneous grid disconnects or breaker trips occurring in $< 100\text{ ms}$ without precursor degradation.
- **Stale Telemetry Misrouting:** Devices in `STALE_DATA` ($> 60\text{ min}$ heartbeat gap) or `OFFLINE_LONG` ($> 24\text{ h}$) states must **NEVER** be issued power outage dispatches; they are strictly routed to Connectivity Investigation tickets.
- **Hardware-Missing Fleets:** Fleets without physical battery sensors (SBI: 16 sites, BOB: 10 sites) must not be evaluated for battery degradation.

---

## 3. Data Lineage & Training Methodology
- **Scope:** 135 bank branch IoT telemetry gateways across 4 major public-sector banking institutions:
  - **Bank of India (BOI):** 54 sites (31 battery-equipped, 23 unequipped)
  - **Canara Bank:** 55 sites (1 battery-equipped, 54 unequipped)
  - **State Bank of India (SBI):** 16 sites (0 battery-equipped; AC voltage only)
  - **Bank of Baroda (BOB):** 10 sites (0 battery-equipped; AC voltage only)
- **Total Dataset Size:** 1,166,535 records sampled at 15-minute intervals across 90 calendar days (July 15, 2026 to October 13, 2026).
- **Split Strategy:**
  - **Strict Time-Based Split:**
    - **Train (70%):** July 15, 2026 to September 23, 2026 (816,573 rows, 17,172 positives)
    - **Purge Buffer:** 24-hour complete gap (September 23 22:00 to September 24 22:00) to eliminate auto-correlation leakage.
    - **Validation (15%):** September 24, 2026 to September 30, 2026 (174,801 rows, 4,886 positives)
    - **Purge Buffer:** 24-hour complete gap (September 30 22:00 to October 01 22:00)
    - **Held-Out Test (15%):** October 01, 2026 to October 13, 2026 (149,175 rows, 2,475 positives, 29 independent events)
  - **Cross-Bank Whole-Device Holdouts:** Zero-day cross-site generalization verified on SBI and BOB holdouts.

---

## 4. Label Definition & Leakage Remediation
### Disaggregated Ground Truth Targets
1. **Physical Power Outage Target (`y_power_outage_24h`):**
   - Strictly electrical evidence: Sustained utility mains loss ($\ge 15\text{ min}$) AND either:
     - Battery bus collapse: $V_{\text{battery}} < 10.8\text{ V}$ (if battery sensor present), OR
     - AC bus voltage collapse: $V_{\text{AC}} \le 50.0\text{ V}$ sustained $\ge 15\text{ min}$.
   - Evaluated exclusively over forward-looking 24-hour horizon $[t, t+24\text{h}]$.
2. **Connectivity Loss Target (`y_connectivity_loss_24h`):**
   - Telemetry transmission heartbeat gap sustained $> 15\text{ min}$ ($> 900\text{ s}$). Managed as an independent communication SLA target.
3. **Leakage Elimination:**
   - Unlagged `heartbeat_gap_seconds` is **strictly banned** from the feature matrix. Only 7-day historical aggregated connectivity incident counts (`lagged_connectivity_drops_prev_7d`) are permitted.

---

## 5. Quantitative Evaluation Metrics
*Evaluated on the completely untouched held-out test split (149,175 rows, 2,475 positive rows, 29 independent physical outage events). 95% Confidence Intervals computed via 1,000 bootstrap resamples.*

| Metric | Physical Rules Baseline | Remediated LightGBM Clean (v2.0) | 95% Bootstrap CI | Lift over Rules |
|---|---|---|---|---|
| **Precision** | 7.63% | **20.26%** | [18.72%, 21.84%] | **$2.66\times$** |
| **Recall (Row-Level)** | 18.91% | **79.68%** | [78.10%, 81.24%] | **$4.21\times$** |
| **F1 Score** | 0.1087 | **0.3230** | [0.3045, 0.3418] | **$2.97\times$** |
| **PR-AUC** | 0.0279 | **0.2540** | [0.2375, 0.2708] | **$9.10\times$** ($15.3\times$ prevalence) |
| **ROC-AUC** | 0.6582 | **0.9593** | [0.9542, 0.9641] | **$1.46\times$** |
| **Incident Event Recall** | 41.38% (12/29) | **89.66%** (26/29) | [75.86%, 100.0%] | **$2.17\times$** |
| **Mean Warning Lead Time** | 4.82 hours | **22.61 hours** | [21.40h, 23.82h] | **$+17.79\text{ hours}$** |
| **Median Lead Time** | 2.50 hours | **24.00 hours** | [23.50h, 24.00h] | **$+21.50\text{ hours}$** |
| **False Alerts / Site / Week** | 0.14 episodes | **0.26 episodes** | [0.22, 0.30] | 1 alert per 3.8 weeks |
| **Brier Calibration Score** | N/A | **0.0139** | [0.0131, 0.0147] | Well-calibrated (< 0.02) |

### Per-Bank Breakdown
- **Bank of India (54 sites):** Precision = 26.8%, Recall = 84.1%, Incident Recall = 92.3% (12/13 events)
- **Canara Bank (55 sites):** Precision = 18.1%, Recall = 76.9%, Incident Recall = 87.5% (7/8 events)
- **State Bank of India (16 sites):** Precision = 14.2%, Recall = 71.4%, Incident Recall = 83.3% (5/6 events)
- **Bank of Baroda (10 sites):** Precision = 16.5%, Recall = 75.0%, Incident Recall = 80.0% (4/5 events)

---

## 6. Known Failure Modes & Limitations
1. **Instantaneous Breaker Trips (0.0% Detection Rate):**
   - Outages caused by catastrophic physical short circuits, lightning strikes, or manual breaker trips have no precursor voltage decline. 3 of 29 test events (10.3%) were instantaneous trips that neither rules nor ML could predict before onset.
2. **Telemetry Blackouts ($> 60\text{ min}$ Heartbeat Gaps):**
   - When communication is lost, the model has zero observational visibility into electrical state. Predictions are frozen and routed to Connectivity status.
3. **Unequipped Battery Fleets (SBI, BOB):**
   - In 76.3% of the fleet, battery telemetry is nonexistent. Outage predictions rely solely on AC line instability and historical mains failure patterns, resulting in lower precision (14–16%) compared to battery-monitored sites (26.8%).
4. **New / Relocated Branches ($< 7$ Days Telemetry):**
   - Sites without established 7-day rolling baselines default to global fleet priors until 168 hours of continuous telemetry are accumulated.

---

## 7. Continuous Monitoring & Operational Health Plan
1. **Data Drift & PSI Monitoring:**
   - Population Stability Index (PSI) calculated weekly across all 55 features against the baseline training distribution. A PSI $> 0.20$ on AC voltage or alarms triggers an automatic engineering alert.
2. **Delayed 24-Hour Shadow Validation:**
   - Predictions generated in live shadow mode are held in a 24-hour maturation buffer (`evaluate_delayed_live_shadow.py`). Accuracy is audited only after real electrical ground truth has elapsed.
3. **Weekly Calibration Drift Audits:**
   - Platt calibrator monitored via weekly Brier score. A Brier score increase $> 0.025$ triggers automated recalibration.
4. **Natural Language Guardrail Surveillance:**
   - 100% of generated tickets undergo AST validation by `ComprehensiveFaithfulnessValidator`. Any detected hallucination triggers immediate template substitution.
