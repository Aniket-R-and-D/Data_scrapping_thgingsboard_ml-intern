# Phase R5 Stage D Report — Clean Evaluation Sets & Frozen Manifests

**Date:** October 14, 2026
**Scope:** Stage D of the R5 reliability-recovery plan (retire contaminated benchmark; build incident-grouped, deduplicated evaluation splits and frozen manifests; run deterministic-ticket baseline).
**Policy Version:** `ticket-policy-v1`
**Status:** GATE DELIVERABLE — awaiting approval. R5 is **NOT approved**.
**Provenance:** Built by the same AI agent that performed the Phase R5 work (self-audit disclosure; not an independent audit).

**Explicit statements:**
- **No LLM was fine-tuned, loaded, or evaluated in Stage D.**
- **No automatic dispatch, ThingsBoard alarm creation, or external notification occurred.**
- **No historical evidence, earlier phase report, or previously frozen test artifact was modified or deleted.**
- **Distinction of metrics:**
  1. **Deterministic renderer correctness:** 100% of evaluation records build under `TicketInput` and render deterministically without error (`render_success_rate = 1.0`).
  2. **Stage C field-validator acceptance:** 100% (`validator_acceptance_rate = 1.0`) across all three splits (`TRAIN`: 212/212, `FUTURE_INCIDENTS_KNOWN_DEVICES`: 407/407, `UNSEEN_DEVICES`: 150/150), with **0 false acceptances (0/10)** and **0 false rejections (0/5)** on the Stage C adversarial suite.
  3. **Independent semantic correctness:** **NOT MEASURED** (human review remains **PENDING**; zero human reviewers have scored any output).

---

## 1. Retirement of the Prior Contaminated Benchmark

The legacy R5 files are preserved untouched on disk and marked **RETIRED — DEVELOPMENT/DEBUG ONLY** via [`training_data/README.md`](../training_data/README.md) (and the Stage A notice [`reports/phase_r5_correction_notice.md`](phase_r5_correction_notice.md)):

- `training_data/remediated_explanation_test_heldout.jsonl` (100 records, SHA-256 `feabc8d9934677400e379da8d32925308326eb1b1249e32a988930d52b8066f8`)
- `training_data/remediated_explanation_train.jsonl` (450 records, SHA-256 `dd48497d79514e090dc0a98664984b5ad0e0e65f251945bc0c2180ed7e3cc582`)
- `reports/remediated_explanation_benchmark.json`

Why retired (reproduced by `python verify_r5_evidence.py`):
- **50/100** test inputs were verbatim duplicates of training inputs (30 distinct payload values).
- **63/100** completions were verbatim duplicates of training completions (42 distinct values).
- **43/46** test devices (93.5%) crossed splits.
- **33/44** active-incident test rows shared device, state, tier, AC voltage, and mains-loss duration (within 2.0 h) with a training record.
- Consequently, the 99% validator-acceptance rate on that file is **not** a generalization result, and neither file is referred to as "held-out" going forward.

---

## 2. Incident Grouping Rules (`src/eval/build_incidents.py`)

Source pool: `data/processed/remediated_shadow_predictions.jsonl` joined with `data/processed/remediated_features_matrix.parquet` — **149,175** 15-minute rows across **135** branch devices (`2026-10-01T22:00:00+00:00` to `2026-10-13T10:00:00+00:00`, 11.50 days / 1.6429 weeks).

Per device, sorted chronologically by UTC timestamp:
1. Each 15-minute row is assigned to one of three regimes:
   - `POWER`: `device_state == "ONLINE_POWER_LOSS"`, or `risk_tier in ("HIGH", "CRITICAL")`, or `RULE_AC_UNDER_50V` / `RULE_AC_SURGE_OVER_250V` triggered while online.
   - `CONNECTIVITY`: `device_state in ("STALE_DATA", "OFFLINE_LONG")` or `risk_tier == "CONNECTIVITY_ISSUE"`.
   - `NORMAL`: all remaining online rows (`ONLINE_OK`, `LOW`/`MEDIUM` without acute voltage collapse).
