# G2 Status and Live-Shadow Launch Checklist

Status date (UTC): 2026-10-14 (updated 12:25Z)
**Live shadow operation has begun. G2 is not complete: day-1 and day-14 reports are outstanding,
and Gate A (formal owner authorization) is still PENDING.** Current detail: `reports/g2_live_shadow_status.md`.

| Gate | Status |
|---|---|
| A. Owner approval for production reads | PENDING (no approver name/role or date recorded) |
| B. Manual single-cycle validation | PASSED: cycle 2026-10-14T11:30:00Z, status OK |
| C. Scheduler registration + scheduled cycle | PASSED (scheduler start): cycle 2026-10-14T12:00:00Z, status OK |
| D. Day-1 operational report | PENDING (due >= 2026-10-15T11:32Z) |
| E. Day-14 outcome evaluation | PENDING |

## 1. Current status

| Item | Status | Evidence |
|---|---|---|
| L1: startup check enforced in nightly chain | FIXED | `0bdfc74`; `tests/test_release_guards_l1_l2.py` |
| L2: nightly GBM bundle pinned and hash-enforced | FIXED | `0bdfc74`; `configs/release.yaml` `nightly_gbm_*` |
| Live shadow scorer (`src/live/shadow_run.py`) | IMPLEMENTED; OFFLINE-TESTED; RUNNING LIVE since 2026-10-14T11:30Z | `d1a6b1b`, `b44a775`; `tests/test_live_shadow.py` |
| Outcome evaluator (`src/live/outcomes.py`) | IMPLEMENTED, OFFLINE-TESTED; no outcome window closed yet | R2 label replication test; maturity-rule test |
| POSSIBLE_CAUSE payload-support fix | FIXED | `src/tickets/causes.py`; `tests/test_ticket_causes.py` |
| Prior R4 "live shadow" (`src/shadow/live_shadow_job.py`, `data/processed/live_shadow_predictions.*`) | **SIMULATED: NOT EVIDENCE** | States and probabilities were drawn from `np.random`. Earlier reports are left unedited. |
| Production telemetry reads | Run by the owner, outside the agent session; **formal Gate A record PENDING** | The agent's own ThingsBoard access stays blocked by the permission classifier and was not worked around |
| Live scheduler | **RUNNING**, with reliability issue | `TB_LiveShadow_G2` registered. Slot 11:45Z missing; run at 12:18Z killed (0xC000013A); see `g2_live_shadow_status.md` §3 |
| Live cycles | **2 completed, status OK** | 2026-10-14T11:30:00Z (manual), 2026-10-14T12:00:00Z (scheduled), in `data/live/cycles.jsonl` |
| Day-1 operational report | **NOT YET DUE** | — |

### Offline verification (historical replay, slot 2026-10-12 22:45 UTC)

| Check | Result |
|---|---|
| Full pytest suite | 263 passed, exit 0 |
| `src/tickets/startup_check.py` | PASS, exit 0 |
| Dry-run cycle, 158 registry-eligible devices | status OK; 0 fetch failures; 0 scoring errors |
| Validator failures in dry run | 0 (was 1 before the cause fix) |
| LLM usage | 0 |
| `action_taken` values | `LOG_ONLY_NO_DISPATCH` only |
| Train/serve feature parity (ONLINE_OK, ONLINE_POWER_LOSS, STALE_DATA, OFFLINE_LONG) | exact (rtol 1e-9) |

The dry run replays historical files. It does **not** show that live fetching, credentials,
TLS or ThingsBoard pagination work. Only step B below can show that.

### Cause-derivation fix

`src/tickets/causes.py` derives POSSIBLE_CAUSE from the ticket's own measurements, state and
rules. The Stage D template (`src/eval/build_splits.py`) and the live path both use it.

| Mains status (from payload) | Connectivity ticket | Online HIGH/CRITICAL ticket |
|---|---|---|
| HEALTHY: current AC ≥ 50 V | link/SIM cause only; no power cause | AC instability hypothesis; no mains-loss term |
| LOST: POWER_LOSS state, `RULE_AC_UNDER_50V`, or current AC < 50 V | link + site-power hypothesis | ongoing mains-loss hypothesis |
| UNKNOWN: AC stale or unavailable | link + explicit "Mains status UNKNOWN" | AC instability + "Mains status UNKNOWN" |

On ONLINE_POWER_LOSS tickets, a current battery reading between 0.5 V and 10.8 V adds the
"mains loss with battery discharging" hypothesis.

The frozen Stage D splits in `data/eval/` were built with the earlier hard-coded causes. They
are intentionally **not** regenerated, and their manifests and hashes are unchanged (the
startup check still passes).

## 2. Launch checklist

Complete the steps in order. Each step has an exit criterion. Do not start a step until the
previous one is met.

### A. Owner approval for production telemetry reads (STATUS: PENDING)

Gate A is **not** formally approved until every field below is filled in by the tenant owner or
their delegate. The run that has already started does not count as authorization.

