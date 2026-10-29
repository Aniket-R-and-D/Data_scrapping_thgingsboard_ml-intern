# TEST_INFRA — SEPLE NovaEdge AI Operations Engine Behavioral Test Infrastructure

## 1. Test Philosophy & Architecture

The SEPLE NovaEdge AI Operations Engine operates across 7 core production AI components spanning unsupervised anomaly scoring, local explainability, 24-hour predictive outage forecasting, continuous monthly retraining, telemetry drift monitoring, hardware recovery state machines, and multi-channel reporting on serverless AWS ECS Fargate.

This test infrastructure implements an **authoritative, opaque-box, requirement-driven behavioral testing methodology** designed to eliminate testing facades, superficial mocks, and artificial stubs:

1. **Opaque-Box Requirement-Driven Testing**: System components are exercised strictly through their public operational interfaces, input datasets, and mathematical contracts as defined in `ORIGINAL_REQUEST.md` and `PROJECT.md`.
2. **Zero Facades & Zero Mock Leaks**: Real production model bundles (`models/gbm_outage_oct2026_365d/`, `models/isolation_forest_365d/`), real fleet parquet datasets (`models/latest_fleet_scored.parquet`, `models/latest_fleet_features.parquet`), and real statistical estimators (`scikit-learn`, `scipy.stats`, `numpy`, `pandas`) are executed directly.
3. **Dry-Run & Mock-Free Isolation**: Tests execute in complete offline isolation. All external network dispatches (live Slack incoming webhooks, SMTP email connections, ThingsBoard REST endpoints, AWS cloud APIs) are prevented by invoking standard `--dry-run` operational pathways and local test fixtures, ensuring zero unintended production mutations.
4. **Empirical Verification**: Every assertion evaluates concrete numerical values, statistical bounds, strict schema structures, or deterministic state transitions derived from authoritative project specifications.

---

## 2. 4-Tier Test Methodology

The test suite is structured into four progressive testing tiers, mapping all 15 project features and R1–R5 requirements:

```
+--------------------------------------------------------------------------------------------------+
|                                    4-Tier Test Methodology                                       |
+--------------------------------------------------------------------------------------------------+
|  Tier 1: Feature Coverage                                                                        |
|  - Anomaly Baseline & Score Formulas (S(x) = -decision_function(x))                              |
|  - Local Feature Attributions (Z-scores & Counterfactuals Δ_j)                                   |
|  - Calibrated 24h Outage Predictions (544 Features, HistGradientBoosting)                        |
|  - 5-Tier Unified Risk Classification (CRITICAL_FAULT, HIGH_RISK, etc.)                          |
|  - Champion-Challenger Metric Gates (ROC-AUC >= 0.60, Brier <= 0.30, PR-AUC >= 85%)             |
|  - Retraining Promotion & Rollback Backup Safety (model.joblib.bak)                              |
|  - Telemetry Drift Mathematics (PSI, Wasserstein Distance) & CloudWatch                          |
|  - Hardware Recovery Gate Criteria & Freshness Rules                                             |
|  - 3-Cycle Debounce State Machine (30-min Elapsed Window)                                        |
|  - Multi-Channel Digest Schema (HTML DOM, Markdown, 16-Col CSV, Slack JSON, TB Attrs)             |
|  - Serverless ECS Fargate Runtime & Scheduling Contracts                                         |
+--------------------------------------------------------------------------------------------------+
|  Tier 2: Boundary & Corner Conditions                                                            |
|  - Anomaly score inverted bounds and probability boundaries [0.0, 1.0]                           |
|  - Exact decision threshold cutoffs (0.80, 0.50, 0.40, 0.30, 0.25)                               |
|  - PSI zero-variance baselines, identical distributions, and epsilon smoothing limits            |
|  - Debounce cycle threshold boundaries (2 vs 3 cycles, 29.9 min vs 30.0 min elapsed)             |
|  - Voltage sag exact triggers (AC 169.9V vs 170.0V, Battery 11.4V vs 11.5V)                      |
|  - Retraining gate boundary rejection (ROC-AUC 0.599, Brier 0.301, PR-AUC 84.9%)                 |
+--------------------------------------------------------------------------------------------------+
|  Tier 3: Multi-Component Combinations & Workflows                                                |
|  - End-to-end Fleet Pipeline: Features -> Anomaly -> Explainer -> Outage -> 5-Tier -> Digest     |
|  - Recovery Precedence Override: Debounce Gate converts CRITICAL_FAULT to SERVICED_RECOVERED     |
|  - Retraining Workflow: Ingest -> Train Challenger -> Gate -> Force Promote -> S3/Bak Backup     |
|  - Multi-Channel Cross-Format Synchronization (HTML, MD, CSV, Slack, TB have identical counts)   |
+--------------------------------------------------------------------------------------------------+
|  Tier 4: Real-World Workloads, Operational Stress & Isolation                                    |
|  - Corrupted Telemetry: Missing columns, all-NaN rows, extreme noise, malformed JSON state       |
|  - Mid-Debounce Voltage Sag Interruption: 2 cycles nominal -> Sag -> Counter zeroed              |
|  - Offline Dry-Run Isolation: Zero live network sockets, zero Slack/SMTP/TB external leaks       |
|  - AWS ECS Fargate & Cloud Runtime Specifications Compliance                                     |
+--------------------------------------------------------------------------------------------------+
```

