# Bank Branch Telemetry Outage Warning & Diagnostic Ticket System

Advisory 24-hour power-outage warning feed and deterministic diagnostic/connectivity ticket
generation for 135 commercial bank branch telemetry gateways (**BOI 104, SBI 16, BOB 10,
Canara 5**) connected via ThingsBoard IoT.

**Operational default:** the deterministic typed ticket renderer (`src/tickets/renderer.py`,
`ticket-policy-v1`). **The fine-tuned Qwen model is experimental and disabled by default**
(`EXPERIMENTAL_LLM_ENABLED=false`). No dispatch, alarm creation, or external notification
is performed anywhere in this system.

> **Evidence correction notice (2026-10-14):** earlier reports contained a fabricated
> "double-blind human audit", a contaminated "held-out" benchmark, and unsupported semantic
> estimates. All are retracted in `reports/phase_r5_correction_notice.md` with a
> per-claim evidence ledger (`reports/phase_r5_claim_evidence_ledger.md`). Human review is
> **PENDING**; independent semantic accuracy is **NOT MEASURED**. Do not cite the retired
> benchmark (`training_data/README.md`) as a headline result.

## Operational status (2026-10-15 — live operation PAUSED)

- **Deterministic renderer is the production default** (`ticket-policy-v1`).
- **LLM (Qwen) is experimental and disabled.** No dispatch, alarm creation, or
  external notification is enabled anywhere.
- **Live shadow is paused** pending tenant-owner authorization (Gate A) and
  scheduler stability (H1): the scheduled task is disabled after repeated
  OS-level terminations (`0xC000013A`) with no stable live cycle yet.
- Live polling cannot run without a completed `configs/gate_a_approval.yaml`
  (template: `configs/gate_a_approval.template.yaml`); the scorer refuses otherwise.
- Current handoff: `reports/h1_wait_state.md`. Live readout:
  `.venv\Scripts\python.exe -m src.live.scheduler_health --status`.

---

## 1. Architecture

```
[ThingsBoard telemetry, 15-min]
        │
        ▼
[Device state machine] ── ONLINE_OK / ONLINE_POWER_LOSS ──► [LightGBM + Platt]
   STALE_DATA / OFFLINE_LONG ──► connectivity path                │ calibrated P
        │                                                          ▼
        │                                              [Tier assignment + rules]
        │                                                          │
        └──────────────────────┬───────────────────────────────────┘
                               ▼
                 [TicketInput validation (ticket-policy-v1)]
                               │  rejects: bad prob/tier/state/units/rules/identity
                               ▼
                 [Deterministic renderer] ──► operational ticket
                               │  (OBSERVED / UNKNOWN / POSSIBLE CAUSE / INVESTIGATION)
                               ▼
                 [Stage C field validator] ──► accept (100% on clean splits)
```

- Probabilities and tiers come **only** from the calibrated predictor and deterministic
  rules. The LLM never predicts.
- `STALE_DATA`/`OFFLINE_LONG` devices receive connectivity tickets with **no outage
  probability** and an explicit electrical-condition-unknown statement.
- Missing battery sensor renders as *"Battery voltage unavailable: no configured
  battery-voltage sensor. Battery presence and condition are unknown."* — never as missing
  hardware.

## 2. Environment setup

```bash
# Clone, then use the project virtual environment (required: has joblib/sklearn/torch):
.venv\Scripts\python.exe -c "import sklearn, joblib, torch; print('deps ok')"
```

Key configuration: `configs/config.yaml` (ingest, labels, features, rules, thresholds),
`configs/release.yaml` (release flags, artifact hashes, manifest hashes — verified by the
startup check).

## 3. One command per stage

```bash
# Stage A — evidence reproduction (read-only; exit 0):
.venv\Scripts\python.exe verify_r5_evidence.py

# Stage B — ticket schema/renderer/LLM-bridge tests:
.venv\Scripts\python.exe -m pytest tests/test_tickets_schema.py tests/test_tickets_renderer.py -q

# Stage C — field-level validator + adversarial suite:
.venv\Scripts\python.exe -m pytest tests/test_field_level_validator.py -q

# Stage D — build incidents, splits + frozen manifests, verify:
.venv\Scripts\python.exe src/eval/build_incidents.py
.venv\Scripts\python.exe src/eval/build_splits.py
.venv\Scripts\python.exe src/eval/verify_splits.py
.venv\Scripts\python.exe -m pytest tests/test_evaluation_split_integrity.py -q

# Stage E — LLM comparison on clean splits (GPU; ~10 min on RTX 3050) + tests:
.venv\Scripts\python.exe src/eval/run_stage_e_comparison.py
.venv\Scripts\python.exe -m pytest tests/test_stage_e_comparison.py -q

# R6 — fail-closed startup check + full suite:
.venv\Scripts\python.exe src/tickets/startup_check.py
.venv\Scripts\python.exe -m pytest tests/test_tickets_schema.py tests/test_tickets_renderer.py tests/test_field_level_validator.py tests/test_evaluation_split_integrity.py tests/test_stage_e_comparison.py tests/test_release_startup.py tests/test_phase_r1_state_classification.py tests/test_phase_r2_label_leakage.py tests/test_phase_r3_predictor.py tests/test_phase_r4_consistency.py tests/test_phase_r5_claims.py -q
```

