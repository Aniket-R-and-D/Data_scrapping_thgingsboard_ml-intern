# ThingsBoard Daily AI Risk Digest — 27 October 2026

**Monitored Fleet:** 130 Bank Branches  
**Fleet Health Score:** `55.0%`  
**Generated At:** `2026-10-27T04:17:48.913315+00:00`  

## Fleet Executive Summary
| Severity Tier | Branch Count | Operational Status |
|---|:---:|---|
| 🚨 **CRITICAL FAULT** | **28** | Immediate physical intervention required |
| ⚠️ **HIGH RISK** | **21** | Outage probability >= 50% within 24 hours |
| 🟢 **SERVICED / RESTORED** | **4** | Hardware repaired; verified healthy via real-time telemetry |
| 🟡 **ELEVATED DRIFT** | **19** | Baseline electrical / comms deviation |
| ✅ **NOMINAL** | **58** | Operating within healthy baseline thresholds |

## ✅ Verified Field Recoveries (Hardware Restored)
The following branches were previously at risk, but real-time telemetry confirms physical repair and nominal operation:

| Rank | Bank | Branch Name | Status | Vitals & Restoration Detail | ThingsBoard Link |
|:---:|:---:|---|:---:|---|:---:|
| 1 | BOI | **BOI-BABAI** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (1.5m ago); Subsystems restored: gateway, bas, fas | [View on TB](https://seple.iot-private.cloud/devices/5472ea20-aa78-11f0-b8ab-b38af83f6c9c) |
| 2 | DX | **DX2** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (0.0m ago); Zero active ticket faults; Subsystems restored: gateway, fas | [View on TB](https://seple.iot-private.cloud/devices/630c1480-63e6-11f1-9555-295f91c5b7c8) |
| 3 | BOI | **BOI-SALTLAKE** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (0.1m ago); Battery healthy (14.0V, not low); Mains power restored (200.0V); Zero active ticket faults; CCTV online & cameras connected; Subsystems restored: gateway, bas, fas | [View on TB](https://seple.iot-private.cloud/devices/6287e400-a823-11f0-b150-2710a8915e1d) |
| 4 | CANARA | **CANARA-BARUIPUR** | `🟢 RESTORED` | Hardware restored; Verified operational recovery: Recent data activity (1.0m ago); Zero active ticket faults; Subsystems restored: gateway, bas, fas | [View on TB](https://seple.iot-private.cloud/devices/0b7bc7d0-671d-11f1-8c55-5bb8284c6aef) |

## High-Risk & Critical Bank Branches
| Rank | Bank | Branch Name | Unified Tier | 24h Outage Prob | Anomaly Score | Primary Root Cause | ThingsBoard Link |
|:---:|:---:|---|:---:|:---:|:---:|---|:---:|
| 1 | CANARA | **CANARA-KAMARHATI** | 🚨 CRITICAL | `99.4%` | `0.0442` | ac_voltage__gap_max_7d (z=236.1); battery_voltage__gap_max_7d (z=236.1); system_current__gap_max_7d (z=236.1) | [View on TB](https://seple.iot-private.cloud/devices/0e4358d0-6708-11f1-8c55-5bb8284c6aef) |
| 2 | BOB | **BOB-BARUIPUR** | 🚨 CRITICAL | `99.3%` | `-0.0305` | High Predictive Collapse Risk (99.3%) | [View on TB](https://seple.iot-private.cloud/devices/50ae7220-711f-11f0-bf1e-9f28a6572bf1) |
| 3 | BOI | **BOI-MALLAGURI** | 🚨 CRITICAL | `98.7%` | `-0.0130` | High Predictive Collapse Risk (98.7%) | [View on TB](https://seple.iot-private.cloud/devices/4d4f61e0-a8f4-11f0-b4af-b334f2239a02) |
| 4 | BOI | **BOI-CUTTACK** | 🚨 CRITICAL | `97.8%` | `-0.0976` | High Predictive Collapse Risk (97.7%) | [View on TB](https://seple.iot-private.cloud/devices/a5cf2df0-d1cb-11f0-82a9-69e5355364db) |
| 5 | BOI | **BOI-SINGH-MORE** | 🚨 CRITICAL | `96.5%` | `-0.1012` | High Predictive Collapse Risk (96.5%) | [View on TB](https://seple.iot-private.cloud/devices/424f3e90-1b96-11f1-88f8-7daa5068de06) |
| 6 | BOI | **BOI-PANASPADA** | 🚨 CRITICAL | `96.2%` | `-0.1012` | High Predictive Collapse Risk (96.2%) | [View on TB](https://seple.iot-private.cloud/devices/d1a4f0f0-d73d-11f0-afc5-358ed627911d) |
| 7 | BOB | **BOB-BARAJAGULI** | 🚨 CRITICAL | `93.8%` | `-0.0414` | High Predictive Collapse Risk (93.8%) | [View on TB](https://seple.iot-private.cloud/devices/1b016000-859d-11f0-9dcf-813811befd2b) |
| 8 | BOI | **BOI-LALPUR** | 🚨 CRITICAL | `92.6%` | `-0.0182` | High Predictive Collapse Risk (92.6%) | [View on TB](https://seple.iot-private.cloud/devices/c0ad6070-b4ba-11f0-b150-2710a8915e1d) |
| 9 | BOI | **BOI-HARDA-ROAD** | 🚨 CRITICAL | `91.3%` | `-0.0995` | High Predictive Collapse Risk (91.3%) | [View on TB](https://seple.iot-private.cloud/devices/b7736000-a98d-11f0-b4af-b334f2239a02) |
| 10 | BOI | **BOI-HGBAD** | 🚨 CRITICAL | `90.3%` | `-0.0506` | High Predictive Collapse Risk (90.3%) | [View on TB](https://seple.iot-private.cloud/devices/194571b0-a998-11f0-b4af-b334f2239a02) |
| 11 | SEPLE | **SEPLe-HO1** | 🚨 CRITICAL | `89.9%` | `0.0362` | High Predictive Collapse Risk (89.9%) | [View on TB](https://seple.iot-private.cloud/devices/1c3a2640-b59a-11f1-9dac-bfdc7cccc90b) |
| 12 | BOB | **BOB-SURYASEN** | 🚨 CRITICAL | `89.8%` | `-0.0614` | High Predictive Collapse Risk (89.8%) | [View on TB](https://seple.iot-private.cloud/devices/1a801820-d0f6-11f0-9dd1-0f70161fd1f6) |
| 13 | BOI | **BOI-BHUBANESHWAR** | 🚨 CRITICAL | `89.4%` | `-0.1019` | High Predictive Collapse Risk (89.4%) | [View on TB](https://seple.iot-private.cloud/devices/275da890-d1c4-11f0-b238-bd8a9470eef2) |
| 14 | BOI | **BOI-SONAHATU** | 🚨 CRITICAL | `89.2%` | `-0.1013` | High Predictive Collapse Risk (89.2%) | [View on TB](https://seple.iot-private.cloud/devices/589773f0-c44a-11f0-ae8f-9fad3c067e6e) |
| 15 | BOI | **BOI-AIZWAL** | 🚨 CRITICAL | `87.9%` | `-0.1040` | High Predictive Collapse Risk (87.9%) | [View on TB](https://seple.iot-private.cloud/devices/64673b00-ed32-11f0-80c0-fdc442896b74) |
| 16 | CANARA | **CANARA-CHETLA** | 🚨 CRITICAL | `86.8%` | `0.0470` | ac_voltage__max_1h (z=7.6); ac_voltage__mean_1h (z=7.6); ac_voltage__min_1h (z=7.6) | [View on TB](https://seple.iot-private.cloud/devices/388f1380-31ad-11f1-9c6f-b71bb2771567) |
| 17 | BOI | **BOI-RAIGANJ** | 🚨 CRITICAL | `86.1%` | `-0.0393` | High Predictive Collapse Risk (86.1%) | [View on TB](https://seple.iot-private.cloud/devices/9f2b14b0-ab41-11f0-b8ab-b38af83f6c9c) |
| 18 | CANARA | **CANARA-HALDI2** | 🚨 CRITICAL | `85.8%` | `0.0186` | ac_voltage__gap_max_7d (z=100.8); system_current__gap_max_7d (z=100.8); battery_voltage__gap_max_7d (z=100.8) | [View on TB](https://seple.iot-private.cloud/devices/6910ecc0-671f-11f1-8c55-5bb8284c6aef) |
| 19 | BOI | **BOI-PURI** | 🚨 CRITICAL | `85.6%` | `-0.1019` | High Predictive Collapse Risk (85.6%) | [View on TB](https://seple.iot-private.cloud/devices/e1f85910-db3e-11f0-afc5-358ed627911d) |
| 20 | BOI | **BOI-SITHIO** | 🚨 CRITICAL | `85.5%` | `-0.0909` | BAS_Uptime_Minutes__max_168h (z=63.0); BAS_Uptime_Minutes__mean_168h (z=63.0); BAS_Uptime_Minutes__min_168h (z=63.0) | [View on TB](https://seple.iot-private.cloud/devices/5bb79740-c51c-11f0-ae8f-9fad3c067e6e) |
| 21 | BOI | **BOI-ZARKAWT** | 🚨 CRITICAL | `84.2%` | `-0.0162` | High Predictive Collapse Risk (84.1%) | [View on TB](https://seple.iot-private.cloud/devices/23d4b010-ec77-11f0-a6fc-1dffa956f056) |
| 22 | BOI | **BOI-PIPARIYA** | 🚨 CRITICAL | `81.8%` | `-0.0992` | High Predictive Collapse Risk (81.9%) | [View on TB](https://seple.iot-private.cloud/devices/260c9fc0-a9bd-11f0-b8ab-b38af83f6c9c) |
| 23 | BOI | **BOI-SHILLONG** | 🚨 CRITICAL | `81.8%` | `-0.0883` | High Predictive Collapse Risk (81.8%) | [View on TB](https://seple.iot-private.cloud/devices/8ea372b0-d4de-11f0-b238-bd8a9470eef2) |
| 24 | BOB | **BOB-KALIKAPUR** | 🚨 CRITICAL | `81.3%` | `-0.0813` | High Predictive Collapse Risk (81.3%) | [View on TB](https://seple.iot-private.cloud/devices/da7862c0-729d-11f0-9029-d34a0811187f) |
| 25 | BOI | **BOI-ROING** | 🚨 CRITICAL | `80.5%` | `-0.0097` | High Predictive Collapse Risk (80.5%) | [View on TB](https://seple.iot-private.cloud/devices/da16d690-da3f-11f0-afc5-358ed627911d) |

## Recommended Technician Interventions
1. **CANARA-KAMARHATI** (CANARA): Urgent: Inspect 12V backup battery pack; test internal resistance & replace if failing load test.
2. **BOB-BARUIPUR** (BOB): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
3. **BOI-MALLAGURI** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
4. **BOI-CUTTACK** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
5. **BOI-SINGH-MORE** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
6. **BOI-PANASPADA** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
7. **BOB-BARAJAGULI** (BOB): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
8. **BOI-LALPUR** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
9. **BOI-HARDA-ROAD** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.
10. **BOI-HGBAD** (BOI): Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery.

---
*Automated AI Operations Dispatcher | SEPLE NovaEdge IoT AI Engine*