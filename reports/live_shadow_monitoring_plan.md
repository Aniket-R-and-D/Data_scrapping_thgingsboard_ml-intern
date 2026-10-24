# ThingsBoard Live Shadow Monitoring & Day-14 Evaluation Plan

**System:** Bank Branch Telemetry Anomaly Detection & 24-Hour Outage Predictor  
**Run ID:** `live_shadow_desktop_2026_10_15`  
**Host Environment:** Dedicated Desktop (`DESKTOP-G2`)  
**Status:** `ACTIVE — SHADOW MONITORING IN PROGRESS`  
**Operational Authorization:** Gate A Read-Only Polling (`config/gate_a_approval.json`)  
**Scheduler:** Windows Scheduled Task `TB_LiveShadow_Desktop_G2` (15-Minute Cadence, Windowless)  
**Safety Invariant:** `LOG_ONLY_NO_DISPATCH` | `experimental_llm_enabled: false` | Zero Effectful Actions  

---

## 1. Executive Summary & Operational Context

The live shadow predictor operates on a dedicated desktop host, executing automated 15-minute polling cycles across the complete fleet of 158 registered bank-branch devices. 

Following the successful migration from the prior unstable laptop environment, the desktop run has achieved:
- **Clean Scheduled Execution:** Scheduled task `TB_LiveShadow_Desktop_G2` executing windowless via `wscript.exe scripts/run_windowless.vbs` wrapping `scripts/run_live_shadow_task.bat`, eliminating interactive `0xC000013A` console close interruptions.
- **Complete Fleet Coverage:** 158 of 158 devices scored on every cycle.
- **Backlog Drained:** Uncapped polling (`--max-backfill-hours 0`) completed with zero backfill-capped devices.
- **Absolute Safety Invariants:** Exactly 0 external dispatches, 0 ThingsBoard alarms created, 0 notifications dispatched, and 0 Qwen LLM calls invoked.
- **Strict Evidence Isolation:** All historical laptop logs remain preserved separately in `data/laptop_historical/` and are never pooled with desktop live data.

### Critical Distinction: Operating vs. Validated
The live shadow system is **operating**, but it is **NOT yet validated**. System validation requires the completion of the full 14-day continuous run, ground-truth outcome verification across closed 24-hour windows, incident-level recall calculation, and real human review of blinded operational tickets.

---

## 2. Daily Health Check Protocol

Operational health is monitored daily using `src/live/daily_health_report.py`. 

### Operational-Only Mandate
Daily health checks track **operational integrity only**. They must **NEVER** include precision, recall, F1, lead time, or outcome-based model performance metrics. Computing outcome metrics before 24-hour outcome windows close or before the 14-day observation horizon is strictly prohibited.

### Monitored Daily Metrics
Each 24-hour operational report records:
1. **Cycles Attempted & Succeeded:** Total scheduled slots (target: 96 per day) and success percentage.
2. **Missed Slots:** Gaps between scheduled cycles exceeding the 15-minute threshold.
3. **Data Fetch Failures:** ThingsBoard API read timeouts, HTTP 5xx errors, or network disconnections.
4. **Alarm Fetch Failures:** Errors encountered during ThingsBoard alarm endpoint polling.
5. **Scoring Errors:** Pipeline exceptions, feature engineering failures, or NaN outputs during scoring.
6. **Validator Failures by Commit:** Output field validation checks grouped by git commit hash.
7. **Fleet Coverage:** Devices scored per cycle (expected: 158/158).
8. **LLM Usage:** Number of calls to the experimental Qwen LLM (expected: strictly 0).
9. **Data Freshness Distribution:** Quantiles (p50, p90, p99, max) of telemetry lag in seconds.
10. **Raw Alert Counts:** Number of raw 15-minute predictions classified as `CONNECTIVITY_ISSUE` or power risk (`CRITICAL`/`HIGH`).
11. **Scheduler Restarts or Gaps:** Logged machine reboots, task restarts, or process terminations.

### CLI Execution
```powershell
.venv\Scripts\python.exe src/live/daily_health_report.py --date 2026-10-15 --out reports/live_shadow_day1_desktop.md
```

---

## 3. Alert Episode Tracking Methodology

To prevent alarm fatigue and accurately assess field operational impact, alerts are tracked as **episodes** rather than raw 15-minute prediction records via `src/live/episode_tracking.py`.

### Episode Definition & Aggregation
A sequence of consecutive 15-minute alerts for a given device is grouped into a single cohesive **Alert Episode**:
- **Continuity Threshold:** Two alert timesteps separated by $\le 30$ minutes belong to the same active episode.
- **Episode Closure:** An episode closes when a device experiences at least 30 minutes without an active alert, or returns to `ONLINE_OK`.
- **Episode Duration:** $\text{Duration} = T_{\text{end}} - T_{\text{start}} + \Delta t_{\text{cadence}}$.

