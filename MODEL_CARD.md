# Model Card: Bank Branch Telemetry Outage Warning & Diagnostic Ticket System (R6 Release)

**Release:** `r5-reliability-recovery-stage-f` · **Date:** 2026-10-14 · **Policy:** `ticket-policy-v1`
**Status note:** This card regenerates the October 2026 model card. Figures that could not be
reproduced from preserved execution artifacts were removed (see
`reports/phase_r5_claim_evidence_ledger.md` entry L14 and
`reports/phase_r5_correction_notice.md` §5). Every number below cites its source artifact.

---

## 1. Intended use

Operational **warning and diagnostic ticket generation** for 135 bank branch telemetry
gateways (BOI, SBI, BOB, Canara): a calibrated 24-hour power-outage probability feed plus a
deterministic, typed diagnostic/connectivity ticket rendered from validated telemetry.

## 2. Explicit non-goals

- **Not automatic technician dispatch.** Tickets carry bounded *suggested* investigation
  checks only; every ticket states that no dispatch, alarm, or notification was performed.
- **Not a substitute for electrical safety procedures.** Tickets never confirm root cause;
  `ONLINE_POWER_LOSS` tickets carry an explicit root-cause-not-confirmed disclaimer.
- **Not a guaranteed hallucination-free system.** The Stage C field validator rejects 10/10
  tested falsification patterns with 0/5 false rejections, but unrestricted-prose validation
  remains unreliable — which is why free LLM text is never the production path.
- **Not a connectivity diagnostician.** On `STALE_DATA`/`OFFLINE_LONG` the system states
  electrical condition is unknown and issues connectivity investigation tickets with no
  outage probability.

## 3. Data source and time range

- Raw telemetry: ThingsBoard tenant ingest, 135 production branch gateways
  (BOI 104, SBI 16, BOB 10, Canara 5); 15-minute resampling.
- Remediated feature matrix: `data/processed/remediated_features_matrix.parquet`
  (**1,166,535 rows**); shadow/backtest pool: `data/processed/remediated_shadow_predictions.jsonl`
  (**149,175 rows**, `2026-10-01T22:00:00Z` → `2026-10-13T10:00:00Z`).
- Battery instrumentation: **32 of 135 devices (23.7%)** have working battery-voltage
  sensors (BOI 31, Canara 1); SBI (16) and BOB (10) have none.

## 4. Device-state taxonomy and label definitions

- `ONLINE_OK` (fresh telemetry, no active loss) · `ONLINE_POWER_LOSS` (fresh telemetry
  showing AC collapse/mains loss) · `STALE_DATA` (>60 min without telemetry) ·
  `OFFLINE_LONG` (>24 h without telemetry). Source: `src/features/state_machine.py`,
  audited in `reports/phase_r1_data_state_classification.md`.
- Primary target `y_power_outage_24h`: mains lost (AC < 50 V or mains off) sustained
  ≥15 min **and** (battery < 10.8 V where equipped, or verified `POWER OFF`/`BATTERY LOW`
  alarm), evaluated over `[t, t+24h]`. Heartbeat gap is **excluded** from the label and
  banned from features. Source: `configs/config.yaml`, `reports/phase_r2_label_redesign.md`.
- Secondary target `y_connectivity_loss_24h`: telemetry silence ≥60 min (separate
  operational problem, never a power tier).

## 5. Architecture

| Component | Role | Status |
|---|---|---|
| **Deterministic ticket renderer** (`src/tickets/renderer.py`, `ticket-policy-v1`) | **Default production ticket path.** Renders typed `TicketInput` to OBSERVED/UNKNOWN/POSSIBLE-CAUSE/INVESTIGATION tickets with traceable IDs and payload hashes | **Enabled (default)** |
| **LightGBM outage predictor** (`models/outage_predictor/remediated_lightgbm_clean.joblib`, SHA-256 `799fae21…5eadbe`) + Platt calibrator (`…0958ce80…edbed`) | 24-hour outage probability; tiers from calibrated bands (LOW<0.02 / MEDIUM<0.05 / HIGH<0.15 / CRITICAL≥0.15; surge rule forces CRITICAL; stale/offline → `CONNECTIVITY_ISSUE`) | Enabled as advisory feed |
| **Fine-tuned Qwen-0.5B** (`remediated_final_model`, SHA-256 `be45761a…e051`) | Experimental commentary only | **Disabled by default** (`EXPERIMENTAL_LLM_ENABLED=false`) |
| **Stage C field validator** (`validator_version: stage-c-field-validator-v1`) | Grounding checks on every rendered ticket | Enabled |
| Dispatch / alarms / notifications | — | **Disabled**; startup check fails closed |

