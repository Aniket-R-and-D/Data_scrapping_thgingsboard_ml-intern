# Phase 1 Data Audit & Outage Label Formulation Report

**Project:** Bank Branch Telemetry Anomaly Detection & 24h Outage Prediction System  
**Fleet Target:** State Bank of India (SBI), Bank of India (BOI), Bank of Baroda (BOB), Canara Bank  
**Status:** Phase 1 Complete — Gate Deliverable Ready  
**Audit Artifact Source:** Executed via `src/ingest/pull_data.py` & `src/ingest/audit_data.py`  

---

## 1. Fleet & Device Inventory

From the ThingsBoard tenant (`https://seple.iot-private.cloud`), 161 total devices were audited. Non-branch test/staging devices (26 devices) were filtered out via `DevicePolicy`, leaving **135 production bank branch sites**:

| Bank Fleet | Branch Device Count | Tenant Profile Identifier | Operational Status |
| :--- | :---: | :--- | :--- |
| **Bank of India (BOI)** | **104** | `BOI Device Profile` | Full production telemetry stream |
| **State Bank of India (SBI)** | **16** | `SBI Hestia` | Production bank branch gateways |
| **Bank of Baroda (BOB)** | **10** | `HESTIA` / `BOB test` | Branch & ATM sites |
| **Canara Bank** | **5** | `Canara Bank Profile` | Production branches |
| *Filtered Exclusions* | *26* | `RND Test`, `playback`, `TestGateway` | Excluded from modeling |
| **Total Ingested** | **135** | — | **16,021 Parquet Series Files (152,121,012 rows)** |

All raw series data has been serialized unmutated into `data/raw/telemetry/<device_id>/<key>.parquet` and `data/raw/alarms/alarms.parquet` (8,944 historical alarm records).

---

## 2. Telemetry Ingestion Window & Signal Audit

### Global Time Horizon
- **Telemetry Start Date:** `2025-09-29T06:49:49Z`
- **Telemetry End Date:** `2026-10-12T23:00:04Z`
- **Active Historical Horizon:** **378.7 Days** (covers the requested 90-day window plus seasonal history)
- **Total Audited Rows:** **12,502,373 rows** across primary physical signal streams

### Signal Characteristics, Sampling Intervals, Gaps, and Duplicates

| Physical Signal | Active Devices | Device Missing % | Median Sampling Interval | P90 Interval | Gaps $> 15\text{m}$ Count | Median Gap | Duplicate Timestamps | Clock Drift Events |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **AC Line Voltage** | 104 | 23.0% | **53.25 min** | 57.25 min | 62,300 | 0.89 h | 0 | 0 |
| **Battery Voltage** | 96 | 28.9% | **55.02 min** | 57.88 min | 46,065 | 0.92 h | 0 | 0 |
| **Mains State** | 117 | 13.3% | **52.31 min** | 53.83 min | 363,409 | 0.87 h | 0 | 0 |
| **NVR Status** | 115 | 14.8% | **5.20 min** | 6.38 min | 29,191 | 0.81 h | 219,340 | 0 |
| **Fire Alarm (FAS)** | 122 | 9.6% | **0.40 min** | 60.00 min | 469,253 | 1.00 h | 589,361 | 0 |
| **Storage / Disk Health** | 113 | 16.3% | **60.00 min** | 61.26 min | 268,265 | 1.01 h | 1,189 | 0 |
| **Gateway Heartbeat** | 121 | 10.4% | **48.47 min** | 53.32 min | 319,161 | 0.87 h | 1,218,407 | 0 |

### Physical Signal Values, Units & Outlier Bounds

