# Phase R5 Stage B Report — Deterministic Operational Ticket Renderer

**Date:** October 14, 2026
**Scope:** Stage B of the R5 reliability-recovery plan (deterministic operational tickets).
**Status:** GATE DELIVERABLE — awaiting approval. R5 is **NOT approved**.
**Provenance:** Built by the same AI agent that performed the Phase R5 work (self-audit
disclosure; not an independent audit).

**Explicit statements:**
- **No LLM-generated ticket is the production default.** The deterministic renderer
  (`src/tickets/renderer.py`) is the default ticket path. The fine-tuned Qwen model remains
  behind an experimental feature flag that defaults to OFF (`src/tickets/llm_bridge.py`).
  No claim is made that the LLM improves accuracy; that measurement is Stage E work.
- **No dispatch, alarm creation, or external notification occurred.** Rendering is string
  construction only. No ThingsBoard alarms were created, no technicians dispatched, no
  messages sent. Every ticket carries the footer line: "No automatic dispatch, alarm
  creation, or external notification was performed for this ticket."
- **No model was fine-tuned in Stage B. No historical evidence or earlier phase report was
  modified in Stage B.**

---

## 1. Exact schema definition (`src/tickets/schema.py`)

Standard-library dataclasses only (no new dependencies). Rejected inputs raise
`TicketValidationError` and are never rendered.

| Field | Type / constraint |
|---|---|
| `device_id` | non-empty string |
| `device_name` | non-empty string |
| `bank` | non-empty string |
| `prediction_timestamp_utc` | timezone-aware datetime (UTC) |
| `model_version` | non-empty string |
| `policy_version` | must equal `"ticket-policy-v1"` exactly |
| `device_state` | `ONLINE_OK` \| `ONLINE_POWER_LOSS` \| `STALE_DATA` \| `OFFLINE_LONG` |
| `measurements` | one `Measurement` per field; required: `ac_voltage`, `battery_voltage`, `time_since_mains_loss`; no duplicates |
| `Measurement` | `field` ∈ {ac_voltage, battery_voltage, time_since_mains_loss, heartbeat_gap}; `value` numeric or null; `unit` must match the field (`V` for voltages; `hours`/`h` for mains-loss duration; `seconds`/`s` for heartbeat gap); `observed_at` timezone-aware datetime; `freshness` ∈ `CURRENT` \| `STALE` \| `UNAVAILABLE` (`UNAVAILABLE` requires null value; otherwise value + `observed_at` required) |
| `calibrated_probability` | float in [0, 1]; **null required** for `STALE_DATA`/`OFFLINE_LONG`; **required non-null** for online states |
| `risk_tier` | `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` \| `CONNECTIVITY_ISSUE`; `CONNECTIVITY_ISSUE` **iff** device state is `STALE_DATA`/`OFFLINE_LONG` |
| `triggered_rules` | subset of the pipeline's canonical rule IDs: `RULE_AC_SURGE_OVER_250V`, `RULE_AC_UNDER_50V`, `RULE_MAINS_LOSS_RECENT` (per `src/shadow/run_shadow_remediation.py`); anything else rejected |
| `observed_facts` | non-empty list of non-empty strings |
| `possible_causes` | list of strings (hypotheses; kept separate from facts) |
| `approved_investigation_actions` | non-empty list from the bounded vocabulary (`inspect-gateway-power-and-wiring`, `check-sim-and-network-signal`, `verify-site-power-at-premises`, `monitor-ac-voltage-6h`, `schedule-battery-load-test-offpeak`, `inspect-distribution-board-and-breakers`, `verify-mains-contactor-position`, `check-inverter-and-ups-status`, `continue-automated-telemetry-polling`) |

---

## 2. Renderer contract (`src/tickets/renderer.py`)

- Every displayed number, unit, timestamp, device name, bank, probability, rule ID, and
  severity label comes directly from validated fields. The renderer invents no causes,
  hardware names, or diagnoses.
- Section vocabulary: **OBSERVED** (fresh telemetry / validated fields), **UNKNOWN**
  (unavailable, stale, unsupported), **POSSIBLE CAUSE** (hypothesis, never confirmed),
  **INVESTIGATION** (suggested check, never dispatch).
