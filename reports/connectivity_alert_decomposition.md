# Connectivity Alert Decomposition

Cutoff (UTC): 2026-10-14T12:00:00+00:00. Source: local live-shadow cache only (`data/live/`), no ThingsBoard access.
States are reconstructed per 15-minute slot with the live scorer's exact feature builder and
point-in-time view. Aggregates only; per-device detail stays in `data/live/analysis/` (gitignored).
No threshold, model, or alerting behaviour was changed.

Training fleet: 135 devices; analysis window 89.01 days after a 24 h warm-up (1716.63 device-weeks). Episodes starting in the warm-up are
left-censored and excluded from rates (136 excluded).

## 1. Currently alerted devices (CONNECTIVITY_ISSUE at cutoff, all 158 eligible)

| Classification | Devices | Rule |
|---|---|---|
| genuinely_stale_offline | 60 | no fresh data on any cached key or alarm; not online in the last 7 days |
| insufficient_evidence | 41 | no telemetry or alarm at all in the 90-day window |
| online_but_misclassified | 15 | fresh (<= 60 min) data on keys the state machine ignores, or an alarm in the last hour |
| intermittent | 12 | online at least once in the last 7 days, stale now |

By current state: {'OFFLINE_LONG/genuinely_stale_offline': 59, 'OFFLINE_LONG/intermittent': 10, 'STALE_DATA/genuinely_stale_offline': 1, 'STALE_DATA/insufficient_evidence': 41, 'STALE_DATA/intermittent': 2, 'STALE_DATA/online_but_misclassified': 15}

Non-state keys that were fresh on misclassified devices: {'fasLastHour': 15}

## 2. Episode decomposition (training fleet, in window)

| Metric | Value |
|---|---|
| raw_15min_alerts_in_window | 990227 |
| episodes_in_window | 832 |
| devices_with_episode | 98 |
| episodes_per_device_per_week | 0.485 |
| mean_raw_alerts_per_episode | 640.4 |
| duration_h_quantiles | {'0.1': 1.0, '0.25': 3.75, '0.5': 19.25, '0.75': 73.81, '0.9': 432.32, '0.99': 1613.92} |
| episodes_over_1h | 731 |
| episodes_over_6h | 603 |
| episodes_over_24h | 379 |
| episodes_ongoing_at_cutoff | 69 |
| episodes_recovered | 763 |
| episodes_reaching_offline_long | 386 |
| episodes_overlapping_power_outage_event | 244 |
| episodes_continuing_prior_within_2h | 146 |
| devices_never_reporting_in_window | 21 |
| share_of_raw_alerts_from_episodes_over_24h | 0.9727 |

Trigger: every episode is triggered by telemetry age > 60 min in the state machine (heartbeat keys count as telemetry); no other signal creates CONNECTIVITY_ISSUE.

## 3. Suppression policies (simulated; nothing enabled)

Incident = connectivity episode lasting > 60 min with onset inside the window. Alerts are counted per alert slot inside the window, including slots of episodes that began before it. Time to first alert is measured from STALE onset, which is itself 60 min after the last telemetry. In 15-minute slot space a minimum-duration rule and an N-consecutive-missed-cycles rule are the same rule family; both are shown as requested.

| Policy | Alerts | Alerts/device/week | Duplicate reduction | Mean time to first alert (min) | Missed incidents (of total) | Weekly alerts, 135 devices |
|---|---|---|---|---|---|---|
| P0 raw (every 15-min cycle) | 990227 | 576.844 | 0.0 | 0.0 | 0 / 731 | 77873.9 |
| P1a min duration 30 min, once per episode | 778 | 0.453 | 0.9992 | 30.0 | 0 / 731 | 61.2 |
| P1b min duration 60 min, once per episode | 731 | 0.426 | 0.9993 | 60.0 | 0 / 731 | 57.5 |
| P2a cooldown 6 h | 41708 | 24.296 | 0.9579 | 21.6 | 16 / 731 | 3280.0 |
| P2b cooldown 12 h | 21061 | 12.269 | 0.9787 | 85.2 | 41 / 731 | 1656.3 |
| P3a escalate after N=4 consecutive missed cycles | 762 | 0.444 | 0.9992 | 45.0 | 0 / 731 | 59.9 |
| P3b escalate after N=8 consecutive missed cycles | 681 | 0.397 | 0.9993 | 105.0 | 50 / 731 | 53.6 |
| P4 min 60 min + 12 h cooldown | 20916 | 12.184 | 0.9789 | 118.7 | 36 / 731 | 1644.9 |

## 4. Interpretation

Is this "many devices are offline" or "the policy is over-alerting"? **Both.** The two causes
separate cleanly.

1. **Over-alerting by repetition.** The raw stream issues one ticket per device per 15-minute cycle.
   - 97.3% of raw alert slots belong to episodes lasting more than 24 h.
   - The mean episode produces 640 raw tickets.
   - 832 episodes became about 990k raw alerts in 89 days, a 1,190× duplication factor.
2. **Many devices are offline for long periods.** Of the 128 devices alerted at the cutoff:
   - 60 are genuinely stale or offline: none of their cached keys has fresh data, and none was online in the last 7 days.
   - 41 have no telemetry or alarm at all in the 90-day window (21 of them in the training fleet).
     - They show as STALE_DATA, not OFFLINE_LONG, because the feature builder fills missing
       telemetry age with exactly 86,400 s. The OFFLINE_LONG test is `> 86400`, so it never fires
       for them.
     - This quirk is shared with training and is not changed here.