2. Consecutive rows on the same device in the same regime are merged into one incident while the gap between consecutive observations is `<= 6 hours`. `NORMAL` segments are additionally capped at `24 hours` so a multi-day steady run does not form a single 11.5-day block straddling the temporal cutoff. Continuous `POWER` and `CONNECTIVITY` episodes are **never** split across time: if a blackout or outage straddles the cutoff, the entire incident is excluded from the temporal split rather than cut in two.
3. Every incident receives a deterministic ID `INC-<device_id[:8]>-<regime[:4]>-<seq:04d>` and is stored in `data/eval/incidents_table.parquet` (**816** total incidents: 380 `NORMAL`, 231 `CONNECTIVITY`, 205 `POWER`).

---

## 3. Split Definitions, Temporal Cutoffs, Purge Window, and Exclusions (`src/eval/build_splits.py`)

### 3.1 Partitioning rules
- **Whole-device holdout (`UNSEEN_DEVICES`):** **27 of 135 devices (20.0%)** held out across all 4 banks (`BOI`: 20, `SBI`: 4, `BOB`: 2, `CANARA`: 1), selected deterministically by sorting each bank's devices by `(has_power_incident desc, device_id asc)` and taking every $k$-th site. Zero rows, incidents, or payloads from these 27 devices appear in `TRAIN` or `FUTURE_INCIDENTS_KNOWN_DEVICES`.
- **Known-device pool:** the remaining **108 devices**.
  - **Training cutoff (`train_cutoff_utc`):** `2026-10-08T00:00:00+00:00`.
  - **Temporal purge window:** **24 hours** (`2026-10-08T00:00:00+00:00` to `2026-10-09T00:00:00+00:00`), matching the 24-hour prediction horizon.
  - **`TRAIN`:** known-device incidents with `incident_end_utc <= 2026-10-08T00:00:00+00:00` (**191 candidate incidents**).
  - **`FUTURE_INCIDENTS_KNOWN_DEVICES`:** known-device incidents with `incident_start_utc >= 2026-10-09T00:00:00+00:00` (**337 candidate incidents**). Note: devices in this split are known (25 of 28 devices also appear in `TRAIN`), so this split is **not** described as device-held-out; it specifically evaluates future incidents after the 24-hour purge window on known sites.
- **Excluded incidents (logged with explicit reason in `incidents_table.parquet`):**
  - `EXCLUDED_STRADDLES_CUTOFF`: **107 incidents** (continuous multi-day `CONNECTIVITY` blackouts on chronically offline known devices that began before `2026-10-08T00:00:00Z` and ended after `2026-10-08T00:00:00Z`, plus any incident crossing the cutoff). Splitting them would place adjacent snapshots of the same blackout on both sides of the split.
  - `EXCLUDED_PURGE_WINDOW`: **65 incidents** on known devices that started inside the 24-hour purge buffer `(2026-10-08T00:00:00Z, 2026-10-09T00:00:00Z)`.
  - Total 15-minute rows in excluded incidents: **96,839 of 149,175** (predominantly 11.5-day continuous `OFFLINE_LONG` trajectories on chronically silent gateways).

### 3.2 Canonicalization and two-level deduplication
For up to 3 representative snapshots per eligible incident (start, midpoint, peak probability):
1. Each row is converted to a validated `TicketInput` (UTC ISO-8601 timestamps, sorted keys, policy precision) and rendered via `render_ticket`.
2. Two SHA-256 hashes are computed per record:
   - `canonical_payload_hash`: full Stage B/C canonical payload including UTC ISO-8601 timestamps.
   - `content_payload_hash`: canonical telemetry/prediction payload **excluding** clock timestamps (`device_id`, `device_name`, `bank`, `model_version`, `policy_version`, `device_state`, `measurements[field,value,unit,freshness]`, `calibrated_probability`, `risk_tier`, `triggered_rules`).
3. Any record matching an earlier record's `canonical_payload_hash` **or** `content_payload_hash` within the same split is removed (`within_split_duplicates_removed`), and any evaluation record matching a `TRAIN` hash on either level is removed (`cross_train_duplicates_removed`).

---

## 4. Exact Split Counts, Deduplication Audit, and Hashes

