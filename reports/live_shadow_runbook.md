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
$a = New-ScheduledTaskAction -Execute "C:\workspace\Data_scrapping_thgingsboard_ml-intern\run_live_shadow.bat"
$t = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 20)
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 14)
Register-ScheduledTask -TaskName "TB_LiveShadow_G2" -Action $a -Trigger $t -Settings $s
```

Also disable system sleep for the run (`powercfg /change standby-timeout-ac 0`). Otherwise
`cycle_coverage` drops without any error being raised.

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
  than 30 minutes is treated as stale and removed.
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
