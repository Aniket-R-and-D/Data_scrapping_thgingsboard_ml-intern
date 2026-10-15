# Live Shadow Runbook (Stage G2)

Mode: **read-only telemetry, LOG_ONLY_NO_DISPATCH.** No ThingsBoard alarms, no
dispatch, no notifications, no LLM. Code: `src/live/shadow_run.py` (scorer),
`src/live/outcomes.py` (delayed outcome evaluator and interim reports).

## 1. Pre-launch checks

```
.venv\Scripts\python.exe src\tickets\startup_check.py          # must print PASS, exit 0
.venv\Scripts\python.exe -m pytest tests\test_live_shadow.py -q  # parity / read-only / append-only
```

Each cycle repeats these checks itself and refuses to score when one fails:
- The release startup check must PASS: policy fields, model and manifest hashes,
  and unsafe flags.
- `experimental_llm_enabled` must be false. `dispatch`, `alarm_creation` and
  `notifications` must all be `disabled`, and `telemetry_mode` must be `read-only`.
- `llm_bridge.is_experimental_llm_enabled()` must return False.
- The model and calibrator are loaded only through `require_pinned_artifact` (SHA-256 check).

## 2. Launch

The person running the launch triggers the first cycle and creates the scheduled task.
The first cycle backfills telemetry from the end of the historical harvest to now.

```
REM 1) one manual cycle (confirms credentials and connectivity)
.venv\Scripts\python.exe -X utf8 -m src.live.shadow_run --once

REM 2) inspect it: status must be "OK" with fetch_failures 0, alarm_fetch_failures 0,
REM    scoring_errors 0. Do NOT register the task on DEGRADED/FAILED; read failure_samples.
powershell -c "Get-Content data\live\cycles.jsonl -Tail 1"
```

```powershell
# 3) every 15 minutes for 20 days. Runs on battery, does not stop on battery, no overlap.
# Windowless (pythonw): an interactive console window can be closed by accident, which kills the
# cycle (0xC000013A) and leaves a lock. run_live_shadow.bat remains for manual use.
$a = New-ScheduledTaskAction -Execute "C:\workspace\Data_scrapping_thgingsboard_ml-intern\.venv\Scripts\pythonw.exe" `
     -Argument "-X utf8 -m src.live.shadow_run --once --log-file data\live\shadow_run.log" `
     -WorkingDirectory "C:\workspace\Data_scrapping_thgingsboard_ml-intern"
$t = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 20)
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 14)
Register-ScheduledTask -TaskName "TB_LiveShadow_G2" -Action $a -Trigger $t -Settings $s
```

Also disable system sleep for the run (`powercfg /change standby-timeout-ac 0`). Otherwise
`cycle_coverage` drops without any error being raised.

> H1 note (2026-10-15): while the `0xC000013A` scheduler failure is under diagnosis the
> task is **disabled** and its action points at `scripts/run_live_shadow_task.bat`
> (diagnostic wrapper, see §11.2), with `--max-backfill-hours 1`. Do not re-enable or
> return to the direct action until H1 is passed and Gate A is approved.

Foreground alternative (no scheduled task): `.venv\Scripts\python.exe -m src.live.shadow_run --loop`.

## 3. Environment variables (`.env`, never committed)

| Variable | Required | Use |
|---|---|---|
| `TB_HOST` | yes | ThingsBoard base URL |
| `TB_EMAIL` | yes | read-only API login |
| `TB_PASSWORD` | yes | read-only API login |
| `TB_VERIFY_TLS` | no (default `true`) | Set `false` only if the tenant certificate fails verification. Each row records the setting in `tls_verify`. |

The live shadow never reads `SLACK_WEBHOOK`, `SMTP_*` or `EMAIL_TO`, and imports nothing from
`run_nightly_audit.py` or `src/explain/`.

## 4. What each cycle does

1. It aligns to the 15-minute slot boundary `data_cutoff_ts`. Only telemetry with
   `ts < data_cutoff_ts` is used, and features describe the last complete bin
   (`feature_slot_ts` = cutoff − 15 min).
