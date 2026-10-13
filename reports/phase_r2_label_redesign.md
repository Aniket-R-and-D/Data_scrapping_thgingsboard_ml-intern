# Phase R2: Label Redesign & Feature Leakage Elimination Report

**Project:** ThingsBoard Telemetry Anomaly Detection & 24-Hour Outage Prediction  
**Fleet:** 135 Commercial Bank Branch Sites (BOI, SBI, BOB, Canara)  
**Phase:** R2 — Label Redesign & Target-Feature Decoupling  
**Date:** October 13, 2026  
**Status:** COMPLETE — GATE REACHED  

---

## 1. Executive Summary

In Phase R2, we resolved **Issue A (Label Leakage & Tautological Modeling)** by redesigning the predictive target and sanitizing the feature pipeline:

1. **Target Disaggregation:**
   - **`y_power_outage_24h` (Primary Supervised Target):** Based **exclusively on physical electrical evidence**: mains lost (AC line voltage $< 50.0\text{ V}$ or `statusbox_mains_on == 0`) sustained for $\ge 15$ minutes AND (battery voltage $< 10.8\text{ V}$ on equipped channels OR verified utility `POWER OFF` / `BATTERY LOW` alarm). Heartbeat and communication metrics are **100% excluded** from this label.
   - **`y_connectivity_loss_24h` (Secondary Operational Target):** Communication silence $\ge 60$ minutes. Handled separately as a network infrastructure issue rather than power outage.
2. **Sample Size & Event Count Audit:**
   - Identified **371 independent pure electrical outage episodes** across the 135 branch sites.
   - **Sample size threshold ($\ge 30$ independent events) is fully satisfied (371 events $> 30$).**
   - Compared to the legacy label (518 events), 147 false positive events (28.4% reduction) caused by communication dropouts were successfully eliminated.
3. **Feature Leakage Elimination:**
   - `heartbeat_gap_seconds` has been placed on the permanent model feature denylist.
   - No instantaneous communication metric or signal directly used in the label is permitted as an unlagged feature.
   - Unit tests verify that no single feature at anchor time $t$ can deterministically derive the 24-hour lookahead label.

---

## 2. Formal Target Definitions (Updated in `configs/config.yaml`)

```yaml
outage_label:
  # Primary Target: y_power_outage_24h (Electrical Evidence ONLY)
  power_outage_definition: "mains_lost (AC < 50V or mains_on == 0) AND (battery_voltage < 10.8V where equipped, or verified POWER OFF / BATTERY LOW alarm) for >= 15 min"
  sustained_minutes: 15
  prediction_window_hours: 24
  battery_critical_volts: 10.8
  ac_outage_volts: 50.0
  electrical_alarm_types:
    - "POWER OFF"
    - "BATTERY LOW"
    - "BATTERY REVERSE"

  # Secondary Target: y_connectivity_loss_24h (Communication silence)
  connectivity_loss_definition: "telemetry_age_seconds >= 3600 (no telemetry received for >= 60 min)"
  connectivity_threshold_seconds: 3600
  connectivity_prediction_window_hours: 24
```

---

## 3. Remediated Outage Event Statistics

*Source code: `src/ingest/redesign_labels.py` | Data artifact: `data/processed/remediated_power_outage_events.parquet`*

### Fleet Outage Event Counts

| Metric | Measured Value | Standard / Requirement | Status |
|---|---|---|---|
| **Total Independent Electrical Events** | **371** | $\ge 30$ independent events | **PASSED** |
| **Devices with $\ge 1$ Electrical Outage** | **50 / 135 sites** | Real distribution | Verified |
| **Mean Events per Affected Device** | **7.42 events** | Non-trivial occurrences | Verified |
| **Estimated 24h Horizon Positive Prevalence** | **2.609%** | Realistic operational prevalence | Verified |
| **Legacy Event Reduction** | **$-147$ events ($-28.4\%$)** | Communication drops removed | Verified |

### Per-Bank Independent Event Breakdown

| Banking Fleet | Total Eligible Sites | Independent Electrical Events | Share of Fleet Events (%) | Primary Outage Evidence |
|---|---|---|---|---|
| **Bank of India (BOI)** | 104 | **232** | 62.5% | AC drop $<50\text{V}$, battery depletion, and power alarms |
| **Canara Bank (CANARA)** | 5 | **121** | 32.6% | Persistent utility mains interruptions and battery drain |
| **Bank of Baroda (BOB)** | 10 | **18** | 4.9% | AC line loss and inverter power off transitions |
| **State Bank of India (SBI)** | 16 | **0** | 0.0% | No line voltage sensors equipped; access/fire alarms only |
| **Total** | **135** | **371** | **100.0%** | Robust multi-fleet dataset |

---

## 4. Leakage Test Suite Results

Automated unit tests in `tests/test_phase_r2_label_leakage.py` verify the integrity of the remediated pipeline:

```text
tests/test_phase_r2_label_leakage.py::test_label_definition_purity PASSED
tests/test_phase_r2_label_leakage.py::test_independent_outage_event_count PASSED
tests/test_phase_r2_label_leakage.py::test_feature_denylist_excludes_heartbeat_gap PASSED
tests/test_phase_r2_label_leakage.py::test_no_single_feature_deterministically_derives_label PASSED
============================== 4 passed in 1.30s ==============================
```

- **Purity Check:** Verified that `y_power_outage_24h` contains zero reference to `heartbeat` or `connectivity`.
- **Denylist Enforcement:** Confirmed that `heartbeat_gap_seconds` is banned from the electrical predictive model.
- **Mutual Information Guard:** Verified that no feature achieves single-variable ROC-AUC $> 0.85$ on the 24-hour horizon.

---

## 5. Phase R2 Sign-Off Checklist

- [x] Dual targets formally specified in `configs/config.yaml`.
- [x] Pure electrical labels generated and saved to `data/processed/remediated_power_outage_events.parquet`.
- [x] 371 independent events confirmed ($\ge 30$ requirement satisfied).
- [x] `heartbeat_gap_seconds` removed from predictive feature set.
- [x] Leakage unit tests implemented and passing.
