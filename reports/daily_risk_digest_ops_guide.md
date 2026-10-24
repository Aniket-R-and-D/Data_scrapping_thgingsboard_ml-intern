# ThingsBoard AI Risk Digest — Operations & Field Team Guide

**Target Audience:** SEPLE NovaEdge Operations Team, Field Technicians, and Technical Leads  
**Platform:** ThingsBoard IoT Cloud ([https://seple.iot-private.cloud](https://seple.iot-private.cloud))  
**Fleet Scope:** 130 Monitored Bank Branches (Bank of Baroda, Bank of India, Canara, DX)  
**AI Engine Version:** `365d_v1.0` (Trained on 154.5M raw telemetry points and 130k alarm events)

---

## 1. Executive Summary

The **Daily AI Risk Digest** is an automated morning intelligence service that evaluates all 130 bank branches across two predictive engines:
1. **Unsupervised Fleet Anomaly Scorer (Isolation Forest):** Evaluates 580 baseline features across 28-day moving windows to catch subtle drift, chronic sensor degradation, and irregular energy signatures.
2. **Supervised Electrical Outage Predictor (Gradient Boosting):** Evaluates electrical brownouts (`ac_voltage < 50V`, battery collapse `< 10.8V`) to predict power loss or device failure within a **24-hour horizon** with **85% precision** at **99.3% recall**.

Every morning at **08:00 AM IST**, the engine runs, analyzes the fleet, updates ThingsBoard, and dispatches an actionable briefing to the operations team.

---

## 2. How the Team Uses the AI

### A. Inside Existing ThingsBoard Dashboards
Because the team already operates on ThingsBoard, **no separate website is needed**. The AI pushes intelligence directly into the devices you already monitor:

1. **Live Device Server-Side Attributes:**
   Every device on ThingsBoard now carries:
   - `ai_health_tier`: `CRITICAL_FAULT`, `HIGH_RISK`, `ELEVATED`, or `NOMINAL`
   - `ai_outage_risk_prob`: Probability of an outage within the next 24 hours (e.g. `0.94` = 94%)
   - `ai_anomaly_score`: Standard deviation distance from healthy fleet baseline
   - `ai_primary_issue`: Specific root cause (e.g. `battery_voltage__gap_max_7d`, `ac_voltage undervoltage`)
   - `ai_recommended_action`: Concrete technician action instructions
   - `ai_last_evaluated`: Exact timestamp of the latest AI evaluation

2. **Active ThingsBoard Alarms:**
   - Any branch with `CRITICAL_FAULT` or Outage Risk $\ge 50\%$ automatically raises an active alarm in ThingsBoard:
     - `AI_CRITICAL_ANOMALY` (Severity: `CRITICAL`)
     - `AI_PREDICTIVE_OUTAGE_RISK` (Severity: `MAJOR` or `CRITICAL`)
   - These alarms immediately appear on your existing dashboards:
     - [Branch List DashBoard BOB](https://seple.iot-private.cloud/dashboards/02c276f0-7760-11f0-8ce1-27426fb87ede)
     - [Daily Report Integrated Alarm System](https://seple.iot-private.cloud/dashboards/0a70cb70-7762-11f0-abfd-39224412c8a8)
     - [UpTime count New Structure](https://seple.iot-private.cloud/dashboards/02cea8b0-58bc-11f1-9463-fde182b082f4)
     - [Novaedge Map](https://seple.iot-private.cloud/dashboards/094b6110-8fdf-11f1-9dac-bfdc7cccc90b)

---

### B. Morning Slack Alert
- Delivered to the team Slack channel before field technicians deploy.
- **Content:**
  - Executive Fleet Health Score (e.g. `52% nominal`, `30 critical branches`).
  - Top priority branches with outage probabilities and root causes.
  - Direct deep links to open each branch device directly on ThingsBoard.
  - Quick action buttons to open the ThingsBoard BOB Branch Dashboard.

> [!TIP]
> **Configuring Slack Incoming Webhook:**  
> In [.env](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/.env), ensure `SLACK_WEBHOOK` is set to an Incoming Webhook URL starting with `https://hooks.slack.com/services/...`.  
> If it currently points to a user profile URL (`https://seplenovaedgepvtltd.slack.com/team/...`), create a webhook in your Slack workspace App settings and paste it into `.env`.

---

### C. Executive Email Digest
- Sent to `animesh.choudhury@seple.in` (and designated technical contacts).
- **Format:** High-resolution HTML brief with color-coded risk badges, summary metrics, and technician dispatch checklists.
- **Attachment:** Attached `daily_risk_digest_YYYYMMDD.csv` containing the full 130-branch status matrix.

> [!NOTE]
> **Gmail SMTP Authentication:**  
> Gmail requires a 16-character **App Password** for automated scripts. If standard password authentication returns an error, generate an App Password at [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) and update `SMTP_PASSWORD` in `.env`.

---

## 3. Risk Tier Definitions & Operations SLA

| Tier | Outage Risk / Anomaly | SLA Response | Field Team Action |
|:---|:---:|:---:|:---|
| 🚨 **CRITICAL_FAULT** | Outage Prob $\ge 80\%$ or Anomaly Score $\ge 0.0475$ | **Same-day dispatch** (< 4 hours) | Immediate physical inspection of 12V battery pack, mains phase line, and edge controller power supply. |
| ⚠️ **HIGH_RISK** | Outage Prob $\ge 50\%$ or Anomaly Score $\ge 0.0106$ | **Next-day dispatch** (< 24 hours) | Remote diagnostics verification via ThingsBoard telemetry; dispatch technician to test UPS backup and signal strength. |
| 🟡 **ELEVATED** | Outage Prob $\ge 30\%$ or Anomaly Score $\ge -0.0137$ | **Within 7 days** | Schedule routine check during regular branch maintenance visit. |
| ✅ **NOMINAL** | Healthy Baseline | **Standard monitoring** | No field action needed; operating within verified electrical tolerances. |

---

## 4. Technician Field Intervention Playbook

When dispatched to a branch flagged by the AI, follow this diagnostic sequence:

### Scenario A: Backup Battery Degradation (`battery_voltage` anomaly)
- **Symptom:** AI flags min battery voltage dropping below 10.8V or severe variance.
- **Technician Steps:**
  1. Measure open-circuit DC voltage on the 12V lead-acid / lithium backup pack with a digital multimeter.
  2. Perform an artificial mains disconnect test to observe battery drop rate under load.
  3. If voltage sags below 11.0V in under 10 minutes, replace battery pack.
  4. Verify UPS trickle charging circuit is supplying 13.6V–13.8V float charge.

### Scenario B: Chronic Undervoltage / Phase Dropout (`ac_voltage` anomaly)
- **Symptom:** AI flags mean AC voltage $< 190\text{V}$ or prolonged zero-voltage gaps.
- **Technician Steps:**
  1. Inspect branch input distribution board (DB) and measure Phase-to-Neutral AC voltage.
  2. Check for loose terminal lugs, tripped residual current circuit breakers (RCCB), or burnt fuses.
  3. Verify whether branch stabilizer is operating correctly or stuck in bypass mode.

### Scenario C: Edge Gateway Communication Drops (`errorCount` / `gatewayUptime`)
- **Symptom:** Edge controller reset count spiking or heartbeat gaps $> 4\text{ hours}$.
- **Technician Steps:**
  1. Inspect 4G/LTE antenna placement: relocate antenna outside metal server rack if RSSI $< -85\text{ dBm}$.
  2. Verify SIM card carrier data balance and APN configuration.
  3. Check gateway 12V/2A DC power adapter with an oscilloscope or multimeter for AC ripple voltage ($> 100\text{ mV}$ ripple causes controller micro-resets).

---

## 5. Operations Commands Reference

All automation scripts are in the project root and `scripts/` directory:

```powershell
# 1. Run full daily risk digest with ThingsBoard live sync, Slack & Email
.\.venv\Scripts\python.exe send_daily_risk_digest.py

# 2. Dry run preview (prints executive summary to console, generates reports, no network calls)
.\.venv\Scripts\python.exe send_daily_risk_digest.py --dry-run

# 3. Target specific channels
.\.venv\Scripts\python.exe send_daily_risk_digest.py --sync-tb    # Sync to ThingsBoard only
.\.venv\Scripts\python.exe send_daily_risk_digest.py --slack      # Send Slack alert only
.\.venv\Scripts\python.exe send_daily_risk_digest.py --email      # Send Email brief only

# 4. Windows Task Scheduler management
.\scripts\schedule_daily_digest.ps1              # Register daily task at 08:00 AM IST
.\scripts\schedule_daily_digest.ps1 -Status      # Check scheduler status
.\scripts\schedule_daily_digest.ps1 -RunNow      # Run scheduled task immediately
.\scripts\schedule_daily_digest.ps1 -Remove      # Remove scheduled task
```

---

## 6. Daily Report Outputs

Every execution automatically generates and archives the following files in [reports/](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/reports/):
- **`daily_risk_digest_YYYYMMDD.md`:** Complete Markdown digest with fleet metrics, top 25 high-risk branches, and technician dispatch checklists.
- **`daily_risk_digest_YYYYMMDD.html`:** Clean, responsive HTML morning briefing.
- **`daily_risk_digest_YYYYMMDD.csv`:** Full 130-branch tabular data file with outage probabilities and root causes.
- **`latest_risk_digest.json`:** Machine-readable summary for API integrations or monitoring hooks.
- **`logs/daily_risk_digest.log`:** Persistent execution log tracking every run, ThingsBoard API responses, and notification dispatches.
