# Final Claims Audit (R6)

**Date:** 2026-10-14 · **Policy:** `ticket-policy-v1`
Every public-facing claim below lists its source artifact, reproduction command, observed
result, confidence/limitation, and whether it is approved for external use. Any claim that
cannot be reproduced from artifacts is marked **NO** and either removed or qualified in
`README.md` / `MODEL_CARD.md`.

Status vocabulary: **REPRODUCED** (re-ran and matched) · **STORED** (preserved artifact
value, internally consistent, not re-executed) · **REFUTED** · **NOT MEASURED**.

| # | Claim | Source artifact | Reproduction command | Result | Confidence / limitation | Approved |
|---|---|---|---|---|---:|---|
| 1 | Predictor precision 20.26% [19.42%, 20.97%], recall 79.68%, F1 0.3231, PR-AUC 0.2540, incident recall 89.66% (26/29), lead time 22.6 h, on untouched split (149,175 rows, 2,475 positives, 29 events; 250 bootstraps) | `reports/phase_r3_outage_predictor_remediation.md`, `phase_r3_predictor_remediation.json` | `.venv\Scripts\python.exe -m pytest tests/test_phase_r3_predictor.py -q` | 29/29 pass (R-suite green); JSON values match report | STORED predictor metrics from executed R3 code; bootstrap CIs computed with 250 (not 1,000) resamples | **YES** |
| 2 | Leakage ablation: unlagged heartbeat lifted PR-AUC 0.2540→0.2873 (+13.1%) | Same R3 artifacts | inspect `phase_r3_predictor_remediation.json` ablation section | Values present and consistent | STORED; ablation design documented in R3 report | **YES** |
| 3 | Fleet inventory: 135 devices (BOI 104, SBI 16, BOB 10, Canara 5); battery sensors on 32/135 | `reports/phase_r1_sentinel_audit.json`, `phase_1_data_audit.md` | `.venv\Scripts\python.exe -m pytest tests/test_phase_r1_state_classification.py -q` | Suite green | REPRODUCED sentinel counts | **YES** |
| 4 | Deterministic render success 100% + Stage C validator acceptance 100% on TRAIN (212), FUTURE (407), UNSEEN (150); 0 cross-split payload overlap | `data/eval/manifest_*.json`, Stage D report | `.venv\Scripts\python.exe src/eval/verify_splits.py` | `verified: true, error_count: 0` | REPRODUCED 2026-10-14 | **YES** |
| 5 | Validator adversarial suite: 0/10 false acceptances, 0/5 false rejections, 10/10 paraphrases | `tests/test_field_level_validator.py`, Stage C report | `.venv\Scripts\python.exe -m pytest tests/test_field_level_validator.py -q` | 27 passed | REPRODUCED; covers listed patterns only, not unrestricted prose | **YES (scoped)** |
| 6 | Stage E: Qwen adds 0 supported facts/steps over renderer on 557 clean records; unsupported claims 14/407 + 31/150; recommendation KEEP EXPERIMENTAL | `reports/phase_r5_stage_e_raw_results.json`, Stage E report | `.venv\Scripts\python.exe -m pytest tests/test_stage_e_comparison.py -q` | 5 passed; raw JSON present (5.4 MB) | REPRODUCED aggregates; full GPU re-run (~10 min) available via `run_stage_e_comparison.py` | **YES** |
| 7 | Fleet alert volume under `ticket-policy-v1`: 0.2029 power + 1.0415 connectivity episodes/device/week | `data/eval/split_build_summary.json`, Stage D report | `.venv\Scripts\python.exe src/eval/verify_splits.py` + manifest inspection | Values match summary artifact | STORED computation over 11.5-day window | **YES** |
| 8 | Release config safe path: LLM OFF, dispatch/alarms/notifications disabled, read-only telemetry, pinned hashes verified | `configs/release.yaml` | `.venv\Scripts\python.exe src/tickets/startup_check.py` | `PASS`, exit 0 | REPRODUCED 2026-10-14 | **YES** |
| 9 | "Human review completed / audited / κ=0.6604 / 4.27 / 4.73" (any phase) | Stage A correction notice §2.1–2.2; ledger L1, L11 | `python verify_r5_evidence.py` | 0 humans; scores are hardcoded heuristics | REFUTED — removed from README/MODEL_CARD; status is PENDING | **NO** |
| 10 | "100% delivered faithfulness / zero ungrounded claims reach operations / comprehensive zero-hallucination guarantee" | Correction notice §2.6; ledger L4 | Stage C adversarial suite (10/10 falsifications passed legacy validator) | Safety claim contradicted | REFUTED — replaced with scoped single-run statements | **NO** |
| 11 | Any semantic-accuracy percentage ("~90–95%", "~40%", or other) | Correction notice §2.5; ledger L8 | No instrument exists | NOT MEASURED | Removed everywhere; human review PENDING | **NO** |
| 12 | Per-bank predictor precision/recall for SBI/BOB (e.g., 14.2%/16.5%) or incident recalls 5/6, 4/5 | Ledger L14 | `phase_r3_predictor_remediation.json` shows SBI/BOB positives = 0 | Contradicted | Removed from README/MODEL_CARD | **NO** |
| 13 | "1,000 bootstrap resamples" / ROC-AUC 0.9593 / 54-site fleet tables | R3 report (250 iterations), R1 audit (BOI 104) | grep R3 report line 44 | Contradicted | Corrected to 250 iterations; fleet table fixed | **NO** |
| 14 | "99% raw pass rate" or any headline metric from the retired contaminated benchmark | `training_data/README.md` retirement notice | `python verify_r5_evidence.py` (50/100 dup inputs) | Contaminated | RETIRED to dev/debug-only; never a headline | **NO** |
| 15 | Pooled known-device + unseen-device LLM metrics | Stage E report §2 (separate tables) | inspect raw results `per_set_results` keys | Reported separately by design | Pooling prohibited; per-set only | **NO (pooled form)** |
| 16 | "Production-ready / flawless / exceptional / 100% accurate" language | This audit | text search of README/MODEL_CARD/summary | Absent | Restrained vocabulary enforced | **NO** |

**Audit outcome:** 8 approved claims (7 full, 1 scoped), 8 rejected claim classes removed or
qualified across `README.md`, `MODEL_CARD.md`, and `reports/project_summary.md`.
