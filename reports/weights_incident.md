# Weights Incident Record (2026-10-15)

Aggregate-only; no device identifiers. Local paths only.

## Timeline (UTC, 2026-10-15)

| Time | Event |
|---|---|
| ~05:20–05:45 | Model-dependent pytest suites pass (33 passed), proving all pinned weights present: `load_release()` → startup check PASS. |
| 05:52:14 | `models/gbm_outage_oct2026/` directory mtime (content deleted). |
| 06:04:41 | `models/outage_predictor/` directory mtime (content deleted). |
| ~06:07 | Discovered: `src/tickets/startup_check.py` FAILs, exit 1. |
| ~06:10–06:25 | 3 of 5 files restored byte-exact from git history, hash-verified against `configs/release.yaml` pins. 2 files confirmed unrecoverable from git. Substitute candidates rejected by hash comparison. |

## The five files

| File | Role | Size | Status |
|---|---|---|---|
| `models/outage_predictor/remediated_lightgbm_clean.joblib` | Live ticket path (24 h outage probability) | 514,692 B | ✅ restored from git, pin `799fae21…` matches |
| `models/outage_predictor/remediated_platt_calibrator.joblib` | Live ticket path (calibration) | 863 B | ✅ restored from git, pin `0958ce80…` matches |
| `models/qwen_reasoning_outage_predictor/remediated_lora_adapter/adapter_model.safetensors` | Experimental LoRA (default OFF) | 35,237,104 B | ✅ restored from git, pin `db16593c…` matches |
| `models/gbm_outage_oct2026/model.joblib` | Nightly alarm-event ranker (not the ticket path) | — | ❌ missing; git holds only an older blob (`d53e079e`, 2,519,496 B) whose hash differs from pin `ca9fc578…`; not substituted |
| `models/qwen_reasoning_outage_predictor/remediated_final_model/model.safetensors` | Experimental merged Qwen (default OFF) | — | ❌ missing; never committed to git; legacy `archive/` copy hashes `cd6a6072…` vs pin `be45761a…`; not substituted |

Small neighbors survived (`feature_names.json`, `metadata.json`, tokenizer/config JSONs),
as did `data/`, `training_data/`, and `.venv`. Startup check still FAILs on the two
missing pins, which blocks the live scorer (`load_release` raises) and the nightly
chain independently of the scheduler issue. Full suite now: 278 passed / 14 failed,
every failure caused by these two files; all 22 model-independent new tests pass.

## Hypotheses (for / against)

1. **My session commands.** FOR: I ran many shell commands during the window.
   AGAINST: the complete command list contains no delete/move under `models/`
   except one documented case — removing my own 4 corrupted restores (PowerShell
   `>` had written them UTF-16, doubling sizes), which I immediately rewrote
   byte-correct via Python and hash-verified. That cleanup postdates discovery
   and is fully accounted for. No `del`/`rm`/`git clean`/cleanup script was run.
   VERDICT: ruled out.
2. **`git clean`.** AGAINST: would also have removed other ignored-untracked trees
   (`data/` matrix, training checkpoints survive). VERDICT: unlikely.
3. **Security software quarantine/removal.** FOR: large-file targeting fits; the same
   endpoint story as the `0xC000013A` scheduler kills. AGAINST: none observed.
   `Get-MpThreatDetection` is inaccessible from this session; Protection history
   needs admin/IT. VERDICT: **untested**.
4. **Sync client.** AGAINST for OneDrive: the repo is not a reparse point, and the
   files are truly gone (not dehydrated placeholders). VERDICT: OneDrive ruled out;
   other sync tools untested.
5. **Manual deletion (user or another agent/process).** Recycle Bin is empty
   (consistent with `Remove-Item`-style bypass, AV action, or Shift-delete).
   The user's PSReadLine history shows aggressive venv/Docker cleanups in *other*
   projects but nothing targeting this repo. VERDICT: **untested**.
6. **Repo cleanup script.** `nightly_risk_chain.prune()` only targets
   `data_harvest/runs` and `training_data/nightly`, and was never executed here.
   VERDICT: ruled out.

**External deletion is a hypothesis, not a finding.** The scheduler kills and the
deletions *could* share a cause (endpoint protection acting on Python and large
files) or be unrelated. Do not assert either.

## Self-check result

Reviewed every shell command issued in this session: reads, scheduler task
operations, probe-log/STOP create-remove cycles in `data/live/`, pytest runs,
`git add/commit`, file writes to code/tests/docs, the failed `wevtutil` enable,
and the documented corrupt-restore cleanup. Nothing else writes under `models/`.
No deletions attributable to this session. Reported either way, as required.

## Options paper (no changes made; owner decides)

Shared facts: the live ticket path (LightGBM + calibrator) is restored and
verified. Only the nightly ranker and the experimental merged Qwen are missing.
Any option touching `configs/release.yaml` is a release change: record the
decision, re-verify, never silently edit.

**A. Restore from owner backup (preferred).** Copy the two files back, re-run
`src/tickets/startup_check.py` (must print PASS, exit 0) and the model-dependent
tests. Changes nothing in git. Requires: a backup that actually holds them —
check before anything else, then adopt `scripts/backup_release_artifacts.ps1`
(verify mode) on a schedule.

**B. Split pins into required vs optional.** `release.yaml` gains a scope flag per
artifact (e.g. `required_for: [live]` vs `[experimental]`); the startup check
enforces optional pins only when the corresponding feature flag is on
(Qwen merged model when the LLM flag is on; nightly ranker in the nightly chain).
Release changes: schema addition + enforcement logic + tests for both modes.
Re-verify: full suite plus a startup matrix (flags on/off). Risk: a weaker default
posture if scoping is wrong — keep live-path pins unconditionally required.

**C. Regenerate the merged Qwen model.** Merge base + restored LoRA adapter with the
original script/settings, pin the result as a NEW clearly labeled artifact
(old pin stays recorded as superseded). Release changes: new pin + provenance note.
Re-verify: hash check, experimental-path smoke test. Risk: merged bytes may differ
from the lost file, so it is a new artifact, not a restore. (No fine-tuning.)

**D. Retrain the nightly ranker.** Only if the nightly alarm-event ranking is still
wanted. Retrain with the pinned procedure, pin the new weights. Release changes:
new pin. Re-verify: nightly-chain tests, hash check. Heaviest option; deprioritize
unless operations needs that signal.

## Owner decisions required

1. Investigate the cause (Protection history via IT/admin; backups inventory).
2. Choose A, B, C/D, or a combination — and record it, not silently edit.
3. Back up `models/` + manifests + `configs/release.yaml` off-machine
   (`scripts/backup_release_artifacts.ps1 -Destination <path>`, then `-Verify`).
4. Only then continue the H1 resume ladder (Gate A → forensics → capped cycles).

Resume order reminder: nothing restarts until (1) Gate A is approved by the real
tenant owner, (2) this incident is explained and startup passes legitimately,
(3) scheduler forensics + endpoint review are done — then capped resume and three
consecutive OK cycles.