3. **Misclassification: 15 devices (all in the training fleet).**
   - Each sends a fresh `fasLastHour` within the last hour, so the gateway is communicating.
   - But none of the series the state machine reads is present: AC, battery, mains or heartbeat.
   - They sit permanently in STALE_DATA. They contribute raw alerts on every cycle and zero in-window episodes.
   - Under the current definition these are false connectivity alerts.
4. **Intermittent: 12 devices.** They were online at least once in the last 7 days and are stale now.
   This is the population where connectivity tickets carry real operational information.

Episode-level rate in the training fleet: **0.485 episodes per device per week**, about 65 per
week across 135 devices. It is computed on the live definition with point-in-time reconstruction
from the local cache, so it is not directly comparable to earlier figures computed on other data.

244 of 832 episodes (29%) overlap an R2-definition power-outage event within [onset − 2 h, end].
Those are power-driven connectivity loss, which should go to the power queue, not a second ticket.

## 5. Recommendation (not enabled)

**Recommended policy: P1b, one connectivity alert per episode once the device has been STALE for 60 minutes**
(about 2 h after its last telemetry).

| | P1b |
|---|---|
| Weekly alerts, 135 devices | about 57.5 (vs about 77,900 raw) |
| Duplicate reduction | 99.93% |
| Missed incidents (> 60 min) | 0 by construction: the alert fires exactly when an episode becomes an incident |
| Episodes < 60 min | not alerted (47 short episodes in the window) |

Tradeoffs, stated explicitly:
- **Latency.** The first alert comes 60 minutes after STALE onset, which is about 2 hours after the
  last telemetry. P1a (30 min) or P3a (N=4, 45 min) are faster for about 2–4 extra alerts a week and
  also catch 30–60 minute blips. P3a is the fallback if ops wants the faster response.
- **No reminders.** A long outage gets exactly one ticket; 379 episodes ran longer than 24 h.
  Ongoing episodes need a separate once-daily digest of open connectivity episodes, not repeat tickets.
- **Go-live backlog.** One-alert-per-episode policies produce no in-window alerts for episodes that
  were already open, because those started before the window. At go-live, about 128 currently open
  episodes would each raise one alert. These belong in an inventory review, not the ops alert stream:
  - 41 never-reporting devices,
  - 60 long-offline devices,
  - 15 misclassified devices.
- **Cooldown policies (P2, P4) are rejected.** They keep re-alerting on permanently offline devices
  (1,650–3,280 alerts a week) and still miss 16–41 incidents that start inside a cooldown.

Recommended follow-ups. Each needs approval and none is applied here:
- Treat `fasLastHour` (and other gateway-originated keys) as liveness evidence. That would remove the
  15 false connectivity alerts.
- Route devices with no telemetry in 7 days to an inventory or decommission list.
- Merge episodes separated by less than 2 h of recovery: 146 episodes resume within 2 h of the previous one.
  This merge was **not simulated**.
- Do not raise a separate connectivity ticket when a power-loss episode explains the silence.

No automatic escalation, dispatch, alarm creation or notification is enabled by this analysis.

## 6. CRITICAL / HIGH sanity check (cycle 2026-10-14T12:00:00+00:00)

Device identifiers are pseudonymised here (D1–D4). Full IDs, measurements and rendered tickets are in
`data/live/analysis/critical_high_diagnostics.md` (local, gitignored). Values come from the logged
prediction record and the local cache. Both live cycles ran on commit `b44a775`.

| ID | State | Tier | Calibrated p | Rules | Key telemetry (observed) | Driver | Plausible? |
|---|---|---|---|---|---|---|---|
| D1 | ONLINE_OK | CRITICAL | 0.1525 | none | no AC sensor (all-zero channel); no battery; mains UNKNOWN | `lagged_connectivity_drops_prev_7d` = 111 | **Weak.** No electrical evidence; the score comes from connectivity history. |
| D2 | ONLINE_OK | CRITICAL | 0.0015 | `RULE_AC_SURGE_OVER_250V` | AC **270.0 V** on every reading for 24 h; above 250 V for 90% of 90 days; battery 14.0 V | Surge rule forces CRITICAL; model p is 0.15% | **No.** A constant 270.0 V points to a stuck or miscalibrated sensor, not a surge. The rule fires permanently. |
| D3 | ONLINE_POWER_LOSS | CRITICAL | 0.4185 | `RULE_AC_UNDER_50V`, `RULE_MAINS_LOSS_RECENT` | AC 0.0 V, observed 4 min before cutoff; mains lost 0 h ago | Current mains loss | **Yes.** Mains is currently lost, and this device's historical 24 h outage rate is 54%. |
| D4 | ONLINE_OK | HIGH | 0.0619 | none | no AC sensor; no battery; mains UNKNOWN | 1 CRITICAL "integrated alarm system off" alarm in the last 6 h | **Weak.** The risk is alarm-driven, with no electrical evidence. |

Validator: D1 and D4 failed with "power loss claimed while mains is healthy". That is the old
template cause bug, fixed in `fe5df5f` after these cycles ran. D2 and D3 passed.

All four rows are `LOG_ONLY_NO_DISPATCH`, `llm_used=false`. No threshold or model was changed.
The D2 pattern (a constant over-voltage reading that permanently forces CRITICAL) is worth a
sensor-health check before any ops review of CRITICAL tickets.
