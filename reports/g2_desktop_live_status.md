# G2 Desktop Live Shadow Status

**Document Time (UTC):** 2026-10-15T12:22:00Z  
**Desktop Host:** Windows 11 Pro Desktop Workstation (AC power, non-sleeping)  
**Scheduled Task:** `TB_LiveShadow_Desktop_G2` (interval: 15 minutes, execution limit: 14 minutes)  
**Task Action:** Windowless execution via `wscript.exe scripts\run_windowless.vbs` wrapping `scripts\run_live_shadow_task.bat`

---

## 1. Run Separation & Lineage Statement

> [!IMPORTANT]
> **Strict Run Separation:** The laptop historical run and the desktop live shadow run are completely independent and cleanly separated. Local logs and historical evidence from the laptop have **NOT** been merged, pooled, or backfilled into this desktop run.

| Environment | Run ID / Scope | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Laptop (Historical)** | `TB_LiveShadow_G2` | **PAUSED** | Historical evidence preserved in `reports/g2_live_shadow_status.md` and repository records. Paused following scheduled-task 0xC000013A failures and model weights incident. |
| **Desktop (Current)** | `TB_LiveShadow_Desktop_G2` | **ACTIVE / OPERATIONAL** | Clean desktop deployment. Running production deterministic ticket renderer + Platt-calibrated LightGBM clean model (`remediated_lightgbm_clean.joblib`). Backlog fully drained. |

---

## 2. Desktop Run Identity & Configuration

* **Start Time (First Successful Scheduled Cycle):** `2026-10-15T11:00:00+00:00` (Local: `2026-10-15 16:30:17`)
* **Git Commit Hash:** `b502cfc03a4547ec484c7091f51788b64eee34d9` (`main`)
* **Gate A Approval Status:** **APPROVED / VALID** on this desktop (`Tenant Owner`, `Tenant Administrator`, `2026-10-15`)
* **Operating Mode:** Read-only ThingsBoard telemetry polling for 158 registered branch devices.
* **Effectful Outputs:** All disabled (`alarm_creation: disabled`, `dispatch: disabled`, `notifications: disabled`).
* **LLM Commentary (Qwen):** `experimental_llm_enabled: false` (Zero LLM calls).
* **Backlog Drain Status:** Drained (1h -> 6h -> 24h -> uncapped `--max-backfill-hours 0`, `backfill_capped_devices: 0`).
* **Current Scheduler State:** `Ready` (running every 15 minutes).

---

## 3. Operational Integrity & Safety

* **Cycles Succeeded:** All scheduled cycles succeeded with status `OK`.
* **Dispatches / Alarms Created / Notifications Sent:** `0 / 0 / 0`
* **Fetch Failures / Alarm Fetch Failures:** `0 / 0`
* **Action Taken:** `LOG_ONLY_NO_DISPATCH` only.
* **Lock State:** Clean (no stale lock).
