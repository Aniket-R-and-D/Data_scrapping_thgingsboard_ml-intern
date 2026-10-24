# Operations Review Pack: Top-20 Faultiest Bank Branches (365-Day Analysis)

**Document ID:** `OPS-REVIEW-PACK-365D-v1.0`  
**Generated:** 2026-10-24 08:54:59 UTC  
**Source Dataset:** 140,219 time-anchored windows, 130 branches across 365 days (`2025-10-22` to `2026-10-22`)  
**Objective:** Operational intervention pack for Seple field engineers and bank facility managers to triage chronic brownouts, battery failures, and controller drops.

---

## 1. Executive Summary Table

| Rank | Bank | Branch Name / Device | Severe Anomalies | 1-Year Alarms | Key Electrical State | Recommended Field Action |
| :---: | :---: | :--- | :---: | :---: | :--- | :--- |
| **1** | CANARA | **CANARA-BARUIPUR**<br>`0b7bc7d0-671d-11f1-8c55-5bb8284c6aef` | **401** | 219 | AC: 0.7V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **2** | CANARA | **CANARA-HALDI2**<br>`6910ecc0-671f-11f1-8c55-5bb8284c6aef` | **271** | 4 | AC: 1.1V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **3** | DX | **DX2**<br>`630c1480-63e6-11f1-9555-295f91c5b7c8` | **250** | 167 | AC: 0.8V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **4** | BOI | **BOI-BARIPADA-CC**<br>`57db9c90-b89a-11f0-87ea-b9809c1a7a9e` | **225** | 376 | AC: 0.8V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **5** | BOI | **BOI-BPADA-MAIN**<br>`0ad74590-b875-11f0-b150-2710a8915e1d` | **168** | 8 | AC: 0.5V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **6** | CANARA | **CANARA-KAMARHATI**<br>`0e4358d0-6708-11f1-8c55-5bb8284c6aef` | **133** | 30 | AC: 0.6V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **7** | CANARA | **CANARA-CHETLA**<br>`388f1380-31ad-11f1-9c6f-b71bb2771567` | **119** | 152 | AC: -1.0V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **8** | BOI | **BOI-SALTLAKE**<br>`6287e400-a823-11f0-b150-2710a8915e1d` | **93** | 105 | AC: -1.3V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **9** | BOI | **BOI-TELCO-TOWN**<br>`cbca57c0-a8e8-11f0-91df-7ffa16af2ee9` | **88** | 78 | AC: 0.6V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **10** | BOI | **BOI-KRISHNANAGAR**<br>`540b38d0-b888-11f0-b4af-b334f2239a02` | **83** | 10 | AC: 0.8V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **11** | BOI | **BOI-BAGULA**<br>`0bfa8fa0-c067-11f0-a562-d9639f025684` | **79** | 116 | AC: 0.5V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **12** | BOI | **BOI-SITHIO**<br>`5bb79740-c51c-11f0-ae8f-9fad3c067e6e` | **72** | 101 | AC: -1.5V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **13** | BOI | **BOI-BAGUIHATI**<br>`ef43bf30-aa77-11f0-b8ab-b38af83f6c9c` | **65** | 641 | AC: -1.1V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **14** | DX | **DX7**<br>`6aac1ab0-63f4-11f1-9555-295f91c5b7c8` | **60** | 251 | AC: -1.5V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **15** | BOI | **BOI-BALURGHAT**<br>`668a4210-ab47-11f0-b150-2710a8915e1d` | **48** | 393 | AC: -1.4V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **16** | BOI | **BOI-SATPUR**<br>`e2275560-bade-11f0-91ad-09ca52d2dd91` | **47** | 101 | AC: 220.0V<br>Bat Min: 12.0V | General Maintenance: Complete quarterly preventative maintenance inspection. |
| **17** | BOI | **BOI-NOKARI**<br>`6c671990-c200-11f0-bb20-a78f8ac460b7` | **47** | 89 | AC: -1.4V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **18** | BOI | **BOI-CHINSURAH**<br>`7b0a1200-a5d5-11f0-b4af-b334f2239a02` | **46** | 26 | AC: 0.5V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **19** | BOI | **BOI-ARAMBAGH**<br>`af53feb0-a5b0-11f0-b8ab-b38af83f6c9c` | **46** | 103 | AC: 0.7V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |
| **20** | BOI | **BOI-ISLAMPUR**<br>`dc0a38d0-ab31-11f0-87ea-b9809c1a7a9e` | **43** | 93 | AC: -1.3V<br>Bat Min: -0.9V | Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test. |