---

## 3. Feature Inventory & Tier Mapping

| # | Feature Name | Component | Tier | Target Source Module | Authoritative Specification |
|---|--------------|-----------|:----:|----------------------|-----------------------------|
| 1 | Fleet Anomaly Baseline & Score Formulas | C1: Anomaly Scoring | Tier 1, 2 | `models/latest_fleet_scored.parquet`, `models/isolation_forest_365d/` | 111 NORMAL, 7 ELEVATED, 8 ANOMALOUS, 4 CRITICAL_FAULT; $S(x) = -\text{decision\_function}(x)$ |
| 2 | Local Feature Attributions | C2: Feature Attribution | Tier 1, 3 | `src/predict/explainer.py` | $\Delta_j = P(Y=1 \mid x) - P(Y=1 \mid x_{-j}, \text{baseline\_median}_j)$; FEATURE_TAXONOMY mapping |
| 3 | Calibrated 24h Outage Prediction | C3: 24h Outage Model | Tier 1, 2 | `models/gbm_outage_oct2026_365d/` | 544 features, HistGradientBoostingClassifier, probabilities in $[0, 1]$ |
| 4 | 5-Tier Unified Risk Classification | C3: Unified Risk Engine | Tier 1, 2, 3 | `src/predict/send_daily_risk_digest.py` | CRITICAL_FAULT, HIGH_RISK, ELEVATED/MODERATE_MONITOR, NOMINAL/NORMAL_STABLE, SERVICED_RECOVERED |
| 5 | Champion-Challenger Metric Gates | C4: Retraining Pipeline | Tier 1, 2 | `src/predict/monthly_retrainer.py` | ROC-AUC >= 0.60, Brier score <= 0.30, PR-AUC retention >= 85% vs Champion |
| 6 | Retraining Workflows & Rollback Safety | C4: Retraining Pipeline | Tier 1, 3 | `src/predict/monthly_retrainer.py` | `--retrain-now`, `--force-promote`, `model.joblib.bak` creation, rollback invariance |
| 7 | Telemetry Drift Mathematics | C5: Drift Monitoring | Tier 1, 2 | `src/features/drift_monitor.py` | $PSI = \sum (P_i - Q_i) \ln(P_i/Q_i) \in [0, 10]$; Standardized $W(P, Q) / \sigma$; Thresholds 0.10, 0.25 |
| 8 | CloudWatch Telemetry Drift Contracts | C5: Drift Monitoring | Tier 1, 4 | `src/features/drift_monitor.py` | Namespace `NovaEdge/TelemetryDrift`; metrics: OverallDriftSeverity, MeanPSI, CriticalDriftFeatureCount |
| 9 | Hardware Recovery Gate & Criteria | C6: Recovery Gate | Tier 1, 2 | `src/live/recovery_gate.py` | Telemetry freshness <= 60m, battery >= 12.0V, AC >= 170V, mains_on=True, zero open critical tickets |
| 10 | 3-Cycle Debounce Window & Anti-Sag Reset | C6: Debounce Engine | Tier 1, 2, 4 | `src/live/alarm_clear_engine.py` | 3 consecutive cycles (15m interval), >= 30 min elapsed; immediate reset on AC < 170V or battery < 11.5V |
| 11 | Multi-Channel Digest Schema Validation | C7: Multi-Channel Reporting | Tier 1, 3 | `src/predict/send_daily_risk_digest.py` | Responsive HTML, Markdown brief, CSV with 16 required columns, Slack Block Kit, TB attributes |
| 12 | Dry-Run & Mock-Free Isolation | C7: Multi-Channel Reporting | Tier 3, 4 | `src/predict/send_daily_risk_digest.py` | Offline execution on real local files; 0 live network calls, 0 SMTP, 0 Slack, 0 TB mutations |
| 13 | ECS Fargate & Cloud Runtime Contracts | C7: Serverless Runtime | Tier 1, 4 | `scripts/cloud/deploy_to_ecs_fargate.py`, `PROJECT.md` | Task def (512 CPU, 1024 RAM, Fargate), EventBridge (cron(0 6 * * ? *), rate(15 minutes)), S3 prefix `novaedge-ai-reports`, EC2 isolation |
| 14 | Standalone Verification Suite & Runner | Integrated Suite | All | `tests/behavioral/test_e2e_acceptance_and_isolation.py` | Pytest executable standalone test file, comprehensive behavioral assertions |
| 15 | Empirical Behavioral Test Report | Validation Reporting | All | `reports/empirical_behavioral_validation_report.md` | Quantitative metrics, pass/fail results across all 7 operational AI components |