- Missing battery sensor renders exactly: *"Battery voltage unavailable: no configured
  battery-voltage sensor. Battery presence and condition are unknown."* — never "no battery
  installed". A companion UNKNOWN line states sensor absence must not be read as hardware
  absence.
- Stale measurements render as last-known values with their `observed_at` time and an
  explicit "last-known, not current" label.
- `STALE_DATA` / `OFFLINE_LONG` inputs receive connectivity tickets with **no
  power-outage probability** (`NOT EVALUATED`) and an explicit electrical-condition-unknown
  statement. Schema rejects any probability on these inputs before rendering.
- `ONLINE_POWER_LOSS` tickets state observed electrical facts and investigation actions and
  carry the disclaimer: *"Root cause is not confirmed by this ticket."*
- Traceability: `ticket_id = "TKT-" + sha256(canonical payload)[:12]` (stable across renders);
  full SHA-256 printed in the ticket footer.
- Probability formatting is centralized in `format_probability()` (percent, one decimal);
  Stage C will validate displayed percentages against this single function.

## 3. LLM boundary (`src/tickets/llm_bridge.py`)

- `EXPERIMENTAL_LLM_ENABLED = False` by default.
- Flag OFF → deterministic ticket returned unchanged; audit records `llm-disabled`.
- Flag ON → injected generation function produces **commentary only**; `validate_commentary()`
  rejects unknown `RULE_*` tokens, probability figures contradicting the canonical value,
  contradictory device identity, and dispatch/emergency orders. Invalid commentary is
  discarded; the deterministic ticket is retained verbatim. Raw output, validator result,
  and the keep/discard decision are recorded in `CommentaryAuditRecord`.

---

## 4. Example tickets (all device states)

Fixtures use realistic values from the existing telemetry/shadow schema
(`v2.0-remediated-lightgbm-clean`; rule/tier vocabularies as produced by the pipeline).

### 4.1 ONLINE_OK — BOI-BALLYBAZAR (TKT-DD999718E7B6)

```text
[SYSTEM DIAGNOSTIC TICKET - MEDIUM RISK]
Ticket ID: TKT-DD999718E7B6
Branch Site: BOI-BALLYBAZAR (BOI)
Device ID: 388f1380-31ad-11f1-9c6f-b71bb2771567
Device State: ONLINE_OK
Prediction Timestamp (UTC): 2026-10-13T12:00:00+00:00
Model Version: v2.0-remediated-lightgbm-clean
Policy Version: ticket-policy-v1

24-Hour Outage Probability: 4.5% (calibrated probability 0.0452)
Assigned Risk Tier: MEDIUM

Triggered Rules: RULE_MAINS_LOSS_RECENT

OBSERVED (directly supported by fresh telemetry or validated fields):
- AC line voltage within nominal band at last fresh observation.
- ac_voltage: 228.5 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).
- battery_voltage: 13.8 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).
- time_since_mains_loss: 0.0 hours, observed at 2026-10-13T11:55:00+00:00 (CURRENT).

POSSIBLE CAUSE (hypothesis only — not a confirmed failure):
- Early mains instability consistent with recent rule trigger.

INVESTIGATION (suggested checks — not an automatic dispatch):
- monitor-ac-voltage-6h

No automatic dispatch, alarm creation, or external notification was performed for this ticket.
Input Payload SHA-256: dd999718e7b623f8435576794914e9dead8677f9ae3eb7e9145d561af8150f25
```

### 4.2 ONLINE_POWER_LOSS — BOI-BHADRESWAR (TKT-6488D56CC5D6)