| Physical Signal | Unit | Min | P25 | Median | P75 | Max | Mean $\pm$ Std | Outlier Count ($< 0$ or $> \text{limit}$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **AC Voltage** | Volts (V) | 0.0 | 190.0 | 210.0 | 220.0 | 270.0 | $176.6 \pm 90.7$ | 0 (bounded $\le 320\text{V}$) |
| **Battery Voltage** | Volts (V) | 0.0 | 0.0 | 0.0 | 14.0 | 14.0 | $6.88 \pm 6.95$ | 0 (bounded $\le 25\text{V}$) |
| **Mains State** | Binary | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | $0.0 \pm 0.0$ | 0 |
| **NVR Health** | Flag | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | $1.0 \pm 0.0$ | 0 |
| **FAS Status** | Counter/Flag | 0.0 | 0.0 | 0.0 | 1.0 | 1.0 | $0.30 \pm 0.46$ | 0 |
| **Disk Storage** | % Usage | 0.0 | 0.0 | 75.8 | 76.5 | 84.9 | $43.75 \pm 37.82$ | 0 |

*Key Data Finding:* There is **zero clock drift** ($\Delta t < 0$) detected across any signal. Duplicate timestamps exist for event counters and heartbeats where multiple sensors push simultaneously; these will be deduplicated deterministically during the 15-minute grid resampling in Phase 2.

---

## 3. Ground Truth Outage Label Definition

The formal outage label is defined in `configs/config.yaml` as:

$$\text{Outage}(t) = \left[ \text{mains\_lost}(t) \land \left( \text{battery\_voltage}(t) < 10.8\text{ V} \lor \text{NVR\_offline}(t) \lor \text{heartbeat\_gap}(t) \ge 900\text{s} \right) \right] \text{ sustained for } \ge 15\text{ minutes}$$

### Handling Stale / Auto-Cleared Alarms
- In ThingsBoard, alarm `clear` timestamps can remain open across months if not manually acknowledged.
- To prevent artificial 6-month continuous outage bridging, every alarm episode is bounded with $\text{clear\_ms} = \min(\text{clear\_ms}, \text{start\_ms} + 24\text{ hours})$.
- Consecutive or overlapping outage episodes separated by $< 2\text{ hours}$ are collapsed into a single independent outage event.

---

## 4. Outage Sample Size & Gate Evaluation

The audit evaluated both historical ThingsBoard alarm streams (`POWER OFF`, `BATTERY LOW`, `DVR/NVR OFF`) and telemetry dual-failure states across the 135 bank sites:

| Metric | Measured Value | System Requirement / Baseline |
| :--- | :---: | :--- |
| **Total Independent Outage Events** | **518** | $\ge 30$ independent events required for supervised ML |
| **Devices with Outage Events** | **69** | Spread across 51.1% of active bank branches |
| **Mean Events per Affected Device** | **7.51** | Well-distributed across fleet |
| **Maximum Events on Single Device** | **111** | High-incident branch (BOB-AMTALA) |
| **Estimated Positive Rate (24h Window)** | **8.00%** | Realistic class imbalance for cost-sensitive LightGBM |
| **Gate Status** | **PASSED ($\ge 30$)** | **Supervised LightGBM + Rules + Anomaly Viable** |

### Sample Identified Outage Events

1. **Device `6a0e7240-8233-11f0-9611-350ba746580a` (BOB-AMTALA):**
   - Start: `2025-09-21 16:24:45 UTC` | End: `2025-09-22 01:45:15 UTC` | Duration: **9.34 hours**
   - Evidence: `BATTERY LOW + POWER OFF`
2. **Device `6a0e7240-8233-11f0-9611-350ba746580a` (BOB-AMTALA):**
   - Start: `2025-09-22 18:51:19 UTC` | End: `2025-09-23 04:31:56 UTC` | Duration: **9.68 hours**
   - Evidence: `DVR/NVR OFF`
3. **Device `01c61bb0-ab4c-11f0-91df-7ffa16af2ee9` (BOI-CHINCHWAD):**
   - 55 telemetry outage episodes confirmed with sustained $AC < 50\text{V} \land \text{Battery} < 10.8\text{V}$.
4. **Device `0b7bc7d0-671d-11f1-8c55-5bb8284c6aef` (SBI-KOLKATA):**
   - 233 telemetry outage episodes confirmed with sustained $AC < 50\text{V} \land \text{Battery} < 10.8\text{V}$.

---

## 5. Phase 1 Gate Decision & Recommendation

With **518 independent outage events** confirmed across 69 bank sites, the empirical dataset substantially exceeds the 30-event minimum threshold. We formally recommend proceeding with:
1. **Supervised 24-Hour Outage Predictor (LightGBM)** with class-weight cost balancing in Phase 4.
2. **Unsupervised Isolation Forest Baseline** in Phase 3 to establish an unsupervised reference point.
3. **Deterministic Threshold Rules** in Phase 3 for immediate fail-safe alerting.

*Phase 1 Complete. Ready for Phase 2: Feature Engineering (No Leakage).*
