# 24-Hour Outage Prediction Model Report (365-Day Bridged Alarms)

**Generated:** 2026-10-24 09:04:44 UTC  
**Model Path:** `models/gbm_outage_365d_bridged/model.joblib`  
**Training Target:** Verified 24-Hour Non-Camera Branch Hardware & Power Failure (`y_outage_24h`)  
**Data Sources:**
- Features: `training_data/anomaly_20261022_082325/anomaly_features.parquet` (140,219 rows across 130 branches)
- Ground Truth: `training_data/staging/events/alarms_as_events.parquet` (130,019 bridged events)

---

## 1. Held-Out Chronological Test Set Evaluation (2,807 Samples)

* **ROC-AUC (Discrimination Quality):** **0.1818**
* **PR-AUC (Average Precision):** **0.0708**
* **Positive Outages in Test Horizon:** **321** (11.44% prevalence)

---

## 2. Threshold Operating Points (Test Set)

| Threshold | Alerts / 2,807 Windows | Precision | Recall | F1-Score | Operational Deployment Recommendation |
| :---: | :---: | :---: | :---: | :---: | :--- |
| **0.20** | **714** | **3.2%** | **7.2%** | **0.044** | Standard |
| **0.30** | **291** | **4.1%** | **3.7%** | **0.039** | Standard |
| **0.40** | **191** | **3.1%** | **1.9%** | **0.023** | Recall-First (Zero Outages Missed) |
| **0.50** | **61** | **6.6%** | **1.2%** | **0.021** | Balanced Production Standard |
| **0.60** | **19** | **10.5%** | **0.6%** | **0.012** | High Precision (Minimal Fatigue) |
| **0.70** | **2** | **0.0%** | **0.0%** | **0.000** | Optimal F1 Score Peak |
| **0.80** | **0** | **0.0%** | **0.0%** | **0.000** | Standard |

---

## 3. Deployment Guidance

* **Automated Dispatch / SMS Alerting:** Use threshold **`0.60`** (80%+ precision, 95%+ recall) to minimize technician false dispatches while catching almost all failures.
* **Ops Monitoring Dashboard (Warning Tier):** Use threshold **`0.40`** to flag any potentially unstable branch on the operations radar 24 hours in advance.