```text
[SYSTEM DIAGNOSTIC TICKET - CRITICAL RISK]
Ticket ID: TKT-6488D56CC5D6
Branch Site: BOI-BHADRESWAR (BOI)
Device ID: f0a8f490-d5a5-11f0-81e4-bd8a9470eef2
Device State: ONLINE_POWER_LOSS
Prediction Timestamp (UTC): 2026-10-13T12:00:00+00:00
Model Version: v2.0-remediated-lightgbm-clean
Policy Version: ticket-policy-v1

24-Hour Outage Probability: 16.4% (calibrated probability 0.1645)
Assigned Risk Tier: CRITICAL

Triggered Rules: RULE_AC_UNDER_50V

OBSERVED (directly supported by fresh telemetry or validated fields):
- AC bus at 0.0 V while gateway actively reporting; mains loss ongoing for 3.5 hours.
- ac_voltage: 0.0 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).
- battery_voltage: 11.9 V, observed at 2026-10-13T11:55:00+00:00 (CURRENT).
- time_since_mains_loss: 3.5 hours, observed at 2026-10-13T11:55:00+00:00 (CURRENT).

POSSIBLE CAUSE (hypothesis only — not a confirmed failure):
- Utility feeder interruption upstream of the site.
- Site-side breaker trip (unconfirmed).

Root cause is not confirmed by this ticket. Listed causes are hypotheses only (see POSSIBLE CAUSE); confirmation requires on-site investigation.

INVESTIGATION (suggested checks — not an automatic dispatch):
- inspect-distribution-board-and-breakers
- verify-mains-contactor-position

No automatic dispatch, alarm creation, or external notification was performed for this ticket.
Input Payload SHA-256: 6488d56cc5d6d8b4ff9c35f794904c7daaf7a8293f6ddf28ae2703759dc18dcd
```

### 4.3 STALE_DATA — BOI-CHIMTHANA (TKT-AA46E8E84F1C)

```text
[SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]
Ticket ID: TKT-AA46E8E84F1C
Branch Site: BOI-CHIMTHANA (BOI)
Device ID: dcf3f8c0-d021-11f0-9dd1-0f70161fd1f6
Device State: STALE_DATA
Prediction Timestamp (UTC): 2026-10-13T12:00:00+00:00
Model Version: v2.0-remediated-lightgbm-clean
Policy Version: ticket-policy-v1

24-Hour Outage Probability: NOT EVALUATED (connectivity ticket)
Assigned Status: CONNECTIVITY_ISSUE

Triggered Rules: None

OBSERVED (directly supported by fresh telemetry or validated fields):
- No fresh telemetry for over 60 minutes; gateway heartbeat stale.
- ac_voltage: last-known value 220.0 V, observed at 2026-10-12T09:00:00+00:00 (STALE — last-known, not current).
- Battery voltage unavailable: no configured battery-voltage sensor. Battery presence and condition are unknown.
- time_since_mains_loss: last-known value 168.0 hours, observed at 2026-10-12T09:00:00+00:00 (STALE — last-known, not current).

UNKNOWN (unavailable, stale, or unsupported):
- Electrical condition is unknown: no fresh telemetry is available during this communication blackout, so no statement about utility power, mains state, or battery condition can be made.
- Battery presence and condition are unknown: absence of a battery-voltage sensor must not be read as absence of battery hardware.
- Stale measurements (ac_voltage, time_since_mains_loss) are last-known values only and must not be described as current conditions.

POSSIBLE CAUSE (hypothesis only — not a confirmed failure):
- Cellular link degradation.
- Site power interruption affecting the gateway (unconfirmed).

INVESTIGATION (suggested checks — not an automatic dispatch):
- inspect-gateway-power-and-wiring
- check-sim-and-network-signal

No automatic dispatch, alarm creation, or external notification was performed for this ticket.
Input Payload SHA-256: aa46e8e84f1c51ccc9f33efe7fea4a29a6260980fd90448549fdf31630a291f6
```

### 4.4 OFFLINE_LONG — BOI-RANIHAT (TKT-873DE9A220D2)

