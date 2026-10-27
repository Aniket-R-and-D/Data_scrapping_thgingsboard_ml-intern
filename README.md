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

## 7. Project Structure & Organization

```
├── configs/                     # System, model, and feature configurations
│   ├── config.yaml              # Pipeline thresholds, label definitions, feature configs
│   ├── release.yaml             # Release flags, operational defaults, pinned hashes
│   ├── training_config.json     # Feature selection, aggregation windows, leakage exclusion
│   ├── feature_policy.json      # Strict feature governance & allowlist specification
│   ├── key_specs/               # ThingsBoard telemetry key manifests & aliases
│   └── templates/               # Approval and decision record templates
├── deploy/                      # Deployment automation & cloud configs
│   ├── setup_aws_ec2.sh         # EC2 host provisioning & Docker installation script
│   └── rule_chain_backups/      # ThingsBoard root & alarm rule chain backup snapshots
├── docs/                        # Formal documentation, specifications, and runbooks
│   ├── aws_cloud_deployment_guide.md
│   ├── prd.md
│   └── ...
├── models/                      # Production ML models & frozen fleet baselines (< 30 MB)
│   ├── gbm_outage_oct2026_365d/ # 24h predictive gradient boosting outage model
│   ├── isolation_forest_365d/   # Unsupervised fleet anomaly detection model
│   └── latest_fleet_scored.parquet # Fleet baseline snapshots
├── notebooks/                   # Analytical & exploration Jupyter notebooks
│   ├── TB_Full_Harvest_v11.ipynb
│   └── executed/                # Automated batch execution runs
├── reports/                     # Daily digests, audit logs, and performance reports
│   ├── audit/                   # Historical nightly alarm risk & execution logs
│   ├── daily_risk_digest_*.html # Interactive executive morning briefs
│   ├── recovery_events_*.csv    # Real-time hardware restoration logs
│   └── latest_risk_digest.json  # Live fleet scoring payload for dashboards & notifications
├── scripts/                     # Operational, deployment, and analytical tools
│   ├── thingsboard/             # Dashboard modernization, rule chain links, telemetry probes
│   ├── cloud/                   # Cloud runner daemon, EC2 live monitoring, production sync
│   ├── automation/              # Scheduled tasks, Windows service runners, digest dispatchers
│   ├── data_ops/                # Telemetry harvesting, key spec audits, current-state snapshots
│   └── training/                # Offline model training, cross-validation, and fine-tuning
├── src/                         # Modular core Python library
│   ├── ingest/                  # Resilient ThingsBoard client, data & alarm harvesting
│   ├── features/                # Leakage-free feature engineering, training dataset pipelines
│   ├── predict/                 # Daily risk scorer, daily operations digest engine, retrainer
│   ├── rules/                   # Hardware alarm bridges, deterministic rules, nightly chains
│   ├── live/                    # 15-min recovery gates, automatic alarm clear engine, shadow run
│   ├── cloud/                   # AWS CloudWatch metrics heartbeat, Amazon S3 archiver
│   ├── tickets/                 # Diagnostic ticket schema, policy, field validator, renderer
│   ├── anomaly/                 # Autoencoder & Isolation Forest training modules
│   ├── eval/                    # Benchmarking, dataset splits, incident builders
│   └── explain/                 # Root-cause explanation layer, audit evaluators
├── tests/                       # Comprehensive test suite (unit, integration, governance)
├── Dockerfile                   # Hardened multi-stage container image for AWS EC2/ECS
├── docker-compose.yml           # Production service definition with persistent volume mounts
├── requirements.txt             # Full development and training dependencies
├── requirements-prod.txt        # Production runtime dependencies (< 200 MB container footprint)
└── pyproject.toml               # Package specifications
```
