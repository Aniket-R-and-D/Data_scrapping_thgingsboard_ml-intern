# Fleet-Wide Unsupervised Anomaly Detection Report (365-Day Dataset)

**Generated:** 2026-10-24 08:53:54 UTC  
**Source Matrix:** `training_data/anomaly_20261022_082325/anomaly_features.parquet`  
**Dataset Scope:** 140,219 time-anchored samples across 130 active bank branches (Full Year lookback)

---

## 1. Executive Summary & Calibration

Unsupervised multi-signal anomaly detection models (Isolation Forest + Standardized Mahalanobis Centroids) were trained on normal baseline periods across all 130 bank branches.

| Anomaly Tier | Threshold Cutoff | Total Fleet Samples | % of Full Year | Operational Action |
| :--- | :--- | :--- | :--- | :--- |
| **NORMAL** | Score < -0.014 | 132,489 | 94.5% | Routine operation; nominal grid/battery stability |
| **ELEVATED** | -0.014 <= Score < 0.011 | 4,004 | 2.9% | Watch-list; subtle electrical or uptime degradation |
| **ANOMALOUS** | 0.011 <= Score < 0.048 | 2,406 | 1.7% | Investigation recommended; multi-sensor deviation |
| **CRITICAL_FAULT** | Score >= 0.048 | 1,320 | 0.9% | Immediate maintenance alert; acute power/hardware crash |

---

## 2. Fleet Breakdown by Bank Group

| Bank Group | Total Monitored Rows | Elevated Incidents | Severe (Anomalous + Critical) | Incident Rate |
| :--- | :--- | :--- | :--- | :--- |
| **BOI** | 112,536 | 3,640 | 2,169 | **1.93%** |
| **SBI** | 11,672 | 0 | 0 | **0.00%** |
| **BOB** | 11,978 | 114 | 248 | **2.07%** |
| **CANARA** | 2,104 | 94 | 940 | **44.68%** |

---

## 3. Top-20 Faultiest Bank Branches (365-Day Ranking)

These branches exhibit the highest persistent multi-signal anomalies and baseline deviations across the entire year:

| Rank | Bank | Device ID | Severe Anomalies (24h/6h) | Primary Root Causes |
| :---: | :---: | :--- | :---: | :--- |
| **1** | CANARA | `0b7bc7d0-671d-11f1-8c55-5bb8284c6aef` | **401** | system_current__gap_max_7d (z=100.2); battery_voltage__gap_max_7d (z=100.2); ac_voltage__gap_max_7d (z=100.2) |
| **2** | CANARA | `6910ecc0-671f-11f1-8c55-5bb8284c6aef` | **271** | ac_voltage__gap_max_7d (z=100.8); system_current__gap_max_7d (z=100.8); battery_voltage__gap_max_7d (z=100.8) |
| **3** | DX | `630c1480-63e6-11f1-9555-295f91c5b7c8` | **250** | ac_voltage__gap_max_7d (z=99.7); battery_voltage__gap_max_7d (z=99.7); system_current__gap_max_7d (z=99.7) |
| **4** | BOI | `57db9c90-b89a-11f0-87ea-b9809c1a7a9e` | **225** | usage_last_15_days__std_1h (z=8.8); usage_last_15_days__max_1h (z=8.6); usage_last_15_days__std_6h (z=8.5) |
| **5** | BOI | `0ad74590-b875-11f0-b150-2710a8915e1d` | **168** | memory__std_24h (z=23.8); errorCount__z24h_vs_28d (z=22.8); iasLastHour__z24h_vs_28d (z=22.3) |
| **6** | CANARA | `0e4358d0-6708-11f1-8c55-5bb8284c6aef` | **133** | ac_voltage__gap_max_7d (z=236.1); battery_voltage__gap_max_7d (z=236.1); system_current__gap_max_7d (z=236.1) |
| **7** | CANARA | `388f1380-31ad-11f1-9c6f-b71bb2771567` | **119** | ac_voltage__max_1h (z=7.6); ac_voltage__mean_1h (z=7.6); ac_voltage__min_1h (z=7.6) |
| **8** | BOI | `6287e400-a823-11f0-b150-2710a8915e1d` | **93** | accessControlLastHour__max_1h (z=42.5); accessControlLastHour__min_1h (z=42.5); accessControlLastHour__mean_1h (z=42.5) |
| **9** | BOI | `cbca57c0-a8e8-11f0-91df-7ffa16af2ee9` | **88** | uptimeTotal__gap_max_7d (z=5.7); fasLastHour__gap_max_7d (z=5.4); timeLockLastHour__gap_max_7d (z=5.3) |
| **10** | BOI | `540b38d0-b888-11f0-b4af-b334f2239a02` | **83** | uptimeTotal__gap_max_7d (z=5.7); fasLastHour__gap_max_7d (z=5.4); timeLockLastHour__gap_max_7d (z=5.3) |
| **11** | BOI | `0bfa8fa0-c067-11f0-a562-d9639f025684` | **79** | system_current__min_6h (z=12.0); system_current__min_24h (z=10.5); system_current__mean_6h (z=10.2) |
| **12** | BOI | `5bb79740-c51c-11f0-ae8f-9fad3c067e6e` | **72** | BAS_Uptime_Minutes__max_168h (z=63.0); BAS_Uptime_Minutes__mean_168h (z=63.0); BAS_Uptime_Minutes__min_168h (z=63.0) |
| **13** | BOI | `ef43bf30-aa77-11f0-b8ab-b38af83f6c9c` | **65** | usage_daily__std_1h (z=7.2); usage_last_7_days__std_1h (z=7.2); usage_daily__max_1h (z=7.1) |
| **14** | DX | `6aac1ab0-63f4-11f1-9555-295f91c5b7c8` | **60** | ac_voltage__gap_max_7d (z=95.5); system_current__gap_max_7d (z=95.5); battery_voltage__gap_max_7d (z=95.5) |
| **15** | BOI | `668a4210-ab47-11f0-b150-2710a8915e1d` | **48** | ac_voltage__max_1h (z=7.6); ac_voltage__mean_1h (z=7.6); ac_voltage__min_1h (z=7.6) |
| **16** | BOI | `e2275560-bade-11f0-91ad-09ca52d2dd91` | **47** | temperature__count_1h (z=13.6); memory__count_1h (z=13.5); frequency__count_1h (z=13.5) |
| **17** | BOI | `6c671990-c200-11f0-bb20-a78f8ac460b7` | **47** | errorCount__max_6h (z=29.2); errorCount__std_6h (z=28.3); errorCount__mean_6h (z=25.0) |
| **18** | BOI | `7b0a1200-a5d5-11f0-b4af-b334f2239a02` | **46** | basLastHour__std_6h (z=7.9); timeLockLastHour__std_6h (z=7.9); fasLastHour__std_6h (z=7.7) |
| **19** | BOI | `af53feb0-a5b0-11f0-b8ab-b38af83f6c9c` | **46** | basLastHour__std_6h (z=10.1); timeLockLastHour__std_6h (z=10.0); cctvLastHour__std_6h (z=8.1) |
| **20** | BOI | `dc0a38d0-ab31-11f0-87ea-b9809c1a7a9e` | **43** | uptimeTotal__gap_max_7d (z=5.7); fasLastHour__gap_max_7d (z=5.4); timeLockLastHour__gap_max_7d (z=5.3) |