### Daily Episode Accounting
For each monitoring day, the following episode lifecycle metrics are reported:
- **New Connectivity Episodes:** Episodes initiated on the current calendar day.
- **Open Connectivity Episodes:** Episodes active at the close of the reporting period.
- **Closed Connectivity Episodes:** Episodes that ceased during the reporting period.
- **New Power-Alert Episodes:** New power risk episodes (`HIGH` or `CRITICAL`).
- **Open Power-Alert Episodes:** Active power risk episodes remaining open.
- **Closed Power-Alert Episodes:** Power risk episodes resolved during the day.
- **Cross-Episode Overlap:** Instances where a device experienced concurrent or overlapping connectivity and power episodes.
- **Normalized Alert Rate:** Episode-level alerts per device per week:
  $$\text{Alerts / Device-Week} = \frac{\text{Total Episodes}}{\text{Fleet Size} \times (\text{Days Monitored} / 7)}$$

Raw 15-minute alert repetitions are reported alongside the deduplication factor:
$$\text{Deduplication Ratio} = \frac{\text{Raw 15-min Alert Count}}{\text{Cohesive Episode Count}}$$

---

## 4. Day-14 Outcome Evaluation Harness

The formal validation evaluation is prepared in `src/live/prepare_day14_evaluation.py`. It is locked against premature execution until the 14-day continuous observation window has fully elapsed.

### Strict Evaluation Constraints
1. **14-Day Maturity Guard:** The evaluation script verifies that $\ge 14$ distinct calendar days of continuous desktop data are recorded before executing.
2. **Closed 24-Hour Windows Only:** A prediction made at time $t_{\text{pred}}$ is eligible for outcome evaluation **if and only if** follow-up telemetry exists up to $t_{\text{pred}} + 24\text{ hours}$. Predictions with pending or partial follow-up are strictly excluded.
3. **Exclusion Accounting:** All excluded predictions are logged with unambiguous reason codes:
   - `INSUFFICIENT_FOLLOWUP_HORIZON`: Window has not yet reached 24 hours.
   - `TELEMETRY_GAP_IN_FOLLOWUP`: Ground-truth telemetry during the 24-hour window was missing or disconnected.
   - `DEVICE_REMOVED_FROM_FLEET`: Device de-registered during the window.
4. **R2 Label Ground-Truth Definition:** Outage occurrence is evaluated against the audited Phase R2 label definition: confirmed AC mains loss resulting in physical power failure or verified outage recovery, strictly excluding connectivity-only blackouts.
5. **No Future Information Leakage:** Features and risk scores are derived strictly from data up to $t_{\text{cutoff}}$. Future data is used exclusively to evaluate outcome labels.
6. **Incident-Level vs. Timestep Metrics:**
   - **Timestep-Level:** Precision, Recall, and F1 across all evaluated 15-minute prediction slots.
   - **Incident-Level Recall:** Proportion of distinct ground-truth power outage incidents that received an advance alert $\ge 30$ minutes prior to outage onset.
   - **Lead Time Distribution:** Time difference between initial alert emission and ground-truth outage onset (mean, median, min, max).
7. **Empirical Bootstrap Confidence Intervals:** 95% confidence intervals are computed using 1,000 bootstrap iterations over evaluation clusters to account for device-level correlation.
8. **Outcome Separation:** Power outage prediction performance is evaluated completely independently from connectivity outage detection.
9. **Zero Run Pooling:** Laptop historical data (`data/laptop_historical/`) is never merged or pooled with desktop evaluation records.

---

## 5. Human Review Protocol (Desktop 30-Case Pack)

To enable verified claims of semantic accuracy and operational ticket validity, a second blinded 30-case human review pack has been prepared in `reports/desktop_human_review_pack/`.

### Review Pack Stratification
- **10 Normal Operation Cases:** `ONLINE_OK` state, `LOW` risk tier.
- **10 Connectivity Issue Cases:** `STALE_DATA` and `OFFLINE_LONG` telemetry blackouts.
- **10 Power-Risk / Borderline Cases:** Active `ONLINE_POWER_LOSS`, `CRITICAL`, `HIGH`, or borderline probability records.