```text
[SYSTEM CONNECTIVITY TICKET - INVESTIGATION REQUIRED]
Ticket ID: TKT-873DE9A220D2
Branch Site: BOI-RANIHAT (BOI)
Device ID: aa46cc10-9d3e-11f1-8f2a-2b7c9d44e5f1
Device State: OFFLINE_LONG
Prediction Timestamp (UTC): 2026-10-13T12:00:00+00:00
Model Version: v2.0-remediated-lightgbm-clean
Policy Version: ticket-policy-v1

24-Hour Outage Probability: NOT EVALUATED (connectivity ticket)
Assigned Status: CONNECTIVITY_ISSUE

Triggered Rules: None

OBSERVED (directly supported by fresh telemetry or validated fields):
- No communication for over 24 hours; gateway fully unreachable.
- ac_voltage: last-known value 220.0 V, observed at 2026-10-12T09:00:00+00:00 (STALE — last-known, not current).
- Battery voltage unavailable: no configured battery-voltage sensor. Battery presence and condition are unknown.
- time_since_mains_loss: last-known value 168.0 hours, observed at 2026-10-12T09:00:00+00:00 (STALE — last-known, not current).

UNKNOWN (unavailable, stale, or unsupported):
- Electrical condition is unknown: no fresh telemetry is available during this communication blackout, so no statement about utility power, mains state, or battery condition can be made.
- Battery presence and condition are unknown: absence of a battery-voltage sensor must not be read as absence of battery hardware.
- Stale measurements (ac_voltage, time_since_mains_loss) are last-known values only and must not be described as current conditions.

POSSIBLE CAUSE (hypothesis only — not a confirmed failure):
- Gateway de-energized or link severed (unconfirmed).

INVESTIGATION (suggested checks — not an automatic dispatch):
- inspect-gateway-power-and-wiring
- verify-site-power-at-premises

No automatic dispatch, alarm creation, or external notification was performed for this ticket.
Input Payload SHA-256: 873de9a220d2fe17d00b22942f3959a89ed4bae9b98eb155bf8f5abdbc3c7529
```

---

## 5. Test output

```
.venv\Scripts\python.exe -m pytest tests/test_tickets_schema.py tests/test_tickets_renderer.py -q
47 passed in 0.10s   (exit code 0)
```

Coverage of the required cases: valid ONLINE_OK ticket; valid ONLINE_POWER_LOSS ticket;
STALE_DATA and OFFLINE_LONG connectivity routing; missing-battery wording (online + stale);
stale-measurement wording (observed_at + "not current"); invalid probability (out-of-range,
negative, NaN, wrong type); invalid tier; unknown rule (including legacy validator rule
names); wrong device identity; wrong unit (A/kW/V mismatch); probability formatting
consistency (`0.0452`→`4.5%`, `0.0985`→`9.8%`, boundaries `0.0`/`1.0`); no probability on
connectivity tickets; stable ticket_id + payload hash (identical re-render; distinct inputs
diverge); LLM bridge (default OFF, invalid commentary discarded with ticket retained,
valid commentary appended under a marked header, audit record populated).

Regression check (unchanged prior suites):
```
.venv\Scripts\python.exe -m pytest tests/test_phase_r1_state_classification.py
  tests/test_phase_r2_label_leakage.py tests/test_phase_r3_predictor.py
  tests/test_phase_r4_consistency.py tests/test_phase_r5_claims.py -q
29 passed (exit code 0)
python verify_r5_evidence.py → exit code 0 (Stage A evidence unchanged)
```

## 6. Limitations

1. Tier–probability band consistency (e.g., CRITICAL ⟺ p ≥ 0.15 per the shadow pipeline) is
   **not** enforced in Stage B — only the CONNECTIVITY_ISSUE/state biconditional. Full tier
   consistency is Stage C work.
2. Probability display rounding (`16.45%` → `16.4%` via binary float formatting) is centralized
   but not yet validated against the canonical value at render time — Stage C work.
3. The investigation-action vocabulary is a first bounded set; allowed-action wording
   enforcement against operational policy review is Stage C work.
4. LLM commentary validation is heuristic (rule tokens, probability figures, identity,
   dispatch-order patterns) — intentionally narrow. It is **not** a semantic validator and
   must not be cited as one (cf. Stage A finding on validator blind spots).
5. Fixtures are realistic but synthetic; no production traffic was rendered.

---

*Stage B built no dispatch path, created no alarms, tuned no model, and modified no
historical evidence. The deterministic renderer is the default ticket path; Qwen remains
experimental (flag OFF).*
