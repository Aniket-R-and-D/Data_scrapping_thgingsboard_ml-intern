# ThingsBoard Daily AI Risk Digest — 30 October 2026

**Monitored Fleet:** 130 Bank Branches  
**Fleet Health Score:** `55.0%`  
**Generated At:** `2026-10-30T02:30:33.659935+00:00`  

## Fleet Executive Summary
| Severity Tier | Branch Count | Operational Status |
|---|:---:|---|
| 🚨 **CRITICAL FAULT** | **29** | Immediate physical intervention required |
| ⚠️ **HIGH RISK** | **20** | Outage probability >= 50% within 24 hours |
| 🟢 **SERVICED / RESTORED** | **4** | Hardware repaired; verified healthy via real-time telemetry |
| 🟡 **ELEVATED DRIFT** | **19** | Baseline electrical / comms deviation |
| ✅ **NOMINAL** | **58** | Operating within healthy baseline thresholds |

## ✅ Verified Field Recoveries (Hardware Restored)
The following branches were previously at risk, but real-time telemetry confirms physical repair and nominal operation:

| Rank | Bank | Branch Name | Status | Vitals & Restoration Detail | ThingsBoard Link |
|:---:|:---:|---|:---:|---|:---:|
| 1 | BOI | **5472ea20-aa78-11f0-b8ab-b38af83f6c9c** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (0.7m ago); Battery healthy (14.0V, not low); Mains power restored (210.0V); Zero active ticket faults; CCTV online & cameras connected; Subsystems restored: gateway, bas, fas | [View on TB](https://seple.iot-private.cloud/devices/5472ea20-aa78-11f0-b8ab-b38af83f6c9c) |
| 2 | DX | **630c1480-63e6-11f1-9555-295f91c5b7c8** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (0.0m ago); Zero active ticket faults; Subsystems restored: gateway, fas | [View on TB](https://seple.iot-private.cloud/devices/630c1480-63e6-11f1-9555-295f91c5b7c8) |
| 3 | BOI | **6287e400-a823-11f0-b150-2710a8915e1d** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (0.3m ago); Battery healthy (14.0V, not low); Mains power restored (200.0V); Zero active ticket faults; CCTV online & cameras connected; Subsystems restored: gateway, bas, fas | [View on TB](https://seple.iot-private.cloud/devices/6287e400-a823-11f0-b150-2710a8915e1d) |
| 4 | BOI | **0bfa8fa0-c067-11f0-a562-d9639f025684** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (0.1m ago); Battery healthy (14.0V, not low); Mains power restored (210.0V); Zero active ticket faults; Subsystems restored: gateway, bas, fas | [View on TB](https://seple.iot-private.cloud/devices/0bfa8fa0-c067-11f0-a562-d9639f025684) |

## High-Risk & Critical Bank Branches
| Rank | Bank | Branch Name | Unified Tier | 24h Outage Prob | Anomaly Score | Primary Root Cause | ThingsBoard Link |
|:---:|:---:|---|:---:|:---:|:---:|---|:---:|
| 1 | CANARA | **0e4358d0-6708-11f1-8c55-5bb8284c6aef** | 🚨 CRITICAL | `99.4%` | `0.0442` | ac_voltage__gap_max_7d (z=236.1); battery_voltage__gap_max_7d (z=236.1); system_current__gap_max_7d (z=236.1) | [View on TB](https://seple.iot-private.cloud/devices/0e4358d0-6708-11f1-8c55-5bb8284c6aef) |
| 2 | BOB | **50ae7220-711f-11f0-bf1e-9f28a6572bf1** | 🚨 CRITICAL | `99.3%` | `-0.0305` | Disk Min 24H (60.6% impact; observed: -2.729 vs baseline: 0.033) (Outage Prob: 99.3%) | [View on TB](https://seple.iot-private.cloud/devices/50ae7220-711f-11f0-bf1e-9f28a6572bf1) |
| 3 | BOI | **4d4f61e0-a8f4-11f0-b4af-b334f2239a02** | 🚨 CRITICAL | `98.7%` | `-0.0130` | Elevated enclosure temperature (51.4% impact; observed: 1.097 vs baseline: -0.388) (Outage Prob: 98.7%) | [View on TB](https://seple.iot-private.cloud/devices/4d4f61e0-a8f4-11f0-b4af-b334f2239a02) |
| 4 | BOI | **a5cf2df0-d1cb-11f0-82a9-69e5355364db** | 🚨 CRITICAL | `97.8%` | `-0.0976` | Elevated enclosure temperature (52.8% impact; observed: 1.123 vs baseline: -0.388) (Outage Prob: 97.7%) | [View on TB](https://seple.iot-private.cloud/devices/a5cf2df0-d1cb-11f0-82a9-69e5355364db) |
| 5 | BOI | **424f3e90-1b96-11f1-88f8-7daa5068de06** | 🚨 CRITICAL | `96.5%` | `-0.1012` | Elevated enclosure temperature (43.9% impact; observed: 1.11 vs baseline: -0.388) (Outage Prob: 96.5%) | [View on TB](https://seple.iot-private.cloud/devices/424f3e90-1b96-11f1-88f8-7daa5068de06) |
| 6 | BOI | **d1a4f0f0-d73d-11f0-afc5-358ed627911d** | 🚨 CRITICAL | `96.2%` | `-0.1012` | Elevated enclosure temperature (41.2% impact; observed: 1.021 vs baseline: -0.388) (Outage Prob: 96.2%) | [View on TB](https://seple.iot-private.cloud/devices/d1a4f0f0-d73d-11f0-afc5-358ed627911d) |
| 7 | BOB | **1b016000-859d-11f0-9dcf-813811befd2b** | 🚨 CRITICAL | `93.8%` | `-0.0414` | Disk Min 24H (42.2% impact; observed: -5.49 vs baseline: 0.033) (Outage Prob: 93.8%) | [View on TB](https://seple.iot-private.cloud/devices/1b016000-859d-11f0-9dcf-813811befd2b) |
| 8 | BOI | **c0ad6070-b4ba-11f0-b150-2710a8915e1d** | 🚨 CRITICAL | `92.6%` | `-0.0182` | Accesscontrollasthour Gap Max 7D (59.3% impact; observed: 3.865 vs baseline: -0.265) (Outage Prob: 92.6%) | [View on TB](https://seple.iot-private.cloud/devices/c0ad6070-b4ba-11f0-b150-2710a8915e1d) |
| 9 | BOI | **b7736000-a98d-11f0-b4af-b334f2239a02** | 🚨 CRITICAL | `91.3%` | `-0.0995` | Elevated enclosure temperature (44.5% impact; observed: 1.124 vs baseline: -0.388) (Outage Prob: 91.3%) | [View on TB](https://seple.iot-private.cloud/devices/b7736000-a98d-11f0-b4af-b334f2239a02) |
| 10 | BOI | **194571b0-a998-11f0-b4af-b334f2239a02** | 🚨 CRITICAL | `90.3%` | `-0.0506` | Elevated enclosure temperature (40.8% impact; observed: 1.15 vs baseline: -0.388) (Outage Prob: 90.3%) | [View on TB](https://seple.iot-private.cloud/devices/194571b0-a998-11f0-b4af-b334f2239a02) |
| 11 | SEPLE | **1c3a2640-b59a-11f1-9dac-bfdc7cccc90b** | 🚨 CRITICAL | `89.9%` | `0.0362` | AC Grid mains failure / voltage sag (58.3% impact; observed: 1.948 vs baseline: -0.632) (Outage Prob: 89.9%) | [View on TB](https://seple.iot-private.cloud/devices/1c3a2640-b59a-11f1-9dac-bfdc7cccc90b) |
| 12 | BOB | **1a801820-d0f6-11f0-9dd1-0f70161fd1f6** | 🚨 CRITICAL | `89.8%` | `-0.0614` | Gateway processor saturation (45.2% impact; observed: 3.476 vs baseline: -0.394) (Outage Prob: 89.8%) | [View on TB](https://seple.iot-private.cloud/devices/1a801820-d0f6-11f0-9dd1-0f70161fd1f6) |
| 13 | BOI | **275da890-d1c4-11f0-b238-bd8a9470eef2** | 🚨 CRITICAL | `89.4%` | `-0.1019` | AC Grid mains failure / voltage sag (36.1% impact; observed: -1.503 vs baseline: 0.587) (Outage Prob: 89.4%) | [View on TB](https://seple.iot-private.cloud/devices/275da890-d1c4-11f0-b238-bd8a9470eef2) |
| 14 | BOI | **589773f0-c44a-11f0-ae8f-9fad3c067e6e** | 🚨 CRITICAL | `89.2%` | `-0.1013` | Elevated enclosure temperature (32.5% impact; observed: 1.14 vs baseline: -0.388) (Outage Prob: 89.2%) | [View on TB](https://seple.iot-private.cloud/devices/589773f0-c44a-11f0-ae8f-9fad3c067e6e) |
| 15 | BOI | **64673b00-ed32-11f0-80c0-fdc442896b74** | 🚨 CRITICAL | `87.9%` | `-0.1040` | Elevated enclosure temperature (31.9% impact; observed: 1.006 vs baseline: -0.388) (Outage Prob: 87.9%) | [View on TB](https://seple.iot-private.cloud/devices/64673b00-ed32-11f0-80c0-fdc442896b74) |
| 16 | CANARA | **388f1380-31ad-11f1-9c6f-b71bb2771567** | 🚨 CRITICAL | `86.8%` | `0.0470` | ac_voltage__max_1h (z=7.6); ac_voltage__mean_1h (z=7.6); ac_voltage__min_1h (z=7.6) | [View on TB](https://seple.iot-private.cloud/devices/388f1380-31ad-11f1-9c6f-b71bb2771567) |
| 17 | BOI | **9f2b14b0-ab41-11f0-b8ab-b38af83f6c9c** | 🚨 CRITICAL | `86.1%` | `-0.0393` | Accesscontrollasthour Gap Max 7D (52.9% impact; observed: 5.453 vs baseline: -0.265) (Outage Prob: 86.1%) | [View on TB](https://seple.iot-private.cloud/devices/9f2b14b0-ab41-11f0-b8ab-b38af83f6c9c) |
| 18 | CANARA | **6910ecc0-671f-11f1-8c55-5bb8284c6aef** | 🚨 CRITICAL | `85.8%` | `0.0186` | ac_voltage__gap_max_7d (z=100.8); system_current__gap_max_7d (z=100.8); battery_voltage__gap_max_7d (z=100.8) | [View on TB](https://seple.iot-private.cloud/devices/6910ecc0-671f-11f1-8c55-5bb8284c6aef) |
| 19 | BOI | **e1f85910-db3e-11f0-afc5-358ed627911d** | 🚨 CRITICAL | `85.6%` | `-0.1019` | Elevated enclosure temperature (45.1% impact; observed: 1.109 vs baseline: -0.388) (Outage Prob: 85.6%) | [View on TB](https://seple.iot-private.cloud/devices/e1f85910-db3e-11f0-afc5-358ed627911d) |
| 20 | BOI | **5bb79740-c51c-11f0-ae8f-9fad3c067e6e** | 🚨 CRITICAL | `85.5%` | `-0.0909` | BAS_Uptime_Minutes__max_168h (z=63.0); BAS_Uptime_Minutes__mean_168h (z=63.0); BAS_Uptime_Minutes__min_168h (z=63.0) | [View on TB](https://seple.iot-private.cloud/devices/5bb79740-c51c-11f0-ae8f-9fad3c067e6e) |
| 21 | BOI | **23d4b010-ec77-11f0-a6fc-1dffa956f056** | 🚨 CRITICAL | `84.2%` | `-0.0162` | Gateway processor saturation (48.4% impact; observed: 2.249 vs baseline: -0.394) (Outage Prob: 84.1%) | [View on TB](https://seple.iot-private.cloud/devices/23d4b010-ec77-11f0-a6fc-1dffa956f056) |
| 22 | BOI | **260c9fc0-a9bd-11f0-b8ab-b38af83f6c9c** | 🚨 CRITICAL | `81.8%` | `-0.0992` | Elevated enclosure temperature (46.1% impact; observed: 1.259 vs baseline: -0.388) (Outage Prob: 81.9%) | [View on TB](https://seple.iot-private.cloud/devices/260c9fc0-a9bd-11f0-b8ab-b38af83f6c9c) |
| 23 | BOI | **8ea372b0-d4de-11f0-b238-bd8a9470eef2** | 🚨 CRITICAL | `81.8%` | `-0.0883` | Gateway processor saturation (27.5% impact; observed: -0.878 vs baseline: 0.18) (Outage Prob: 81.8%) | [View on TB](https://seple.iot-private.cloud/devices/8ea372b0-d4de-11f0-b238-bd8a9470eef2) |
| 24 | BOB | **da7862c0-729d-11f0-9029-d34a0811187f** | 🚨 CRITICAL | `81.3%` | `-0.0813` | Memory Mean 24H (39.9% impact; observed: 3.802 vs baseline: -0.297) (Outage Prob: 81.3%) | [View on TB](https://seple.iot-private.cloud/devices/da7862c0-729d-11f0-9029-d34a0811187f) |
| 25 | BOI | **da16d690-da3f-11f0-afc5-358ed627911d** | 🚨 CRITICAL | `80.5%` | `-0.0097` | Accesscontrollasthour Gap Max 7D (44.0% impact; observed: 4.07 vs baseline: -0.265) (Outage Prob: 80.5%) | [View on TB](https://seple.iot-private.cloud/devices/da16d690-da3f-11f0-afc5-358ed627911d) |

## Recommended Technician Interventions
1. **0e4358d0-6708-11f1-8c55-5bb8284c6aef** (CANARA): Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.
2. **50ae7220-711f-11f0-bf1e-9f28a6572bf1** (BOB): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
3. **4d4f61e0-a8f4-11f0-b4af-b334f2239a02** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
4. **a5cf2df0-d1cb-11f0-82a9-69e5355364db** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
5. **424f3e90-1b96-11f1-88f8-7daa5068de06** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
6. **d1a4f0f0-d73d-11f0-afc5-358ed627911d** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
7. **1b016000-859d-11f0-9dcf-813811befd2b** (BOB): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
8. **c0ad6070-b4ba-11f0-b150-2710a8915e1d** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
9. **b7736000-a98d-11f0-b4af-b334f2239a02** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
10. **194571b0-a998-11f0-b4af-b334f2239a02** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.

---
*Automated AI Operations Dispatcher | SEPLE NovaEdge IoT AI Engine*