## 4. Evaluation commands and headline results

```bash
.venv\Scripts\python.exe src/eval/verify_splits.py          # split integrity: verified, 0 errors
.venv\Scripts\python.exe src/tickets/startup_check.py       # release config: PASS
```

- **Predictor (R3, untouched split: 149,175 rows, 2,475 positives, 29 events; 250
  bootstraps):** precision **20.26%** [19.42%, 20.97%], recall **79.68%**, F1 **0.3231**,
  PR-AUC **0.2540**, incident recall **89.66% (26/29)**, mean lead time **22.6 h**.
  (`reports/phase_r3_outage_predictor_remediation.md`)
- **Tickets (Stage D/E frozen splits):** deterministic render success **100%**, Stage C
  validator acceptance **100%** on TRAIN (212), FUTURE-known (407), UNSEEN (150);
  **0** cross-split payload overlap. (`reports/phase_r5_stage_d_clean_evaluation_sets.md`)
- **LLM value (Stage E, 557 paired records):** Qwen adds **0** supported facts/steps beyond
  the renderer; unsupported claims on 14/407 + 31/150 records; **KEEP EXPERIMENTAL**.
  (`reports/phase_r5_stage_e_llm_value_assessment.md`)
- **Validator adversarial suite:** **0/10** false acceptances, **0/5** false rejections.

Reproduce every headline number: `verify_r5_evidence.py` (Stage A),
`src/eval/verify_splits.py` (Stage D), `run_stage_e_comparison.py` (Stage E); per-claim
reproduction commands in `reports/final_claims_audit.md`.

## 5. Safety constraints

- Read-only telemetry mode; alarm creation, dispatch, and notifications are **disabled**.
- `configs/release.yaml` pins artifact and manifest hashes; `src/tickets/startup_check.py`
  **fails closed** on missing policy fields, hash mismatches, manifest failures, or any
  enabled unsafe flag without an explicit operator override.
- Frozen evaluation manifests (`data/eval/manifest_*.json`) must never be tuned on; changes
  require a new manifest version and documented reason.

## 6. Known limitations

- Instantaneous breaker trips without precursors are unpredictable (3/29 R3 test events).
- During blackouts there is no electrical visibility; tickets must say unknown.
- SBI/BOB sites have no battery sensors; battery claims are always UNKNOWN there.
- A silent gateway can mask an ongoing outage (connectivity/power ambiguity).
- New sites (<7 days telemetry) fall back to fleet priors.
- Human review **PENDING**; independent semantic accuracy **NOT MEASURED**.

## 7. Project layout (R6 additions)

```
├── configs/
│   ├── config.yaml            # pipeline thresholds, labels, features
│   └── release.yaml           # R6 release flags + pinned artifact/manifest hashes
├── src/
│   ├── tickets/               # policy.py, schema.py, renderer.py, llm_bridge.py,
│   │                          # field_validator.py, startup_check.py
│   └── eval/                  # build_incidents.py, build_splits.py, verify_splits.py,
│                              # run_stage_e_comparison.py
├── data/eval/                 # incidents_table.parquet, train/future/unseen .jsonl + manifests
├── training_data/README.md    # retirement notice for the contaminated R5 benchmark
├── tests/                     # ticket/field/split/stage-E/release/R-series suites
├── reports/
│   ├── phase_r5_correction_notice.md / phase_r5_claim_evidence_ledger.md
│   ├── phase_r5_stage_[b-e]_*.md, phase_r5_stage_e_raw_results.json
│   ├── phase_r5_stage_e_human_review_pack/   # 30 blinded cases, blank forms (PENDING)
│   ├── final_claims_audit.md / project_summary.md / phase_r6_release_readiness.md
├── MODEL_CARD.md              # regenerated R6 model card
└── verify_r5_evidence.py      # Stage A read-only evidence reproduction
```