2. It fetches new telemetry for the 20 feature keys, plus the last 26 h of alarms.
   - Requests go through `ReadOnlySession`. That session raises `WriteAttemptError` on any
     non-GET request, with one exception: the login POST.
   - When a key hits the ThingsBoard per-key page limit, that key is re-fetched on its own
     until it is complete.
3. It builds features with the training builder `build_remediated_device_features` over
   a 90-day window. Exact feature parity with the training matrix is tested.
4. It scores with the pinned remediated LightGBM model and the Platt calibrator, then assigns
   the policy tier.
5. It renders the deterministic ticket, using real observation times; sensors with no data are
   marked UNAVAILABLE. It then runs the Stage C field validator.
6. It appends one record per device to the logs, with `action_taken="LOG_ONLY_NO_DISPATCH"`.

Fleet: every `eligible` device in the registry snapshot (158). Each row carries
`in_training_fleet`: 135 devices appear in the training matrix, and power metrics use only those.

## 5. Logs and how to inspect them

All logs live in `data/live/`. The directory is gitignored because it holds tenant device
identifiers.

| File | Content |
|---|---|
| `shadow_predictions.jsonl` | Append-only. One JSON record per device per cycle. |
| `shadow_predictions.db` | SQLite table `predictions`, unique on (cycle_id, device_id). UPDATE and DELETE are blocked by triggers. |
| `cycles.jsonl` | One summary per cycle: status, tier counts, validator failures, fetch failures, `llm_used=0`. |
| `shadow_run.log` | stdout/stderr from scheduled runs. |
| `telemetry_cache/`, `alarms_cache.parquet`, `fetch_state.json` | Telemetry cache (about 91-day rolling) and per-device fetch cursors. |

Every record includes these fields:
- `prediction_time`, `data_cutoff_ts`, `feature_slot_ts`, `device_id`
- `model_version`, `model_sha256`, `calibrator_sha256`, `policy_version`, `release_config_sha256`, `git_commit`
- `input_payload_hash`, `feature_vector_hash`, `ticket_hash`, `rendered_ticket`
- `validator_valid`, `validator_violations`
- `risk_tier`, `device_state`, `calibrated_prob`
- `telemetry_age_seconds`, `heartbeat_gap_seconds` (diagnostic only, not a model input)
- `input_complete`, `llm_used`, `action_taken`

```
REM last 5 cycle summaries
powershell -c "Get-Content data\live\cycles.jsonl -Tail 5"
REM tier counts for the last 3 cycles (no sqlite3 CLI needed)
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/live/shadow_predictions.db');[print(r) for r in c.execute('SELECT cycle_id,risk_tier,COUNT(*) FROM predictions WHERE cycle_id IN (SELECT DISTINCT cycle_id FROM predictions ORDER BY cycle_id DESC LIMIT 3) GROUP BY 1,2 ORDER BY 1 DESC')]"
REM interim reports (aggregate only, safe for reports/)
.venv\Scripts\python.exe -m src.live.outcomes --report day1
```

## 6. Outcome evaluation

- The outcome window for a prediction at time t is (t, t + 24 h].
- A row is labelled only when both hold:
  - now ≥ t + 24 h + 1 h settle, and
  - the device's fetch cursor has passed t + 24 h (complete follow-up).
- Rows that don't meet these conditions are `PENDING` or `INCOMPLETE` and are left out of
  every metric.
- Power outcomes use the R2 label definition from `configs/config.yaml outage_label`. A test
  checks it reproduces `remediated_power_outage_events.parquet`.
- Connectivity outcomes record two things: whether an offline episode (a telemetry gap
  > 60 min) occurred, and whether the device recovered within the window.
- Metrics are reported overall, per ISO week, and over a trailing 7 days. Features are never
  recomputed from outcome data.
- No threshold or model is tuned on live outcomes during the run.

Interim reports are due after day 1, day 3 and day 7. The final report comes after day 14
plus the 24 h outcome wait:
`python -m src.live.outcomes --report {day1|day3|day7|final}`.