---

## 2. Detailed Branch Triage & Ops Review Forms

### Branch #1: CANARA-BARUIPUR (CANARA)
* **Device ID:** `0b7bc7d0-671d-11f1-8c55-5bb8284c6aef`
* **Severe 6h Anomaly Intervals (Year):** **401** (Max Anomaly Score: `0.0761`)
* **Total Historic Alarms in 365 Days:** **219**
* **Observed Metrics:** Mean AC Voltage = `0.7 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `system_current__gap_max_7d (z=100.2); battery_voltage__gap_max_7d (z=100.2); ac_voltage__gap_max_7d (z=100.2)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #2: CANARA-HALDI2 (CANARA)
* **Device ID:** `6910ecc0-671f-11f1-8c55-5bb8284c6aef`
* **Severe 6h Anomaly Intervals (Year):** **271** (Max Anomaly Score: `0.0809`)
* **Total Historic Alarms in 365 Days:** **4**
* **Observed Metrics:** Mean AC Voltage = `1.1 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `ac_voltage__gap_max_7d (z=100.8); system_current__gap_max_7d (z=100.8); battery_voltage__gap_max_7d (z=100.8)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #3: DX2 (DX)
* **Device ID:** `630c1480-63e6-11f1-9555-295f91c5b7c8`
* **Severe 6h Anomaly Intervals (Year):** **250** (Max Anomaly Score: `0.0604`)
* **Total Historic Alarms in 365 Days:** **167**
* **Observed Metrics:** Mean AC Voltage = `0.8 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `ac_voltage__gap_max_7d (z=99.7); battery_voltage__gap_max_7d (z=99.7); system_current__gap_max_7d (z=99.7)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #4: BOI-BARIPADA-CC (BOI)
* **Device ID:** `57db9c90-b89a-11f0-87ea-b9809c1a7a9e`
* **Severe 6h Anomaly Intervals (Year):** **225** (Max Anomaly Score: `0.106`)
* **Total Historic Alarms in 365 Days:** **376**
* **Observed Metrics:** Mean AC Voltage = `0.8 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `usage_last_15_days__std_1h (z=8.8); usage_last_15_days__max_1h (z=8.6); usage_last_15_days__std_6h (z=8.5)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #5: BOI-BPADA-MAIN (BOI)
* **Device ID:** `0ad74590-b875-11f0-b150-2710a8915e1d`
* **Severe 6h Anomaly Intervals (Year):** **168** (Max Anomaly Score: `0.1562`)
* **Total Historic Alarms in 365 Days:** **8**
* **Observed Metrics:** Mean AC Voltage = `0.5 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `memory__std_24h (z=23.8); errorCount__z24h_vs_28d (z=22.8); iasLastHour__z24h_vs_28d (z=22.3)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #6: CANARA-KAMARHATI (CANARA)
* **Device ID:** `0e4358d0-6708-11f1-8c55-5bb8284c6aef`
* **Severe 6h Anomaly Intervals (Year):** **133** (Max Anomaly Score: `0.094`)
* **Total Historic Alarms in 365 Days:** **30**
* **Observed Metrics:** Mean AC Voltage = `0.6 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `ac_voltage__gap_max_7d (z=236.1); battery_voltage__gap_max_7d (z=236.1); system_current__gap_max_7d (z=236.1)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #7: CANARA-CHETLA (CANARA)
* **Device ID:** `388f1380-31ad-11f1-9c6f-b71bb2771567`
* **Severe 6h Anomaly Intervals (Year):** **119** (Max Anomaly Score: `0.0491`)
* **Total Historic Alarms in 365 Days:** **152**
* **Observed Metrics:** Mean AC Voltage = `-1.0 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `ac_voltage__max_1h (z=7.6); ac_voltage__mean_1h (z=7.6); ac_voltage__min_1h (z=7.6)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #8: BOI-SALTLAKE (BOI)
* **Device ID:** `6287e400-a823-11f0-b150-2710a8915e1d`
* **Severe 6h Anomaly Intervals (Year):** **93** (Max Anomaly Score: `0.1176`)
* **Total Historic Alarms in 365 Days:** **105**
* **Observed Metrics:** Mean AC Voltage = `-1.3 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `accessControlLastHour__max_1h (z=42.5); accessControlLastHour__min_1h (z=42.5); accessControlLastHour__mean_1h (z=42.5)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #9: BOI-TELCO-TOWN (BOI)
* **Device ID:** `cbca57c0-a8e8-11f0-91df-7ffa16af2ee9`
* **Severe 6h Anomaly Intervals (Year):** **88** (Max Anomaly Score: `0.141`)
* **Total Historic Alarms in 365 Days:** **78**
* **Observed Metrics:** Mean AC Voltage = `0.6 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `uptimeTotal__gap_max_7d (z=5.7); fasLastHour__gap_max_7d (z=5.4); timeLockLastHour__gap_max_7d (z=5.3)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #10: BOI-KRISHNANAGAR (BOI)
* **Device ID:** `540b38d0-b888-11f0-b4af-b334f2239a02`
* **Severe 6h Anomaly Intervals (Year):** **83** (Max Anomaly Score: `0.0853`)
* **Total Historic Alarms in 365 Days:** **10**
* **Observed Metrics:** Mean AC Voltage = `0.8 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `uptimeTotal__gap_max_7d (z=5.7); fasLastHour__gap_max_7d (z=5.4); timeLockLastHour__gap_max_7d (z=5.3)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #11: BOI-BAGULA (BOI)
* **Device ID:** `0bfa8fa0-c067-11f0-a562-d9639f025684`
* **Severe 6h Anomaly Intervals (Year):** **79** (Max Anomaly Score: `0.0488`)
* **Total Historic Alarms in 365 Days:** **116**
* **Observed Metrics:** Mean AC Voltage = `0.5 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `system_current__min_6h (z=12.0); system_current__min_24h (z=10.5); system_current__mean_6h (z=10.2)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #12: BOI-SITHIO (BOI)
* **Device ID:** `5bb79740-c51c-11f0-ae8f-9fad3c067e6e`
* **Severe 6h Anomaly Intervals (Year):** **72** (Max Anomaly Score: `0.0946`)
* **Total Historic Alarms in 365 Days:** **101**
* **Observed Metrics:** Mean AC Voltage = `-1.5 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `BAS_Uptime_Minutes__max_168h (z=63.0); BAS_Uptime_Minutes__mean_168h (z=63.0); BAS_Uptime_Minutes__min_168h (z=63.0)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #13: BOI-BAGUIHATI (BOI)
* **Device ID:** `ef43bf30-aa77-11f0-b8ab-b38af83f6c9c`
* **Severe 6h Anomaly Intervals (Year):** **65** (Max Anomaly Score: `0.0848`)
* **Total Historic Alarms in 365 Days:** **641**
* **Observed Metrics:** Mean AC Voltage = `-1.1 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `usage_daily__std_1h (z=7.2); usage_last_7_days__std_1h (z=7.2); usage_daily__max_1h (z=7.1)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #14: DX7 (DX)
* **Device ID:** `6aac1ab0-63f4-11f1-9555-295f91c5b7c8`
* **Severe 6h Anomaly Intervals (Year):** **60** (Max Anomaly Score: `0.1052`)
* **Total Historic Alarms in 365 Days:** **251**
* **Observed Metrics:** Mean AC Voltage = `-1.5 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `ac_voltage__gap_max_7d (z=95.5); system_current__gap_max_7d (z=95.5); battery_voltage__gap_max_7d (z=95.5)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #15: BOI-BALURGHAT (BOI)
* **Device ID:** `668a4210-ab47-11f0-b150-2710a8915e1d`
* **Severe 6h Anomaly Intervals (Year):** **48** (Max Anomaly Score: `0.0683`)
* **Total Historic Alarms in 365 Days:** **393**
* **Observed Metrics:** Mean AC Voltage = `-1.4 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `ac_voltage__max_1h (z=7.6); ac_voltage__mean_1h (z=7.6); ac_voltage__min_1h (z=7.6)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #16: BOI-SATPUR (BOI)
* **Device ID:** `e2275560-bade-11f0-91ad-09ca52d2dd91`
* **Severe 6h Anomaly Intervals (Year):** **47** (Max Anomaly Score: `0.0704`)
* **Total Historic Alarms in 365 Days:** **101**
* **Observed Metrics:** Mean AC Voltage = `220.0 V`, Minimum Battery = `12.0 V`
* **Dominant Sensor Deviations:** `temperature__count_1h (z=13.6); memory__count_1h (z=13.5); frequency__count_1h (z=13.5)`
* **Recommended Field Action:** **General Maintenance: Complete quarterly preventative maintenance inspection.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #17: BOI-NOKARI (BOI)
* **Device ID:** `6c671990-c200-11f0-bb20-a78f8ac460b7`
* **Severe 6h Anomaly Intervals (Year):** **47** (Max Anomaly Score: `0.0687`)
* **Total Historic Alarms in 365 Days:** **89**
* **Observed Metrics:** Mean AC Voltage = `-1.4 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `errorCount__max_6h (z=29.2); errorCount__std_6h (z=28.3); errorCount__mean_6h (z=25.0)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #18: BOI-CHINSURAH (BOI)
* **Device ID:** `7b0a1200-a5d5-11f0-b4af-b334f2239a02`
* **Severe 6h Anomaly Intervals (Year):** **46** (Max Anomaly Score: `0.1634`)
* **Total Historic Alarms in 365 Days:** **26**
* **Observed Metrics:** Mean AC Voltage = `0.5 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `basLastHour__std_6h (z=7.9); timeLockLastHour__std_6h (z=7.9); fasLastHour__std_6h (z=7.7)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #19: BOI-ARAMBAGH (BOI)
* **Device ID:** `af53feb0-a5b0-11f0-b8ab-b38af83f6c9c`
* **Severe 6h Anomaly Intervals (Year):** **46** (Max Anomaly Score: `0.103`)
* **Total Historic Alarms in 365 Days:** **103**
* **Observed Metrics:** Mean AC Voltage = `0.7 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `basLastHour__std_6h (z=10.1); timeLockLastHour__std_6h (z=10.0); cctvLastHour__std_6h (z=8.1)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
### Branch #20: BOI-ISLAMPUR (BOI)
* **Device ID:** `dc0a38d0-ab31-11f0-87ea-b9809c1a7a9e`
* **Severe 6h Anomaly Intervals (Year):** **43** (Max Anomaly Score: `0.0587`)
* **Total Historic Alarms in 365 Days:** **93**
* **Observed Metrics:** Mean AC Voltage = `-1.3 V`, Minimum Battery = `-0.9 V`
* **Dominant Sensor Deviations:** `uptimeTotal__gap_max_7d (z=5.7); fasLastHour__gap_max_7d (z=5.4); timeLockLastHour__gap_max_7d (z=5.3)`
* **Recommended Field Action:** **Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.**

#### Human Field Review Form:
```markdown
[ ] Confirmed Equipment Fault (Battery / Inverter / Wiring failure)
[ ] Utility Power Failure (Chronic local grid instability / scheduled load-shedding)
[ ] Sensor / Cable Disconnection (CT clamp loose, false sensor reading)
[ ] Nuisance / False Alarm
Reviewer Name: _______________________    Date: _______________    Signed: __________________
Field Notes: _________________________________________________________________________________
```

---