### Blinding & Anti-Bias Controls
- **Randomized Presentation:** Cases are pseudo-randomly shuffled (`DESK-LIVE-0001` through `DESK-LIVE-0030`) so reviewers cannot infer operational severity from case ordering.
- **Blank Scoring Forms:** All evaluation fields in `blank_review_forms_30_cases.json` are initialized to `null`.
- **Separate Sealed Blinding Key:** The true mapping of case IDs to device names, bank branches, model probabilities, and stratum classifications is stored separately in `blinding_key_DO_NOT_OPEN_DURING_REVIEW.json`.
- **Zero Simulated Scores:** No programmatic scores or synthetic reviewers are populated. Cohen's kappa remains `null` until independent human reviewers complete and sign review forms.

### Scoring Rubric Dimensions
1. **Factual Consistency (1–5):** Agreement with input telemetry timestamps, voltages, and rules.
2. **Claim Grounding (PASS / FAIL):** Absence of hallucinated hardware, invented rules, or ungrounded claims.
3. **Operational Actionability (1–5):** Precision and safety of recommended investigation checks.
4. **Harmful / Misleading Guidance (YES / NO):** Risk of misdirecting field personnel (e.g. dispatching power crews to connectivity drops).
5. **Reviewer Operational Verdict (`APPROVE` / `SUPPRESS`):** Field suitability decision.

---

## 6. Incident Escalation & Pause Rules (Defined, Non-Enabled)

The following rules define the exact conditions under which the live shadow run must be paused. These rules are **formalized for operational governance** and are monitored during daily health checks.

| Trigger Condition | Threshold / Condition | Immediate Action |
| :--- | :--- | :--- |
| **Consecutive Failed Cycles** | $\ge 3$ consecutive 15-minute cycles fail or terminate with non-zero exit code | Pause scheduled task, retain logs, record `CONSECUTIVE_CYCLE_FAILURES` |
| **Repeated Fetch Errors** | ThingsBoard API fetch failure rate $> 5\%$ of fleet across 2 consecutive cycles | Pause scheduled task, verify network/credential state, log `PERSISTENT_FETCH_ERROR` |
| **Validator Failure Spike** | Ticket field-validator failure rate $> 0.1\%$ or any unverified commit violation | Pause pipeline, isolate failing device payloads, log `VALIDATOR_VIOLATION_SPIKE` |
| **Unexpected LLM Usage** | Any non-zero count of Qwen LLM calls ($> 0$ calls detected in audit log) | Immediate emergency stop, verify config freeze, log `UNAUTHORIZED_LLM_INVOCATION` |
| **Missing Release Artifacts** | Release artifact file missing or SHA256 checksum mismatch detected at cycle start | Halt execution, trigger environment integrity audit, log `RELEASE_ARTIFACT_CORRUPTION` |
| **Gate A Revocation** | `config/gate_a_approval.json` modified, missing, or revoked by tenant owner | Halt polling immediately, purge cached credentials, log `GATE_A_AUTHORIZATION_EXPIRED` |

### Pause Execution Procedure
If any pause condition is triggered:
1. The scheduled task must be safely disabled via `schtasks /Change /TN TB_LiveShadow_Desktop_G2 /Disable`.
2. All logs, predictions, and lock states must be frozen and preserved without deletion.
3. A signed incident record must be written to `reports/incidents/run_pause_<timestamp>.json` documenting the triggering rule, diagnostic stack trace, and system state.
4. Engineering leads must be alerted before any restart is attempted.

---

## 7. The Standard for System "Validation"

Under no circumstances should the system be described as "validated" prior to satisfying all of the following criteria:

1. **Continuous Operation:** At least 14 consecutive days of scheduled operation without unaddressed scheduler gaps or unhandled failures.
2. **Documented Ground Truth:** A verified count of confirmed power outage incidents across the fleet during the 14-day window.
3. **Closed-Window Scoring:** Outcome scoring evaluated exclusively on predictions with complete 24-hour follow-up.
4. **Dual-Level Metrics:** Documented incident-level recall and lead time alongside timestep-level precision, recall, and F1.
5. **Empirical Alert Rates:** Documented episode-level false alert rate per device per week.
6. **Zero Scheduler Anomalies:** Full accounting of all 1,344 scheduled 15-minute time slots (96 cycles/day $\times$ 14 days).
7. **Independent Human Review:** Completed review forms signed by human operations reviewers evaluating the blinded 30-case sample.

### Approved Portfolio Wording (During Shadow Mode)
Until the Day-14 evaluation is completed and audited, the only accurate and truthful portfolio description is:

> *"Deployed a read-only live shadow scorer on a dedicated desktop, with append-only audit logs, Gate A authorization enforcement, episode-level alert tracking, and a 14-day outcome-evaluation plan. The system remains advisory-only: dispatch, alarms, notifications, and the experimental LLM are disabled."*
