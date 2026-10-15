# H1 Wait-State Handoff (G2 Live Shadow)

Date (UTC): 2026-10-15. Live command: `python -m src.live.scheduler_health --status`.

## State

| Item | Status |
|---|---|
| Live shadow implementation | Complete and offline-tested |
| Live operation | **PAUSED** |
| Scheduled task `TB_LiveShadow_G2` | **DISABLED** (action: `scripts/run_live_shadow_task.bat`) |
| Gate A (owner approval) | **PENDING** — scorer refuses without it (`REFUSED_GATE_A_APPROVAL`) |
| H1 (scheduler stability) | **NOT PASSED** |
| H2–H4 | Not started (correct) |
| Backlog | ~18 h since last scored cycle (2026-10-14T12:00Z); resume capped at 1 h/cycle |
| Root cause of repeated `0xC000013A` | Unknown; OS-level termination suspected |

No dispatch, alarm creation, notifications, LLM, or automatic escalation exist or
are enabled anywhere. The Qwen explainer stays experimental and disabled.

## Forensic summary

Four consecutive scheduled runs (windowless `pythonw.exe`) died with Task Scheduler
result `0xC000013A` (`STATUS_CONTROL_C_EXIT`) and no new `cycles.jsonl` entry.
A Python-level exception would release the lock (`finally`) and write a `FAILED`
row plus `data/live/last_crash.json`; every failed run instead **left its lock
behind with no cycle row**, which points to OS-level termination. Interactive
`pythonw` probes survive, so the failure correlates with real fetch/scoring runs.
The Task Scheduler Operational log is disabled and needs admin to enable; TCP 443
to the ThingsBoard host succeeds, so this is not a hard network outage.

## What was built during H1 diagnosis (committed, tests pass)

- `scripts/run_live_shadow_task.bat` — captures task start/exit and the Python exit
  code in `data/live/task_wrapper.log` (validated live, exit 0 paths).
- `src/live/scheduler_health.py` — `--json` health check, `--baseline`
  (operational-only; ready after three consecutive OK), `--status` handoff readout.
- `src/live/shadow_run.py` — working `--log-file` under `pythonw`, per-stage flushed
  logs, `last_crash.json` marker, `--max-backfill-hours` (default 1.0, oldest-first).
- `src/live/gate_a.py` + `configs/gate_a_approval.template.yaml` — file-based
  authorization gate; placeholder or missing approval refuses polling.
- Runbook §11 — failure diagnostics, stale-lock handling, resume ladder.

## Required owner/admin actions (in order)

1. **Gate A approval.** Copy `configs/gate_a_approval.template.yaml` to
   `configs/gate_a_approval.yaml` (gitignored) and have the tenant owner complete
   every field. Never commit placeholder values.
2. **Scheduler forensics (admin):** `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true`,
   then review events around failure timestamps.
3. **Endpoint protection review:** check Defender/EDR for `pythonw.exe`, the project
   path, ThingsBoard-host network access, and `.env` credential access. Do not disable
   protection on a managed machine without IT.
4. **Controlled resume:** confirm `--max-backfill-hours 1`, `Enable-ScheduledTask`,
   one cycle, then inspect wrapper/shadow/scheduler/EDR evidence. (Backlog is ~18 h
   as of 2026-10-15 06:10Z per `--status`, not 43 h — the earlier figure was wrong;
   the drain is ~18 one-hour cycles, not ~43.)
5. **Ladder:** three consecutive `OK` → `1 → 6 → 24 → 0` (uncapped only when stable).

## Resume ladder (exact)

```powershell
Set-ScheduledTask -TaskName "TB_LiveShadow_G2" -Action $a  # $a = wrapper action; rung is edited inside the .bat
Enable-ScheduledTask -TaskName "TB_LiveShadow_G2"
.venv\Scripts\python.exe -m src.live.scheduler_health --baseline --json
.venv\Scripts\python.exe -m src.live.scheduler_health --status --json
```

## H1 pass criteria (do not relax)

- Three consecutive scheduled cycles return `status: OK`.
- No `0xC000013A`.
- No residual stale lock after completion.
- Wrapper exit code matches the Python cycle result.
- No unexplained scheduler gaps.
- Gate A formally approved.
- Backlog drains under the cap ladder without crashing.

## Model-weights incident (2026-10-15, discovered during handoff verification)

At ~05:52–06:04 UTC, five release-pinned weight files disappeared from `models/`
(directory mtimes; small metadata/config JSONs alongside them survived, so this
looks like large-file-targeted deletion by an external process — same endpoint
story as the scheduler kills; IT should review). Three were restored byte-exact
from git history and re-verified against `configs/release.yaml` pins:

- `models/outage_predictor/remediated_lightgbm_clean.joblib` ✅ hash matches
- `models/outage_predictor/remediated_platt_calibrator.joblib` ✅ hash matches
- `models/qwen_reasoning_outage_predictor/remediated_lora_adapter/adapter_model.safetensors` ✅ hash matches

Two are unrecoverable from git (never committed; local-only training outputs)
and were NOT substituted (a legacy hash was checked and differs):

- `models/gbm_outage_oct2026/model.joblib` (nightly alarm-event ranker, not the ticket path)
- `models/qwen_reasoning_outage_predictor/remediated_final_model/model.safetensors` (experimental, default OFF)

Consequence: `src/tickets/startup_check.py` FAILS (exit 1) until the owner restores
these from backup or authorizes a retrain + release re-pin. The live scorer cannot
score without a passing startup check, independent of the scheduler issue. Do not
retrain or re-pin without owner approval; do not relax the hash check.

## Evidence locations (all local, gitignored)
`data/live/cycles.jsonl`, `data/live/shadow_run.log`, `data/live/task_wrapper.log`,
`data/live/last_crash.json` (only after a Python-level crash),
`data/live/fetch_state.json`, `data/live/shadow_predictions.*`. Never delete or edit
prediction logs. Reports in `reports/` contain aggregates only.
