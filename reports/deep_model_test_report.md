# [DEPRECATED] Deep Model Test Report & Outage Prediction Evaluation

> [!CAUTION]
> **FORMALLY DEPRECATED — DO NOT USE FOR OPERATIONAL PLANNING OR MODEL BENCHMARKING**  
> An independent engineering review identified critical methodological and data-quality defects in this legacy report:
> 1. **Label Leakage:** The target definition included `heartbeat_gap > 900s`, while `heartbeat_gap_seconds` was simultaneously fed as a raw model feature (inflating legacy LightGBM PR-AUC by +13.1%).
> 2. **Telemetry Sentinel Confounding:** 50.41% of battery readings were flat `0.0 V` sentinels (64/135 devices have no battery hardware). Telemetry staleness was confounded with true utility power outages (73.77% of legacy labels were communication blackouts).
> 3. **Shadow Mode Inconsistencies:** The legacy shadow evaluation suffered from mathematical discrepancies (0 FN in tier table vs 432 FN in confusion matrix) and represented a historical backtest replay rather than true shadow validation.
> 4. **Explanation Hallucinations:** The legacy explanation layer only checked numbers, guaranteeing "100% delivered faithfulness" via deterministic template fallback while raw LLM pass rate was only 28%. Stale-device cases were incorrectly routed to "urgent power outage" tickets.
>
> **REMEDIATED SYSTEM REPORTS (OCTOBER 2026):**
> - **Phase R1 (Data State Classification):** [reports/phase_r1_data_state_classification.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/reports/phase_r1_data_state_classification.md)
> - **Phase R2 (Label Redesign & Leakage Elimination):** [reports/phase_r2_label_redesign.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/reports/phase_r2_label_redesign.md)
> - **Phase R3 (Model Retraining & Honest Evaluation):** [reports/phase_r3_outage_predictor_remediation.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/reports/phase_r3_outage_predictor_remediation.md)
> - **Phase R4 (Tiering & Shadow Mode Remediation):** [reports/phase_r4_tiering_and_shadow_remediation.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/reports/phase_r4_tiering_and_shadow_remediation.md)
> - **Phase R5 (Explanation Layer & Semantic Validation):** [reports/phase_r5_explanation_layer_remediation.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/reports/phase_r5_explanation_layer_remediation.md)
> - **Comprehensive Production Model Card:** [MODEL_CARD.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/MODEL_CARD.md)
>
> All legacy assets have been safely archived under `archive/legacy_v1_run/`.

---
*Legacy report preserved for historical auditability under archive/legacy_v1_run/.*