| Field | Value |
|---|---|
| Approver name or role | ____________________ (PENDING) |
| Approval date | ____________________ (PENDING) |
| Approved scope | Read-only polling of telemetry (20 feature keys) and alarm history for the 158 registry-eligible devices, every 15 min, for at least 14 days plus the outcome wait. HTTP GET only, plus the login POST. |
| Explicit prohibitions | No ThingsBoard alarm creation, acknowledgement or clearing; no dispatch; no notifications (Slack, email, SMS or other); no telemetry or attribute writes; no LLM use. Enforced in code by `ReadOnlySession` and the startup check. |
| Retention | Prediction logs (`data/live/shadow_predictions.*`, `cycles.jsonl`) are append-only and kept through G2–G5 review, at least until the G5 incident register is reviewed. The telemetry cache is a rolling ~91 days. Proposed deletion: delete `data/live/` 90 days after G5 sign-off unless the owner extends it. ____ (owner to confirm) |
| Redaction | Committed `reports/` contain aggregates or pseudonyms only. Per-device detail stays in gitignored `data/live/analysis/`. |
| Identifiers in local logs | Device IDs: yes. Branch names: yes. IP addresses: none are collected. ____ (owner to confirm) |
| Identifiers in committed reports | Device IDs: no (pseudonyms). Branch names: no. IP addresses: no. ____ (owner to confirm) |

Note: the repository remote is public, and earlier commits already contain device IDs and branch
names (G1 owner decision). Review before any push.

- [ ] The tenant owner approves read-only API polling of the eligible fleet every 15 minutes
      for at least 14 days. That is roughly 2 GETs per device per cycle, plus a one-time
      backfill from the last harvest to now.
- [ ] The approver name or role and the date are recorded here: ____________________
- [ ] `.env` holds `TB_HOST`, `TB_EMAIL` and `TB_PASSWORD` for a read-only-capable account.
      `TB_VERIFY_TLS` should be left at its default (`true`).
- [ ] If an agent will run the commands, the user grants an explicit permission rule.
      Otherwise the user runs them with the `!` prefix.

**Exit:** the approval is written down. Without it, nothing below runs.

### B. Manual single-cycle validation
- [ ] `.venv\Scripts\python.exe src\tickets\startup_check.py` → `PASS`, exit 0.
- [ ] `.venv\Scripts\python.exe -X utf8 -m src.live.shadow_run --once` → exit 0.
- [ ] `powershell -c "Get-Content data\live\cycles.jsonl -Tail 1"` shows **all** of:
  - `"status": "OK"` (not `DEGRADED` or `FAILED`)
  - `fetch_failures: 0`, `alarm_fetch_failures: 0`, `scoring_errors: 0`
  - `complete_input_devices` equal to `devices`
  - `llm_used: 0`, `alarms_created: 0`, `dispatches: 0`, `notifications: 0`
- [ ] Spot-check one prediction record in `data/live/shadow_predictions.jsonl`. These fields
      must all be present and non-null: `prediction_time`, `device_id`, `model_version`,
      `policy_version`, `input_payload_hash`, `ticket_hash`, `rendered_ticket`,
      `validator_valid`, `action_taken == "LOG_ONLY_NO_DISPATCH"`, `llm_used == false`,
      `git_commit`, `model_sha256`.
- [ ] The validator failure count is recorded. Above zero is not a blocker, but list the
      violation messages.

**Exit:** one real cycle with status OK and every required safety field. If it is DEGRADED,
fix it using `failure_samples` and repeat B.

### C. Scheduler registration
- [ ] Register `TB_LiveShadow_G2` with the PowerShell block in
      `reports/live_shadow_runbook.md` §2. It must run on battery, must not stop on battery,
      must ignore overlapping instances, and must have a 14-minute execution limit.
- [ ] System sleep is disabled for the run.
- [ ] Wait for the **first scheduled** (not manual) cycle. Confirm that a new
      `cycles.jsonl` line has `"status": "OK"` and a new `cycle_id`.
- [ ] Record the first scheduled cycle_id here: ____________________

**Exit:** a scheduled cycle has completed with status OK. Only then may anyone say "the live
shadow run has started".

### D. Day-1 operational report (≥ 24 h after the first successful cycle)
- [ ] `.venv\Scripts\python.exe -X utf8 -m src.live.outcomes --report day1`
- [ ] The report includes:
  - cycles attempted and succeeded (`cycle_status_counts`)
  - fetch failures and incomplete-input rows
  - devices scored
  - validator failures
  - LLM usage, which must be 0
  - data-freshness distribution (`state_counts`)
  - gaps and restarts (`cycle_coverage`, `expected_cycles_since_first`)
- [ ] The report contains **no** outage precision, recall, lead time or alert-quality figures.
      No 24-hour outcome window has closed, so outcome metrics stay at 0.
- [ ] The day-3 and day-7 reports follow the same command with labels `day3` and `day7`.

**Exit:** the day-1 report is written and reviewed. This is the G2 gate.

### E. Day-14 outcome evaluation (≥ 14 days of cycles + 24 h outcome wait + 1 h settle)
- [ ] `.venv\Scripts\python.exe -X utf8 -m src.live.outcomes --report final`
- [ ] Only MATURE rows are counted, meaning the window has closed and follow-up is complete.
      PENDING and INCOMPLETE counts are reported.
- [ ] Power metrics are restricted to the 135-device training fleet. Connectivity metrics are
      reported separately.
- [ ] No threshold or model change is made on live outcomes before the final report is reviewed.
- [ ] Stop safely as described in runbook §8. Prediction logs are preserved and never edited.

## 3. Rules that stay in force

- Do not describe the live shadow as started before checklist step C is complete.
- Do not report outcome metrics before any window has matured.
- Dispatch, alarm creation and notifications stay disabled. The live path has no code that
  performs them.
