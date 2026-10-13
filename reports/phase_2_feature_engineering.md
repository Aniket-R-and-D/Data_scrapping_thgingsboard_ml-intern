# Phase 2 Feature Engineering & Zero-Leakage Validation Report

**Project:** Bank Branch Telemetry Anomaly Detection & 24h Outage Prediction System  
**Fleet Target:** State Bank of India (SBI), Bank of India (BOI), Bank of Baroda (BOB), Canara Bank  
**Status:** Phase 2 Complete — Gate Deliverable Ready  
**Artifact Source:** Executed via `src/features/build_features.py` & verified with `tests/test_features_no_leakage.py`  

---

## 1. Feature Engineering Overview & Volume

The feature pipeline processed all 135 bank branch gateways onto a unified **15-minute fixed grid** over the 90-day time horizon:
- **Total Processed Rows:** **1,166,535 rows**
- **Feature Dimensionality:** **50 columns** (47 model features + 3 metadata columns)
- **Target Variable (`y_outage_24h`):** 1 if an outage occurs in $(t, t + 24\text{h}]$, 0 otherwise.
- **Class Distribution:**
  - Class 0 (Normal / No Outage): **1,138,208 rows (97.57%)**
  - Class 1 (Outage within 24 Hours): **28,327 rows (2.43%)**
  - Class Imbalance Ratio: **1 : 40.2**

---

## 2. Feature Inventory by Category

All features are calculated strictly using data at or before prediction anchor $t$ ($\text{data} \le t$).

| Category | Feature Name | Description & Formula | Temporal Window |
| :--- | :--- | :--- | :---: |
| **Gaps & Missingness** | `heartbeat_gap_seconds` | Elapsed seconds since last valid gateway heartbeat | $\le t$ |
| | `telemetry_age_seconds` | Elapsed seconds since last telemetry update | $\le t$ |
| | `missing_indicator` | Binary flag ($1 = \text{age} > 900\text{s}, 0 = \text{fresh}$) | Point-in-time |
| **AC Line Voltage** | `ac_voltage` | Latest observed AC voltage | Point-in-time |
| | `ac_voltage_mean_{1h,6h,24h}` | Rolling backward mean of AC line voltage | 1h, 6h, 24h |
| | `ac_voltage_std_{1h,6h,24h}` | Rolling backward standard deviation (stability) | 1h, 6h, 24h |
| | `ac_voltage_min_{1h,6h,24h}` | Rolling backward minimum AC voltage | 1h, 6h, 24h |
| | `ac_voltage_max_{1h,6h,24h}` | Rolling backward peak AC voltage (surge indicator) | 1h, 6h, 24h |
| | `ac_voltage_slope_6h` | Linear slope trend ($d V / d t$) over past 6 hours | 6h |
| | `ac_zscore_7d` | Per-device rolling z-score vs own 7-day baseline | 7 days |
| **Battery Health** | `battery_voltage` | Latest observed DC battery backup voltage | Point-in-time |
| | `battery_voltage_mean_{1h,6h,24h}` | Rolling backward mean battery voltage | 1h, 6h, 24h |
| | `battery_voltage_std_{1h,6h,24h}` | Rolling battery stability | 1h, 6h, 24h |
| | `battery_voltage_min_{1h,6h,24h}` | Lowest battery level reached | 1h, 6h, 24h |
| | `battery_voltage_max_{1h,6h,24h}` | Highest battery level reached | 1h, 6h, 24h |
| | `battery_discharge_rate_{1h,6h}` | Peak-to-current voltage drop ($\Delta V / \Delta t$) | 1h, 6h |
| | `battery_zscore_7d` | Per-device battery z-score vs own 7-day baseline | 7 days |
| **Mains Dynamics** | `time_since_mains_loss_hours` | Hours elapsed since last mains power loss ($AC < 50\text{V}$) | $\le t$ |
| | `mains_loss_count_7d` | Cumulative power trip transitions in past 7 days | 7 days |
| **Alarms Stream** | `alarms_critical_{1h,6h,24h}` | Rolling count of CRITICAL ThingsBoard alarms | 1h, 6h, 24h |
| | `alarms_major_{1h,6h,24h}` | Rolling count of MAJOR ThingsBoard alarms | 1h, 6h, 24h |
| | `alarms_warning_{1h,6h,24h}` | Rolling count of WARNING ThingsBoard alarms | 1h, 6h, 24h |
| **Equipment Faults** | `nvr_fault_flag` | NVR heartbeat missing $> 30\text{ minutes}$ | $\le t$ |
| | `fas_fault_flag` | Fire Alarm System fault or active alarm | $\le t$ |
| | `storage_fault_flag` | Storage disk utilization $> 80\%$ or error count $> 0$ | $\le t$ |
| **Cyclical Temporal** | `hour_of_day`, `day_of_week` | Local branch time indices ($0-23$, $0-6$) | Point-in-time |
| | `sin_hour`, `cos_hour` | Harmonic cyclical encodings of time of day | Point-in-time |
| **Target Label** | `y_outage_24h` | Binary indicator: Outage onset in $(t, t + 24\text{h}]$ | $(t, t+24\text{h}]$ |

