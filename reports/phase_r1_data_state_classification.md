# Phase R1: Data State Classification & Sentinel Forensics Report

**Project:** ThingsBoard Telemetry Anomaly Detection & 24-Hour Outage Prediction  
**Fleet:** 135 Commercial Bank Branch Sites (BOI, SBI, BOB, Canara)  
**Phase:** R1 — Data State Classification & Sentinel Disentanglement  
**Date:** October 13, 2026  
**Status:** COMPLETE — GATE REACHED  

---

## 1. Executive Summary

In Phase R1, we executed a forensic audit of raw telemetry sentinels, missingness patterns, and communication gaps across all 135 eligible bank branch sites. We identified and corrected the root causes behind Issue B:

1. **Battery Sentinel Revelation:** Out of 46,311 raw battery telemetry points, **23,344 points (50.41%) are literal `0.0 V`**. Only **32 out of 135 devices (23.7%)** actually have working physical battery sensors installed. 64 devices (47.4%) report flat `0.0 V` sentinels continuously, and 39 devices (28.9%) have no battery telemetry files. An operating IoT gateway powered by a 0.0 V battery is physical nonsense; these zeros represent unequipped/unmonitored sensor channels.
2. **AC Line Voltage Breakdown:** AC voltage sensors are active on **97 of 135 devices (71.9%)**. Out of 63,118 raw AC readings, 12,497 (19.8%) are genuine `0.0 V` observations recorded while the gateway was actively reporting, corresponding to true utility grid power losses.
3. **Legacy Confounding Impact:** In the legacy pipeline, missing battery and AC readings were filled with `0.0 V`. Consequently, `battery < 10.8 V` evaluated to `True` 100% of the time across 76.3% of the fleet.
4. **State Machine Audit Result:** Applying our newly developed physical 4-state classification machine across all 1,166,535 rows revealed that **73.77% of the legacy positive "outage" labels occurred when devices were in `STALE_DATA` or `OFFLINE_LONG` states**, proving that the legacy model was predominantly predicting communication loss rather than utility power failures.

---

## 2. Forensic Sentinel Audit

*Source code: `src/features/audit_sentinels.py` | Artifact: `reports/phase_r1_sentinel_audit.json`*

### Fleet Sensor Instrumentation Matrix

| Fleet / Bank | Total Devices | Active Battery Sensors ($>0.5\text{ V}$) | Unequipped / Flat Zero Battery ($0.0\text{ V}$) | Missing Battery File | Active AC Sensors ($>5\text{ V}$) |
|---|---|---|---|---|---|
| **Bank of India (BOI)** | 104 | 31 (29.8%) | 55 (52.9%) | 18 (17.3%) | 84 (80.8%) |
| **State Bank of India (SBI)** | 16 | 0 (0.0%) | 7 (43.8%) | 9 (56.2%) | 0 (0.0%)* |
| **Bank of Baroda (BOB)** | 10 | 0 (0.0%) | 2 (20.0%) | 8 (80.0%) | 8 (80.0%) |
| **Canara Bank (CANARA)** | 5 | 1 (20.0%) | 0 (0.0%) | 4 (80.0%) | 5 (100.0%) |
| **Total Fleet** | **135** | **32 (23.7%)** | **64 (47.4%)** | **39 (28.9%)** | **97 (71.9%)** |

*\*Note: SBI devices do not instrument line voltage; they report discrete binary indicators (`power_sts`, `bat_sts`) and alarm events.*

### Raw Telemetry Value Distributions

| Signal | Total Raw Points | Literal Zero Points | True Non-Zero Points | Physical Interpretation of Zeros |
|---|---|---|---|---|
| **Battery Voltage** | 46,311 | 23,344 (50.41%) | 22,967 (49.59%) | **Unequipped ADC / Missing Sensor Channel** (Must be treated as `NaN`) |
| **AC Line Voltage** | 63,118 | 12,497 (19.80%) | 50,621 (80.20%) | **Genuine Utility Power Loss** when gateway is active ($220\text{V} \to 0\text{V}$) |

---

## 3. The Physical 4-State Machine Specification

We implemented `src/features/state_machine.py` with deterministic physical boundaries:

```
[Incoming Telemetry Snapshot at time t]
                   │
                   ▼
       Is Telemetry Age > 24 Hours?
         ├── YES ───────────────────────────> [OFFLINE_LONG]
         └── NO
              │
              ▼
       Is Telemetry Age > 60 Minutes?
         ├── YES ───────────────────────────> [STALE_DATA]
         └── NO (Gateway is Online & Reporting)
              │
              ▼
   Does Physical Electrical Loss Exist?
   - AC Voltage < 50V (where equipped)
   - Mains Indicator == OFF (where equipped)
   - Active POWER OFF / BATTERY LOW Alarm
   - Battery Discharging < 10.8V (where equipped)
         ├── YES ───────────────────────────> [ONLINE_POWER_LOSS]
         └── NO ────────────────────────────> [ONLINE_OK]
```

### Configurable State Thresholds (`configs/config.yaml`):
- `stale_threshold_seconds`: 3,600 s (60 minutes).
- `offline_threshold_seconds`: 86,400 s (24 hours).
- `ac_outage_volts`: 50.0 V.
- `battery_critical_volts`: 10.8 V (evaluated only when $V_{\text{batt}} > 0.5\text{ V}$).

---

## 4. Fleet-Wide State Distribution & Legacy Label Audit

*Source code: `src/features/evaluate_state_distribution.py` | Artifact: `reports/phase_r1_state_distribution.json`*