## 6. Evaluation results (clean Stage D/E sets only)

- **Predictor (R3, untouched test split: 149,175 rows, 2,475 positives, 29 independent
  events; 250 bootstrap iterations):** precision **20.26%** [19.42%, 20.97%], recall
  **79.68%** [78.11%, 81.10%], F1 **0.3231** [0.3107, 0.3324], PR-AUC **0.2540**
  [0.2392, 0.2700], incident recall **89.66% (26/29)**, mean lead time **22.6 h**,
  **0.26** false-alarm episodes/device/week. Leakage ablation: unlagged heartbeat feature
  inflated PR-AUC to 0.2873 (+13.1%). Source: `reports/phase_r3_outage_predictor_remediation.md`.
  Per-bank note: SBI and BOB had **zero** positives in the test window, so no
  precision/recall is computable for them there.
- **Tickets (Stage D/E, frozen manifests):** deterministic render success **100%** and
  Stage C validator acceptance **100%** on `TRAIN` (212), `FUTURE_INCIDENTS_KNOWN_DEVICES`
  (407), and `UNSEEN_DEVICES` (150), with **0 cross-split payload overlap**.
  Fleet alert volume under `ticket-policy-v1`: **0.2029** power-alert episodes and
  **1.0415** connectivity episodes per device per week.
- **LLM value (Stage E, 557 paired records):** neither Qwen model added a single supported
  fact or investigation step beyond the deterministic ticket (**0/557**); remediated Qwen
  introduced unsupported claims on 14/407 future-known and 31/150 unseen-device records
  (probability truncation, sensorless battery-bank inspection orders, one stale-as-current
  snapshot). **Qwen remains experimental, default OFF.**
- **Validator adversarial suite (Stage C):** **0/10** false acceptances, **0/5** false
  rejections, **10/10** paraphrases accepted.

## 7. Known failure modes

1. **Instantaneous breaker trips** without precursor degradation are unpredictable from
   steady-state telemetry (3 of 29 R3 test events missed).
2. **Stale/missing telemetry:** during blackouts the system has no electrical visibility
   and must say so (connectivity tickets only).
3. **Unequipped battery fleets (SBI, BOB):** outage evidence rests on AC/mains patterns
   alone; battery claims are always `UNKNOWN` there.
4. **Connectivity-vs-power ambiguity:** a silent gateway can mask an ongoing outage;
   tickets must present both hypotheses as unconfirmed.
5. **New-site generalization:** whole-device holdout (`UNSEEN_DEVICES`, 27 sites) is the
   only spatial evidence; sites with <7 days of telemetry fall back to fleet priors.

## 8. Human review and semantic accuracy

- **Human review: PENDING** (0 completed reviews; 30-case blinded pack ready at
  `reports/phase_r5_stage_e_human_review_pack/`).
- **Independent semantic accuracy: NOT MEASURED.** No reviewer scores, credentials, kappa
  values, or "audited" labels from prior reports are valid (see Stage A correction notice).

## 9. Monitoring plan and retraining triggers

- Weekly PSI drift check on AC voltage/features (alert at PSI > 0.20); weekly Brier-score
  calibration audit (recalibrate if Brier rises > 0.025); delayed 24-hour shadow scoring
  with maturation buffer; 100% Stage C validation on rendered tickets.
- **Retraining triggers:** sustained precision drop below the 19.42% CI lower bound,
  incident recall below 75%, tier-volume drift (HIGH+CRITICAL persistently ≥10%), or new
  sensor types. Any retraining requires a new frozen manifest version and must not tune on
  frozen evaluation splits.
- **LLM enablement requires:** a non-tabular input need, ≥99.5% Stage C commentary pass
  with zero probability/unit/sensorless-battery errors on frozen splits, **and** completed
  blinded human review preferring commentary tickets. See Stage E report §6.