---

## 4. Test Suite Execution & Pass/Fail Semantics

### Execution Command
The standalone end-to-end acceptance and isolation test suite is executed using pytest:

```powershell
.venv\Scripts\pytest -v tests/behavioral/test_e2e_acceptance_and_isolation.py
```

### Pass/Fail Criteria
- **Pass (100%)**:
  1. All tests complete with exit code 0.
  2. All empirical assertions match expected distributions, thresholds, and statistical boundaries without relaxed epsilon hacks.
  3. All 16 CSV audit columns are verified for structure, data types, and ordering.
  4. 0 network calls escape the local environment during dry-run testing.
  5. 0 mocks or facades are used in place of production algorithms or artifact files.
- **Fail**:
  - Any assertion failure, unhandled runtime exception, missing schema column, mock leak, or unverified contract.

---

## 5. Directory Layout

```
c:\workspace\Data_scrapping_thgingsboard_ml-intern\
├── TEST_INFRA.md                                  # (This document) Test infrastructure architecture & 4-tier plan
├── TEST_READY.md                                  # Published upon test suite completion
├── tests/
│   └── behavioral/
│       ├── __init__.py                            # Module marker
│       └── test_e2e_acceptance_and_isolation.py  # Standalone Opaque-Box Acceptance Suite
├── models/
│   ├── latest_fleet_scored.parquet                # Baseline fleet scored parquets (130 branches)
│   ├── latest_fleet_features.parquet              # Baseline fleet features (544 features)
│   ├── isolation_forest_365d/                     # Anomaly scoring models & metadata
│   └── gbm_outage_oct2026_365d/                   # Outage predictor HistGradientBoosting model & metadata
├── src/
│   ├── features/drift_monitor.py                  # Telemetry drift computation (PSI, Wasserstein)
│   ├── live/
│   │   ├── recovery_gate.py                       # Real-time recovery evaluation gate
│   │   └── alarm_clear_engine.py                  # 3-cycle debounce state machine & anti-sag logic
│   ├── predict/
│   │   ├── explainer.py                           # Local counterfactual feature attribution
│   │   ├── monthly_retrainer.py                   # Continuous retraining pipeline & champion-challenger gate
│   │   └── send_daily_risk_digest.py              # Multi-channel reporting engine & dry-run orchestrator
│   └── cloud/
│       ├── cloudwatch_metrics.py                  # CloudWatch metrics contracts
│       └── s3_archiver.py                         # S3 cloud archival
└── scripts/
    └── cloud/deploy_to_ecs_fargate.py             # ECS Fargate & EventBridge deployment specification
```