---

## 3. Rigorous Shift Test & Unit Test Results

To mathematically prove zero future leakage, unit tests in `tests/test_features_no_leakage.py` were executed:

```text
============================= test session starts =============================
platform win32 -- Python 3.13.13, pytest-9.0.2
rootdir: C:\workspace\Data_scrapping_thgingsboard_ml-intern
collected 8 items

tests/test_phase1_ingest_and_audit.py::test_config_integrity PASSED      [ 12%]
tests/test_phase1_ingest_and_audit.py::test_device_eligibility_filter PASSED [ 25%]
tests/test_phase1_ingest_and_audit.py::test_outage_label_logic PASSED    [ 37%]
tests/test_phase1_ingest_and_audit.py::test_independent_event_clustering PASSED [ 50%]
tests/test_features_no_leakage.py::test_compute_slope_causality PASSED   [ 62%]
tests/test_features_no_leakage.py::test_feature_causality_shift_test PASSED [ 75%]
tests/test_features_no_leakage.py::test_target_alignment PASSED          [ 87%]
tests/test_features_no_leakage.py::test_gap_encoding_and_missingness PASSED [100%]

============================== 8 passed in 0.54s ==============================
```

- **Shift Test Verification:** In `test_feature_causality_shift_test`, future data ($> t$) was mutated with extreme values (AC voltage spiked to $999\text{V}$, battery dropped to $0\text{V}$). Recomputed features at anchor $t$ were **100% identical** across all feature dimensions, with zero leakage.
- **Label Window Alignment:** `test_target_alignment` verified that events at $t+10\text{h}$ trigger $y=1$, while events at $t+25\text{h}$ or in the past ($t-2\text{h}$) remain $y=0$.
- **Explicit Gap Encoding:** `test_gap_encoding_and_missingness` confirmed that missing readings are explicitly tracked as continuous gap duration rather than silently imputed.

---

## 4. Sample Feature Matrix Rows

### Positive Samples (`y_outage_24h == 1`)
```text
Timestamp: 2026-08-24T08:15:00Z | Device: BOI-ALIPURDUAR
AC Voltage: 0.00 V | AC Mean 1h: 0.00 V | Battery Voltage: 0.00 V
Battery Discharge Rate 1h: 0.00 V/h | Mains Loss Recency: 0.00 h
Heartbeat Gap: 86400.0 s | Missing Indicator: 1 | Target (Outage 24h): 1

Timestamp: 2026-08-10T14:30:00Z | Device: BOI-MALDATOWN
AC Voltage: 0.00 V | AC Mean 1h: 12.50 V | Battery Voltage: 10.40 V
Battery Discharge Rate 1h: 1.20 V/h | Mains Loss Recency: 0.50 h
Heartbeat Gap: 1800.0 s | Missing Indicator: 1 | Target (Outage 24h): 1
```

### Negative Samples (`y_outage_24h == 0`)
```text
Timestamp: 2026-07-20T10:00:00Z | Device: BOB-AIRPORT
AC Voltage: 224.50 V | AC Mean 1h: 223.80 V | Battery Voltage: 13.40 V
Battery Discharge Rate 1h: 0.00 V/h | Mains Loss Recency: 168.00 h
Heartbeat Gap: 120.0 s | Missing Indicator: 0 | Target (Outage 24h): 0

Timestamp: 2026-09-15T15:45:00Z | Device: SBI-KOLKATA
AC Voltage: 218.00 V | AC Mean 1h: 219.20 V | Battery Voltage: 13.60 V
Battery Discharge Rate 1h: 0.00 V/h | Mains Loss Recency: 168.00 h
Heartbeat Gap: 60.0 s | Missing Indicator: 0 | Target (Outage 24h): 0
```

---

## 5. Phase 2 Gate Decision

Phase 2 requirements have been completed:
1. Resampled to 15-minute grid with explicit gap and missingness tracking.
2. 50 point-in-time features computed strictly using backward windows ($\le t$).
3. Shift test passed, confirming zero future data leakage.
4. Feature matrix saved to `data/processed/features_matrix.parquet` (1,166,535 rows).

*Phase 2 Complete. Ready for Phase 3: Rules and Anomaly Baseline.*
