# G2 Live Shadow Status

Status time (UTC): 2026-10-14T12:25. Source: local logs in `data/live/` and local Task Scheduler
state. No ThingsBoard access by the agent.

**Live shadow operation has begun.** G2 is **not complete**: the day-1 and day-14 reports are
still outstanding, and production polling is not yet formally authorized (Gate A pending).

## 1. Gates

| Gate | Status | Evidence |
|---|---|---|
| A. Owner approval for production reads | **PENDING** | No approver name/role or date recorded yet; see `g2_status_and_launch_checklist.md` §A |
| B. Manual cycle | **PASSED** | cycle `2026-10-14T11:30:00+00:00`: status OK, 158 devices, `complete_input_devices` 158, 0 fetch / alarm-fetch / scoring failures, 0 alarms, 0 dispatches, 0 notifications, `llm_used` 0 |
| C. Scheduler registered + live cycle | **PASSED (scheduler start)** | Task `TB_LiveShadow_G2` registered (15 min, 20 days). Scheduled cycle `2026-10-14T12:00:00+00:00` completed with status OK and the same zero counts. |
| D. Day-1 operational report | **PENDING** | Due ≥ 24 h after the first successful cycle, i.e. ≥ 2026-10-15T11:32Z |
| E. Day-14 outcome evaluation | **PENDING** | Needs ≥ 14 days of cycles plus closed 24 h outcome windows |

## 2. Safety fields (all 316 logged prediction rows, 2 cycles)

| Field | Observed |
|---|---|
| `action_taken` | `LOG_ONLY_NO_DISPATCH` only |
| `llm_used` | false on every row |
| `git_commit` | `b44a775` on every row. The cycles ran before the cause fix `fe5df5f`. Later cycles run from the working tree and record their own commit. |
| `tls_verify` | true |
| alarms / dispatches / notifications per cycle | 0 / 0 / 0 |

## 3. Scheduler reliability issue (found in this review)

| Observation | Detail |
|---|---|
| Slot `2026-10-14T11:45:00Z` | missing; no cycle was logged |
| Run at 17:48 local (slot 12:15Z) | `LastTaskResult = 0xC000013A` (STATUS_CONTROL_C_EXIT): killed by Ctrl+C or a console close. `^C` markers in `shadow_run.log`. |
| Lock file | the killed run left `data/live/shadow.lock` behind |
| Task history | the Windows TaskScheduler/Operational log is disabled, so the exact cause of each gap cannot be confirmed |

Most likely cause: the task runs interactively, so a console window opens every 15 minutes, and
closing it kills the cycle.

Fixes, in the working tree (tests pass):
- **Stale-lock threshold is now 14 minutes** (it was 30). A killed cycle now costs one slot instead of up to three.
- **`--log-file` option** lets the task run windowless under `pythonw.exe`.

**Owner action recommended.** Switch the task action to the windowless form (PowerShell):

```powershell
$a = New-ScheduledTaskAction -Execute "C:\workspace\Data_scrapping_thgingsboard_ml-intern\.venv\Scripts\pythonw.exe" `
     -Argument "-X utf8 -m src.live.shadow_run --once --log-file data\live\shadow_run.log" `
     -WorkingDirectory "C:\workspace\Data_scrapping_thgingsboard_ml-intern"
Set-ScheduledTask -TaskName "TB_LiveShadow_G2" -Action $a
```

The agent did not change the scheduled task. Coverage so far: 2 of 4 expected slots. The day-1
report shows `cycle_coverage` and lists every missing slot.

## 4. Operational alert profile (first 2 cycles; not an outcome evaluation)

Per cycle: 128 CONNECTIVITY_ISSUE, 3 CRITICAL, 1 HIGH, 1 MEDIUM, 25 LOW (158 devices).

- **The 128 connectivity alerts** are decomposed in `reports/connectivity_alert_decomposition.md`:
  - 60 genuinely stale or offline
  - 41 never reporting in 90 days
  - 15 misclassified (alive on `fasLastHour` only)
  - 12 intermittent
- **Root problem: repetition.** One ticket per device per cycle produces about 1,190 raw tickets per
  real episode. The recommended (not enabled) policy is one alert per episode after 60 min STALE.
- **CRITICAL/HIGH:**
  - 1 of 4 is plausible (current mains loss).
  - 1 is a stuck 270 V sensor that permanently forces the surge rule.
  - 2 are scores with no electrical evidence.
- **Validator failures:** 2 per cycle, both from the old cause template. Fixed in `fe5df5f`, and the
  tests now cover them.

No thresholds, models or alerting behaviour were changed.

## 5. Day-1 report (Gate D) contents, verified

`python -m src.live.outcomes --report day1` is operational-only **by construction**: outcome
metrics are withheld even if some windows have matured. A regression test enforces this. It
reports:

- cycles attempted / succeeded / degraded, and status counts
- scheduler gaps (count and list of missing slots) and cycle coverage
- fetch and alarm-fetch failure totals, plus incomplete-input rows
- devices scored (all 158, and the 135 in the training fleet)
- validator failures, in total and by git commit
- LLM usage (must be 0) and `action_taken` values
- data-freshness distribution (telemetry age bins, never-seen rows) and device-state counts
- connectivity alert rows (total and mean per cycle) and power alert rows by tier

It contains no precision, recall, lead time or alert-quality figures. A preview against the
current logs (written to scratch, not `reports/`) ran without errors.

## 6. Corrections carried forward

- The R4 "live shadow" (`src/shadow/live_shadow_job.py`, `data/processed/live_shadow_predictions.*`)
  was simulated with `np.random` and is not evidence.
- G2 runs on commit `b44a775` or later, not on tag `v0.1.0-deterministic-default`.