| Metric | `TRAIN` | `FUTURE_INCIDENTS_KNOWN_DEVICES` | `UNSEEN_DEVICES` |
|---|---:|---:|---:|
| **Data file** | `data/eval/train.jsonl` | `data/eval/future_known_devices.jsonl` | `data/eval/unseen_devices.jsonl` |
| **Manifest file** | `data/eval/manifest_train.json` | `data/eval/manifest_future_known_devices.json` | `data/eval/manifest_unseen_devices.json` |
| **Final record count** | **212** | **407** | **150** |
| **Unique `canonical_payload_hash` count** | **212** | **407** | **150** |
| **Unique `content_payload_hash` count** | **212** | **407** | **150** |
| **Within-split duplicate payloads removed** | 186 | 42 | 148 |
| **Cross-train duplicate payloads removed** | 0 | 94 | 0 |
| **Unique full-text completions** | 212 | 407 | 150 |
| **Unique structural template signatures** | 21 | 21 | 23 |
| **Distinct devices** | 30 | 28 | **27** (held-out) |
| **Distinct incidents** | 123 | 297 | 90 |
| **Min timestamp (UTC)** | `2026-10-01T22:00:00+00:00` | `2026-10-09T00:00:00+00:00` | `2026-10-01T22:00:00+00:00` |
| **Max timestamp (UTC)** | `2026-10-08T00:00:00+00:00` | `2026-10-13T05:00:00+00:00` | `2026-10-13T05:00:00+00:00` |
| **`data_file_sha256`** | `b5afcf84e873031387ea21dd3be726f1b829b79570cd8f7193d5b602fc5eadbe` | `54e224628cccd738e845d1bddc3477eb70fce2a68205200a8da5ed8027429ace` | `0338742c77f0be7040360373e18292868b6377b4fc928b2f160efc186dbcec46` |
| **`manifest_sha256`** | `a8a3c92e2f73324f47f04ddc929438e10ae25ee97071e169a7e9585e97b17e4e` | `7e8927dd4fe51d598407ff367853758a3fbc680c8bb4ed748a5e888e8097b72c` | `6594db4569235fe538a19b6eda855d3c77b4a688c2f7a8fbf9efdffe593183c5` |

**Note on completion overlap:** Every record's full deterministic ticket text is unique (`unique_completion_count == record_count`) because each ticket embeds its unique `Ticket ID`, timestamp, and `Input Payload SHA-256`. When those per-record lines and telemetry numbers are stripped to measure the structural boilerplate skeleton (`unique_template_signature_count`), the 212 / 407 / 150 records map to **21 / 21 / 23** structural template signatures. As required by Stage D, identical template skeletons are **not** leakage (the renderer is deterministic by design), but are reported here so template uniformity is never mistaken for free-text diversity.

### Cross-split leakage verification (`src/eval/verify_splits.py`)
- Cross-split `incident_id` overlap (`TRAIN` ∩ `FUTURE`, `TRAIN` ∩ `UNSEEN`, `FUTURE` ∩ `UNSEEN`): **0**
- Device overlap with `UNSEEN_DEVICES` (`TRAIN` ∩ `UNSEEN`, `FUTURE` ∩ `UNSEEN`): **0**
- Known-device overlap (`TRAIN` ∩ `FUTURE_INCIDENTS_KNOWN_DEVICES`): **25 devices** (expected by definition of `FUTURE_INCIDENTS_KNOWN_DEVICES`)
- Cross-split `canonical_payload_hash` overlap (`TRAIN` vs. both eval splits): **0**
- Cross-split `content_payload_hash` overlap (`TRAIN` vs. both eval splits): **0**

---

## 5. Deterministic-Ticket Baseline Evaluation & Alert Volume (`ticket-policy-v1`)