## 7. Failure and restart behaviour

- **Overlap:** a lock file (`data/live/shadow.lock`) prevents overlapping cycles. A lock older
  than 14 minutes (`LOCK_STALE_AFTER`, matching the 14-minute task execution limit) is treated
  as stale and removed. A killed cycle therefore costs at most one slot.
- **Idempotent:** if a cycle whose `cycle_id` is already in the database is re-run, it exits with
  `SKIPPED_ALREADY_LOGGED`.
- **Cycle status** is one of:
  - `OK`.
  - `DEGRADED`: some telemetry fetch, alarm fetch or scoring failed. The exit code is 0, except
    when no device had complete input, in which case it is 1. `failure_samples` carries the
    error messages.
  - `FAILED`.
- **Device fetch failure:**
  - The device is still scored from cached data, but its row gets `input_complete=false`.
  - The evaluator excludes those rows.
  - The next successful fetch fills the gap, because the cursor only moves forward on success.
- **Startup check fails:** the cycle is recorded as `FAILED`. Nothing is scored, and the task
  retries in the next slot.
- **Machine off, or user logged off:** the scheduled task does not run, and those cycles are
  missing. The interim report shows `cycle_coverage`.

## 8. Stopping safely

```
type nul > data\live\STOP                       REM cycles exit without scoring (status STOP_FILE_PRESENT)
powershell -c "Disable-ScheduledTask -TaskName TB_LiveShadow_G2"
REM optional, after the outcome window closes:
powershell -c "Unregister-ScheduledTask -TaskName TB_LiveShadow_G2 -Confirm:$false"
```

Never delete or edit the prediction logs. To resume, delete `data\live\STOP` and re-enable the task.

## 9. Data retention and privacy

- `data/live/` holds device IDs, branch names and raw telemetry. It stays on this machine,
  is gitignored, and must never be pushed or copied into `reports/`.
- Prediction logs are kept for the whole G2–G5 programme, at least until the final report and
  the incident register (G5) have been reviewed. They are append-only.
- The telemetry cache keeps a rolling ~91 days; older points are dropped on rewrite.
- Interim and final reports contain aggregates only, with no device names or IDs.

## 10. Known limitations and corrections

- **Correction (R4):** `src/shadow/live_shadow_job.py` and
  `data/processed/live_shadow_predictions.*` were **simulated**: states and probabilities came
  from `np.random`. They are not live evidence. G2 replaces them with `src/live/` and writes
  to new paths under `data/live/`. The R4 files and earlier reports are left unedited.
- **Window origin:** live features use a rolling 90-day window, while the training matrix used a
  fixed origin. Two unbounded forward-fill features (`time_since_mains_loss_hours` and the
  telemetry age) can differ when a device's last event lies just outside one window but inside
  the other. Parity is exact when the origins match (tested).
- **Ticket display values:**
  - CURRENT readings show the 1 h mean; STALE readings show the last raw reading.
  - These are shown as UNAVAILABLE, never as measurements:
    - battery readings of 0.5 V or less (the sentinel for an unequipped channel);
    - the 168 h value the builder fills in when the window has no mains loss.
- **Ticket narrative:** it comes from the Stage D template. When an AC sensor has no observation
  (UNAVAILABLE), the facts line can still cite an AC value. The validator flags such rows, and
  they are counted as `validator_failures`.
- **Fleet:** the fleet is frozen to the registry snapshot. New devices are not picked up.
- **Scheduler:** the scheduled task runs only while the user is logged on.

## 11. Scheduler-failure diagnostics (H1)

Background (2026-10-15): four consecutive scheduled runs died with Task Scheduler
result `0xC000013A` (`STATUS_CONTROL_C_EXIT`) and no new `cycles.jsonl` entry, while
interactive `pythonw` probes survive. The task is **disabled** until the root cause is
found. Key forensic rule: a Python-level exception releases the lock (`finally`) and
writes a `FAILED` row plus `data/live/last_crash.json`; a **remaining lock file with no
cycle row means an OS-level kill** (or power loss), not a Python exception.

