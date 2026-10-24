# Live shadow interim report — day1

Generated (UTC): 2026-10-15T12:23:27.883612+00:00

Aggregate only; no device identifiers. Mode: read-only, LOG_ONLY_NO_DISPATCH.

## Operational status

- first_prediction_utc: 2026-10-15T09:42:17.153046+00:00
- last_prediction_utc: 2026-10-15T12:15:19.531572+00:00
- cycles_attempted: 13
- cycles_succeeded: 9
- cycles_degraded: 0
- cycle_status_counts: {'OK': 9, 'STOP_FILE_PRESENT': 2, 'FAILED': 2}
- expected_cycles_since_first: 11
- scored_cycles: 9
- cycle_coverage: 0.8182
- scheduler_gap_count: 3
- scheduler_missing_slots: ['2026-10-15T10:00:00+00:00', '2026-10-15T10:15:00+00:00', '2026-10-15T10:45:00+00:00']
- fetch_failures_total: 0
- alarm_fetch_failures_total: 0
- devices: 158
- devices_in_training_fleet: 0
- prediction_rows: 1422
- tier_counts: {'CONNECTIVITY_ISSUE': 1391, 'LOW': 26, 'CRITICAL': 3, 'HIGH': 2}
- state_counts: {'STALE_DATA': 1311, 'OFFLINE_LONG': 80, 'ONLINE_OK': 30, 'ONLINE_POWER_LOSS': 1}
- connectivity_alert_rows: 1391
- mean_connectivity_alerts_per_cycle: 154.6
- power_alert_rows_by_tier: {'CRITICAL': 3, 'HIGH': 2, 'MEDIUM': 0, 'LOW': 26}
- telemetry_age_distribution: {'<=1h': 47, '1-6h': 2, '6-24h': 0, '1-7d': 8, '7-30d': 7, '>30d': 240}
- telemetry_never_seen_rows: 1118
- validator_failures: 1
- validator_failures_by_git_commit: {'b502cfc': 1}
- git_commits: ['b502cfc']
- scoring_errors: 0
- incomplete_input_rows: 0
- llm_used_rows: 0
- action_taken_values: ['LOG_ONLY_NO_DISPATCH']

## Outcomes (24h window + 1h settle; complete follow-up only)

- outcome_status_counts: {'PENDING': 1422}
- Outcome metrics WITHHELD: operational-only report. No precision, recall, lead time or alert-quality figures are reported at this stage.
