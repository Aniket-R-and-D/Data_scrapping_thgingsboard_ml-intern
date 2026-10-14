# Phase R6 Report — Documentation, Model Card, and Release Readiness

**Date:** 2026-10-14 · **Release:** `r5-reliability-recovery-stage-f`
**Status:** GATE DELIVERABLE — awaiting approval.
**Provenance:** self-audit disclosure; not an independent audit.

**Explicit statements:** no model was fine-tuned in R6; no dispatch, ThingsBoard alarm
creation, or external notification was enabled or performed; no historical evidence,
earlier report, frozen manifest, evaluation split, or Stage A–E artifact was modified.

## 1. Documents produced

| Artifact | Change |
|---|---|
| `MODEL_CARD.md` | **Regenerated.** Unsupported figures removed (per-bank SBI/BOB metrics, 54.3% dispatch precision, ROC-AUC 0.9593, 1,000 bootstraps, 54/55 fleet table); verified figures cited with sources; non-goals, failure modes, PENDING review, NOT MEASURED accuracy, monitoring/retraining triggers |
| `README.md` | **Rewritten.** Corrected R5 row, 250 (not 1,000) bootstraps, per-stage commands incl. A–E and R6, safety constraints, operational default, reproduction instructions, honest limitations |
| `reports/final_claims_audit.md` | **New.** 16-claim audit: 8 approved (7 full + 1 scoped), 8 rejected classes removed/qualified |
| `reports/project_summary.md` | **New.** Portfolio summary in restrained vocabulary (no "exceptional/flawless/100% accurate/production-ready") |
| `configs/release.yaml` | **New.** Safe path explicit: renderer enabled, LLM OFF, read-only telemetry, dispatch/alarms/notifications disabled, pinned model + manifest hashes |
| `src/tickets/startup_check.py` | **New.** Fail-closed verification (policy fields, hashes, manifests, unsafe flags) |
| `tests/test_release_startup.py` | **New.** 7 startup-check tests |

## 2. Final test results (commands + exit codes)

| Command | Exit | Result |
|---|---:|---|
| `.venv\Scripts\python.exe -m pytest tests/test_tickets_schema.py tests/test_tickets_renderer.py tests/test_field_level_validator.py tests/test_evaluation_split_integrity.py tests/test_stage_e_comparison.py tests/test_release_startup.py tests/test_phase_r1_state_classification.py tests/test_phase_r2_label_leakage.py tests/test_phase_r3_predictor.py tests/test_phase_r4_consistency.py tests/test_phase_r5_claims.py -q` | **0** | **124 passed** |
| `python verify_r5_evidence.py` (Stage A) | **0** | contamination + hardcoding evidence unchanged |
| `.venv\Scripts\python.exe src/eval/verify_splits.py` (Stage D) | **0** | `verified: true, error_count: 0` |
| `tests/test_field_level_validator.py` (Stage C suite) | **0** | 27 passed (0 FA / 0 FR / 10 paraphrases) |
| `tests/test_stage_e_comparison.py` (Stage E) | **0** | 5 passed |
| `.venv\Scripts\python.exe src/tickets/startup_check.py` | **0** | `startup check: PASS` |

## 3. Release readiness checklist

- [x] Deterministic renderer is the default and only approved ticket path
- [x] `EXPERIMENTAL_LLM_ENABLED=false`; dispatch/alarms/notifications disabled; read-only mode
- [x] All artifact and manifest hashes verified at startup (fail-closed)
- [x] Frozen manifests intact; no tuning on evaluation splits
- [x] Human review PENDING; semantic accuracy NOT MEASURED — stated everywhere it matters
- [x] Retired benchmark marked dev/debug-only; never a headline
- [x] No pooled known/unseen metrics; per-set reporting only
- [ ] Live read-only shadow validation + completed blinded human review (deferred; required before any production promotion beyond advisory use)