### 11.1 Task Scheduler Operational log (needs admin)

```powershell
wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true
Start-ScheduledTask -TaskName "TB_LiveShadow_G2"
Get-WinEvent -LogName "Microsoft-Windows-TaskScheduler/Operational" -MaxEvents 50 |
  Format-List TimeCreated, Id, LevelDisplayName, Message
```

Look for task termination, logon failure, privilege failure, or launch errors.
Without admin rights this log stays disabled; that was the case on 2026-10-15
(`wevtutil` → access denied).

### 11.2 Wrapper log (no admin needed)

During diagnosis the task action points at `scripts/run_live_shadow_task.bat`, which
appends to `data/live/task_wrapper.log`:

```
[date time] task start
[date time] task exit <python exit code>
```

- `task start` with no `task exit` → pythonw never returned (killed mid-run).
- `task exit 0` with no new cycle → early exit (lock held) or STOP file; read
  `data/live/shadow_run.log` for which.
- Compare with Task Scheduler's `LastTaskResult`: if they differ, the wrapper was
  killed externally.

```powershell
Get-ScheduledTask -TaskName "TB_LiveShadow_G2" | Get-ScheduledTaskInfo
Get-Content data\live\task_wrapper.log -Tail 10
Get-Content data\live\shadow_run.log -Tail 20
Get-Content data\live\last_crash.json  # only exists after a Python-level crash
```

### 11.3 Interactive vs scheduled

Run the wrapper by hand (`cmd /c scripts\run_live_shadow_task.bat`) and compare with a
scheduled `Run`. Fails-only-under-scheduler points at session/logon or endpoint policy.
Guard manual runs with a STOP file (`New-Item data\live\STOP`) so they exit before any
fetch; delete it afterwards. A STOP-guarded run appends one honest
`STOP_FILE_PRESENT` row to `cycles.jsonl` and never touches ThingsBoard.

### 11.4 Task principal and settings

```powershell
Get-ScheduledTask -TaskName "TB_LiveShadow_G2" | Select-Object -ExpandProperty Principal
Get-ScheduledTask -TaskName "TB_LiveShadow_G2" | Select-Object -ExpandProperty Settings
```

Preferred for unattended operation: run whether the user is logged on or not, with a
dedicated service or password-backed account; do not depend on an interactive desktop.
Changing the logon type needs the account password and usually admin rights.

### 11.5 Security software

Check Windows Security / Defender / corporate EDR quarantine and event logs for
`pythonw.exe` or the project path. On a managed machine, ask IT before changing anything.

### 11.6 Stale lock (safe to clear)

`data/live/shadow.lock` older than 14 minutes (`LOCK_STALE_AFTER`, matching the task
execution limit) is stale: no live cycle can still hold it. A cycle removes it itself
and logs `removing stale lock`. Manual removal is equivalent when the task is disabled:

```powershell
Remove-Item data\live\shadow.lock  # only when no cycle is running
```

Never delete `cycles.jsonl`, `shadow_predictions.jsonl`, `shadow_predictions.db`,
`fetch_state.json`, or the telemetry cache.

### 11.7 Bounded-backfill resume

Each cycle ingests at most `--max-backfill-hours` (default 1.0) of backlog per device,
oldest-first, so a resume cannot issue one oversized request. During the drain the cache
is honestly stale (expect elevated `CONNECTIVITY_ISSUE` rows); outcome windows stay valid
because no permanent hole is left. After consecutive stable cycles, raise the cap
gradually by editing the task-action argument:

```
--max-backfill-hours 1   # resume (default)
--max-backfill-hours 6   # after stability proven
--max-backfill-hours 24  # then larger
--max-backfill-hours 0   # uncapped full catch-up, only when stable
```

`--loop` mode accepts the same flag. `cycles.jsonl` records `backfill_capped_devices`
and `max_backfill_hours` per cycle.