### Fleet State Distribution (1,166,535 Rows)

| Operational State | Total Rows | Fleet Share (%) | Operational Significance |
|---|---|---|---|
| **`ONLINE_OK`** | 113,955 | 9.77% | Branch verified powered and operating nominally |
| **`ONLINE_POWER_LOSS`** | 46,767 | 4.01% | True physical power failure confirmed by active sensors |
| **`STALE_DATA`** | 524,103 | 44.93% | Communication delayed (1h–24h); power status uncertain |
| **`OFFLINE_LONG`** | 481,710 | 41.29% | Gateway disconnected ($>24$h); link or breaker severed |
| **Total** | **1,166,535** | **100.00%** | Comprehensive fleet monitoring coverage |

### Audit of Legacy Positive Labels (`y_outage_24h == 1`)

Out of 28,311 positive outage labels assigned by the legacy Phase 1 pipeline:

| Actual State at Prediction Time | Legacy Positives Count | Share of Legacy Positives (%) | Finding |
|---|---|---|---|
| **`ONLINE_POWER_LOSS`** | 6,272 | 22.15% | **Genuine physical power outages** |
| **`ONLINE_OK`** | 1,153 | 4.07% | False positives / transition boundaries |
| **`STALE_DATA`** | 10,159 | 35.88% | **Communication stalled (not verified power failure)** |
| **`OFFLINE_LONG`** | 10,727 | 37.89% | **Prolonged gateway disconnection (not verified power failure)** |
| **Total Stale / Offline** | **20,886** | **73.77%** | **73.77% of legacy labels were invalid!** |

---

## 5. Concrete Real-World Examples from the Fleet

### Example 1: `ONLINE_OK` (Nominal Utility Power)
- **Branch:** `BOI-BURDWAN` (BOI)
- **Timestamp:** `2025-08-15T04:00:00Z`
- **Telemetry Snapshot:** Heartbeat Gap: `180.0 s`, AC Voltage: `226.4 V`, Battery Voltage: `13.8 V`.
- **Legacy Label:** $y = 0$.
- **State Machine Classification:** **`ONLINE_OK`**.

### Example 2: `ONLINE_POWER_LOSS` (Genuine Physical Grid Outage)
- **Branch:** `BOI-COOCHBEHAR` (BOI)
- **Timestamp:** `2025-08-22T11:15:00Z`
- **Telemetry Snapshot:** Heartbeat Gap: `300.0 s`, AC Voltage: `0.0 V`, Battery Voltage: `11.2 V` (discharging under load).
- **Legacy Label:** $y = 1$.
- **State Machine Classification:** **`ONLINE_POWER_LOSS`**.

### Example 3: `STALE_DATA` (Cellular / Gateway Stalled)
- **Branch:** `SBI-REGIONAL-HUB` (SBI)
- **Timestamp:** `2025-09-01T08:30:00Z`
- **Telemetry Snapshot:** Heartbeat Gap: `7,200.0 s` (2.0 hours), AC Voltage: `0.0 V` (unequipped fill), Battery Voltage: `0.0 V` (unequipped fill).
- **Legacy Label:** $y = 1$ (FALSE POSITIVE).
- **State Machine Classification:** **`STALE_DATA`**.

### Example 4: `OFFLINE_LONG` (Prolonged Disconnection)
- **Branch:** `BOI-BARIPADA-CC` (BOI)
- **Timestamp:** `2025-09-10T14:00:00Z`
- **Telemetry Snapshot:** Heartbeat Gap: `172,800.0 s` (48 hours), AC Voltage: `0.0 V`, Battery Voltage: `0.0 V`.
- **Legacy Label:** $y = 1$ (FALSE POSITIVE).
- **State Machine Classification:** **`OFFLINE_LONG`**.

---

## 6. Unit Testing & Verification

Unit tests were created in `tests/test_phase_r1_state_classification.py` covering:
- Nominal `ONLINE_OK` conditions.
- Grid loss detection (`ONLINE_POWER_LOSS`) via AC zero, mains off, and active alarms.
- **Unequipped battery protection:** Asserting that a device with `battery = NaN` or `battery = 0.0 V` remains `ONLINE_OK` when AC is normal.
- Stale threshold transitions ($> 3,600$ s).
- Long offline transitions ($> 86,400$ s).
- Exact parity between vectorized array evaluation and scalar classification.

```text
tests/test_phase_r1_state_classification.py::test_state_online_ok_nominal PASSED
tests/test_phase_r1_state_classification.py::test_state_online_power_loss_ac_zero PASSED
tests/test_phase_r1_state_classification.py::test_state_online_power_loss_alarm_active PASSED
tests/test_phase_r1_state_classification.py::test_state_unequipped_battery_remains_online_ok PASSED
tests/test_phase_r1_state_classification.py::test_state_stale_data_transition PASSED
tests/test_phase_r1_state_classification.py::test_state_offline_long_transition PASSED
tests/test_phase_r1_state_classification.py::test_vectorized_matches_scalar PASSED
============================== 7 passed in 0.54s ==============================
```

---

## 7. Phase R1 Sign-Off Checklist

- [x] Forensic sentinel zero patterns audited across all 135 sites and 4 banking fleets.
- [x] Unequipped battery channels identified (50.41% zeros; only 32 devices equipped).
- [x] State machine module created in `src/features/state_machine.py`.
- [x] Sentinel zeros replaced with `NaN` in feature logic; silent fills eliminated.
- [x] State distribution computed on 1,166,535 rows and legacy label confounding quantified (73.77%).
- [x] Unit test suite passing with 100% success rate.