| Baseline Metric | `TRAIN` (212) | `FUTURE_INCIDENTS_KNOWN_DEVICES` (407) | `UNSEEN_DEVICES` (150) |
|---|---:|---:|---:|
| **Deterministic render success rate** | **100.0%** (212/212) | **100.0%** (407/407) | **100.0%** (150/150) |
| **Stage C field-validator acceptance rate** | **100.0%** (212/212) | **100.0%** (407/407) | **100.0%** (150/150) |
| **Power diagnostic tickets (`ONLINE_OK` / `ONLINE_POWER_LOSS`)** | 136 (64.2%) | 339 (83.3%) | 83 (55.3%) |
| **Connectivity tickets (`STALE_DATA` / `OFFLINE_LONG`)** | 76 (35.8%) | 68 (16.7%) | 67 (44.7%) |
| **Tier: `CRITICAL`** | 16 | 11 | 21 |
| **Tier: `HIGH`** | 5 | 3 | 10 |
| **Tier: `MEDIUM`** | 1 | 0 | 8 |
| **Tier: `LOW`** | 114 | 325 | 44 |
| **Tier: `CONNECTIVITY_ISSUE`** | 76 | 68 | 67 |
| **State: `ONLINE_OK`** | 125 | 222 | 75 |
| **State: `ONLINE_POWER_LOSS`** | 11 | 117 | 8 |
| **State: `STALE_DATA`** | 36 | 62 | 40 |
| **State: `OFFLINE_LONG`** | 40 | 6 | 27 |
| **Bank distribution** | BOI 200, CANARA 12 | BOI 397, CANARA 10 | BOI 108, CANARA 36, SBI 4, BOB 2 |

### Fleet-wide alert volume across the 11.5-day window (149,175 rows, 135 devices, 1.6429 weeks)
- **Full-window tier row counts:** `CONNECTIVITY_ISSUE`: 122,569 | `LOW`: 19,990 | `CRITICAL`: 4,161 | `HIGH`: 1,618 | `MEDIUM`: 837.
- **Actionable power-alert rows (`HIGH` or `CRITICAL` on online devices):** 5,779 rows grouped into **45 independent power-alert episodes** → **0.2029 power-alert episodes per device per week** (~1 power alert every 4.9 weeks per site).
- **Connectivity episodes (`STALE_DATA` / `OFFLINE_LONG`):** **231 episodes** → **1.0415 connectivity episodes per device per week**.
- **Stage C adversarial benchmark (re-verified):** **0 / 10 false acceptances**, **0 / 5 false rejections**, **10 / 10 paraphrases accepted**.

---

## 6. Commands Run and Exit Codes

| Command | Exit Code | Result |
|---|---:|---|
| `.venv\Scripts\python.exe src/eval/build_incidents.py` | **0** | 816 incidents built across 135 devices (149,175 rows) |
| `.venv\Scripts\python.exe src/eval/build_splits.py` | **0** | `TRAIN` (212), `FUTURE_INCIDENTS_KNOWN_DEVICES` (407), `UNSEEN_DEVICES` (150) + frozen manifests written |
| `.venv\Scripts\python.exe src/eval/verify_splits.py` | **0** | `verified: true`, `error_count: 0` |
| `.venv\Scripts\python.exe -m pytest tests/test_evaluation_split_integrity.py -q` | **0** | `7 passed` |
| Full regression suite (`test_tickets_schema`, `test_tickets_renderer`, `test_field_level_validator`, `test_evaluation_split_integrity`, `test_phase_r1..r5`) | **0** | `112 passed` |

---

## 7. Known Limitations

1. **Known-device temporal split bank coverage:** In the 108-device known pool, all 12 SBI and 8 BOB devices were in continuous multi-day `OFFLINE_LONG` states that straddled `2026-10-08T00:00:00Z`; our non-splitting rule rightly excluded those straddling blackouts (`EXCLUDED_STRADDLES_CUTOFF`) rather than slicing one continuous blackout across train and eval. Consequently, `TRAIN` and `FUTURE_INCIDENTS_KNOWN_DEVICES` contain `BOI` and `CANARA` records, while `SBI` and `BOB` are evaluated in `UNSEEN_DEVICES` (where whole-device holdout requires no temporal cut).
2. **Independent semantic accuracy remains NOT MEASURED:** 100% deterministic render success and 100% Stage C validator acceptance verify structural and field-level consistency against `ticket-policy-v1`, not human-verified domain quality.
