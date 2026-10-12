# PRD: Dexter HMS Telemetry Anomaly & Fault Prediction Platform

## 1. Overview

The product is a telemetry-driven anomaly and fault detection system for Dexter HMS devices deployed across branches. It harvests device telemetry from ThingsBoard, scores device-day health, surfaces likely outage or fault conditions, and provides actionable alerts to operations teams.

The system aims to reduce manual monitoring effort, detect branch/device degradation earlier, and provide a repeatable ML-based decisioning layer grounded in operational evidence.

## 2. Problem Statement

Operations teams currently rely on device health snapshots, manual checks, and rule-based interpretations of telemetry. This is slow, hard to scale, and inconsistent across branches and devices.

Key issues:
- Device fleet size is large and heterogeneous.
- Device telemetry keys vary by device and may drift over time.
- Some telemetry keys are missing or under-reported, making a single static rule set unreliable.
- Fault events need to be triaged quickly and explained clearly to operations.
- Daily/weekly operational visibility is required without manual spreadsheet analysis.

## 3. Product Goal

Build a reliable system that:
- harvests device telemetry and history from ThingsBoard
- identifies likely fault or outage conditions at device and branch level
- explains why a device was flagged
- supports operational alerting and dashboard visibility
- learns from real-world outcomes over time

## 4. Users and Stakeholders

Primary users:
- Operations / field support teams
- Branch or fleet reliability teams
- Data/ML engineering team

Secondary users:
- Business leaders who want a fleet health overview
- Engineering teams monitoring schema drift or telemetry regressions

User needs:
- know which devices/branches are at risk today
- understand the reason for each alert
- avoid excessive false positives
- access historical trends and root-cause signals
- see which telemetry keys are changing or disappearing

## 5. Objectives

- Detect high-risk device-days with high recall for CRITICAL faults.
- Provide explainable alerts using the top contributing telemetry reasons.
- Reduce manual triage effort for operations teams.
- Build a nightly automated pipeline that runs without human intervention.
- Support future model improvement using operator feedback.

## 6. Non-Goals

- Full real-time streaming analytics at millisecond latency
- Full replacement of human investigation workflows
- General-purpose IoT platform beyond this fleet monitoring use case
- High-confidence production model before data validation

## 7. Functional Requirements

FR1. Data harvesting
The system shall retrieve telemetry, device metadata, alarms, events, and relations from ThingsBoard for eligible devices.

FR2. Device eligibility filtering
The system shall classify devices as eligible or excluded based on real vs test/demo/simulator signals and configured allow/deny policies.

FR3. Telemetry key discovery
The system shall discover telemetry keys automatically and track which keys are active across devices over time.

FR4. Daily scoring
The system shall compute a daily device-level fault score based on telemetry behavior and historical patterns.

FR5. Alerting
The system shall flag devices or branches as HEALTHY, WARNING, or CRITICAL, with a clear threshold model.

FR6. Explainability
Every high-severity alert shall include the top reasons for the score, such as recurring anomalies, missing heartbeats, or abnormal telemetry patterns.

FR7. Nightly automation
The system shall run on a fixed schedule, produce outputs, and notify stakeholders via configured alerts.

FR8. Drift monitoring
The system shall detect:
- new telemetry keys appearing across many devices
- active keys dropping to zero or near-zero coverage over consecutive days

FR9. Dashboard visibility
The system shall provide operational views for:
- fleet health summary
- per-branch drill-down
- device deep-dive

FR10. Feedback loop
The system shall allow operational verdicts (true fault / false alarm / inconclusive) to be captured and incorporated into future model retraining.

## 8. Non-Functional Requirements

NFR1. Reliability
The system must be robust to partial failures, resume interrupted runs, and handle telemetry fetch interruptions gracefully.

NFR2. Performance
The system must process large daily telemetry volumes without exhausting memory, particularly for long lookback windows.

NFR3. Explainability
Outputs must be understandable to operations staff without requiring deep ML knowledge.

NFR4. Security
No credentials or sensitive secrets should be committed to the repository. Device IDs and operational data must be reviewed for privacy/compliance concerns.

NFR5. Maintainability
The pipeline must be modular, readable, and aligned with reproducible data/ML workflows.

NFR6. Auditability
Run outputs must include manifests, coverage summaries, and completeness reports so any alert can be traced to data provenance.

## 9. Key Metrics / Success Criteria

Primary KPIs:
- CRITICAL recall on true device-day faults
- reduction in false positives relative to manual inspection
- number of alerts per day within operations capacity
- time to detect real device degradation

Operational KPIs:
- nightly run success rate
- number of branches with active issues
- number of previously unknown telemetry keys detected
- percent key coverage retention over time

Threshold definitions:
- Model must outperform the rule baseline by a meaningful margin.
- Ops should be able to act on alerts without needing to investigate each device manually.
- Alert volume should match investigation capacity (for example, ~30 alerts/day).

## 10. User Experience Requirements

- Daily summary should answer: “Which branches need attention today?”
- Alert cards should provide:
  - device/branch name
  - severity
  - top 3 reasons
  - time window
  - relevant historical trend
- Dashboard should allow drill-down from fleet summary to branch and device detail within under 30 seconds.

## 11. Assumptions

- Device metadata and telemetry are available in ThingsBoard.
- At least a subset of historical telemetry is sufficient to train a baseline model.
- Device/fleet schema may evolve; discovery and monitoring must account for drift.
- A small but reliable subset of true fault labels can be established from auditing or ops reporting.

## 12. Risks and Mitigations

Risk: Spec keys never appear consistently
Mitigation: support legacy-key scoring and reevaluate after firmware migration.

Risk: JWT expiry interrupts harvest
Mitigation: auto-refresh and retry logic.

Risk: Low prevalence of critical faults
Mitigation: use stratified sampling, class weighting, or anomaly-based fallback.

Risk: Branch-level data quality inconsistency
Mitigation: score and surface coverage quality separately.

Risk: Schema drift
Mitigation: automatic discovery and alerting on missing or new telemetry keys.

Risk: Confidential or sensitive identifiers
Mitigation: review before external model training or external sharing.

## 13. Release Phases

### Phase 0: Validate current data quality
- Confirm scoring reflects real outages
- Sanity-check outputs before model training

### Phase 1: Train first model baseline
- Build baseline classification on device-day examples
- Compare against rule-based scoring

### Phase 2: Threshold tuning and actionability
- Optimize alert thresholds
- Add reason explanations and runbook guidance

### Phase 3: Productionize nightly run
- Fix automation and scheduling
- Add notifications and storage hygiene

### Phase 4: Drift detection
- Monitor telemetry key changes and regressions

### Phase 5: Dashboard MVP
- Fleet overview, branch drill-down, device deep-dive

### Phase 6: Feedback loop
- Capture operator outcomes and retrain monthly

## 14. Acceptance Criteria

The product is considered ready for meaningful operational pilot when:
- the nightly pipeline completes without manual intervention
- alerts are generated for known real faults in the data
- at least top 3 reasons are available for each alert
- ops users can understand and act on each alert
- the dashboard answers common fleet health questions in under 30 seconds
- the telemetry drift monitoring alerts on key changes or disappears

## 15. Recommended Next Step

The immediate next milestone should be Phase 0: validate that current scoring aligns with known real device faults before advancing to model training. This is the critical gate that determines whether the downstream ML work is based on trustworthy signal.

